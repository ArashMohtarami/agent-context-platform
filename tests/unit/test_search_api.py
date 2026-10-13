"""The unified search endpoint's contract."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.search import get_search_service, router
from app.db.errors import SearchUnavailableError
from app.embeddings.errors import EmbeddingUnavailableError
from app.retrieval.context_builder import ContextItem, RetrievalContext
from app.retrieval.ranking import DEFAULT_WEIGHTS
from app.retrieval.types import RetrievalDiagnostics, ScoreBreakdown
from app.search.types import KnowledgeSearchResult, RetrievalTrace
from tests.api_doubles import open_application

NOW = datetime(2026, 9, 1, tzinfo=UTC)
PROJECT = "sampleco-payment"


def items() -> tuple[ContextItem, ...]:
    """Build one result from each retrievable source."""
    return (
        ContextItem(
            key=f"project_context:{PROJECT}:6720a1b2c3d4e5f60718293a",
            source="project_context",
            title="Payment idempotency strategy",
            content="Payment requests carry a UUID4 idempotency key.",
            score=0.9312,
            tokens=80,
            metadata={"context_id": "6720a1b2c3d4e5f60718293a", "project_id": PROJECT},
            explanation=breakdown(0.93, 1.0),
        ),
        ContextItem(
            key="skill_chunk:fastapi-production:1.0.0:chunk-0000",
            source="active_skill",
            title="FastAPI Production: Rules",
            content="Payment requests use idempotency keys.",
            score=0.8775,
            tokens=120,
            metadata={"skill_id": "fastapi-production", "skill_version": "1.0.0"},
            explanation=breakdown(0.90, 0.8),
        ),
    )


def breakdown(vector: float, lexical: float) -> ScoreBreakdown:
    """Build one score breakdown."""
    return ScoreBreakdown(
        vector=vector,
        lexical=lexical,
        importance=0.95,
        recency=1.0,
        source_priority=1.0,
        final=0.9312,
        methods=("vector", "lexical"),
        raw_vector=vector,
        raw_lexical=8.0,
    )


def result(
    *,
    project_id: str | None = PROJECT,
    skipped: tuple[str, ...] = (),
) -> KnowledgeSearchResult:
    """Build one finished unified search."""
    return KnowledgeSearchResult(
        context=RetrievalContext(
            query="payment idempotency",
            project_id=project_id,
            items=items(),
            token_count=200,
            token_budget=4_000,
            diagnostics=RetrievalDiagnostics(
                methods=("vector", "lexical"),
                weights=DEFAULT_WEIGHTS.as_mapping(),
                vector_matches=3,
                lexical_matches=2,
                merged=4,
                filtered_out=1,
                duplicates_removed=1,
                ranked=2,
                token_budget=4_000,
                dropped_for_budget=0,
            ),
        ),
        trace=RetrievalTrace(
            query_id="0123456789abcdef0123456789abcdef",
            retrieval_method="hybrid",
            requested_sources=("project_context", "active_skill", "memory"),
            searched_sources=("project_context", "active_skill", "memory"),
            skipped_sources=skipped,  # type: ignore[arg-type]
            latency_ms=18.25,
        ),
    )


def build_application(service: MagicMock) -> FastAPI:
    """Build an app whose search dependency is the given double."""
    application = open_application(router)

    async def override_service() -> MagicMock:
        return service

    application.dependency_overrides[get_search_service] = override_service
    return application


async def post_search(application: FastAPI, payload: dict[str, Any]) -> Any:
    """Send one unified search request."""
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/search", json=payload)


def searching(outcome: KnowledgeSearchResult | Exception) -> MagicMock:
    """Build a search service double that answers with one outcome."""
    service = MagicMock()
    if isinstance(outcome, Exception):
        service.search_knowledge = AsyncMock(side_effect=outcome)
    else:
        service.search_knowledge = AsyncMock(return_value=outcome)
    return service


@pytest.mark.asyncio
async def test_one_request_returns_compact_context_from_every_source() -> None:
    application = build_application(searching(result()))

    response = await post_search(application, {"query": "payment idempotency"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "payment idempotency"
    assert payload["project_id"] == PROJECT
    assert payload["token_count"] == 200
    assert payload["token_budget"] == 4_000
    assert [item["source"] for item in payload["results"]] == [
        "project_context",
        "active_skill",
    ]
    first = payload["results"][0]
    assert first["title"] == "Payment idempotency strategy"
    assert first["score"] == 0.9312
    assert first["tokens"] == 80
    assert first["metadata"]["project_id"] == PROJECT
    assert first["key"].startswith("project_context:")


@pytest.mark.asyncio
async def test_retrieval_metadata_is_always_returned() -> None:
    application = build_application(searching(result(skipped=("conversation",))))

    response = await post_search(application, {"query": "payment idempotency"})

    # A unified answer is only debuggable if the caller can see which sources
    # contributed, so this block is not behind `explain`.
    retrieval = response.json()["retrieval"]
    assert retrieval["query_id"] == "0123456789abcdef0123456789abcdef"
    assert retrieval["retrieval_method"] == "hybrid"
    assert retrieval["searched_sources"] == ["project_context", "active_skill", "memory"]
    assert retrieval["skipped_sources"] == ["conversation"]
    assert retrieval["latency_ms"] == 18.25
    assert retrieval["diagnostics"]["merged"] == 4
    assert retrieval["diagnostics"]["filtered_out"] == 1
    assert retrieval["diagnostics"]["duplicates_removed"] == 1


@pytest.mark.asyncio
async def test_per_result_scores_are_explained_only_when_asked_for() -> None:
    application = build_application(searching(result()))

    quiet = await post_search(application, {"query": "payment idempotency"})
    explained = await post_search(
        application,
        {"query": "payment idempotency", "explain": True},
    )

    assert quiet.json()["results"][0]["explanation"] is None
    explanation = explained.json()["results"][0]["explanation"]
    assert explanation["vector"] == 0.93
    assert explanation["lexical"] == 1.0
    assert explanation["source_priority"] == 1.0
    assert explanation["methods"] == ["vector", "lexical"]


@pytest.mark.asyncio
async def test_every_filter_reaches_the_service() -> None:
    service = searching(result())
    application = build_application(service)

    await post_search(
        application,
        {
            "query": "payment idempotency",
            "project_id": PROJECT,
            "sources": ["project_context", "memory"],
            "skill_id": "fastapi-production",
            "skill_version": "1.0.0",
            "category": "backend",
            "context_types": ["architecture_decision"],
            "memory_types": ["preference"],
            "memory_scopes": ["global", "project"],
            "tags": ["payments"],
            "trust_level": "trusted",
            "minimum_importance": 0.5,
            "active_only": False,
            "phrase": True,
            "token_budget": 2_000,
            "limit": 25,
        },
    )

    call = service.search_knowledge.await_args
    assert call.args == ("payment idempotency",)
    assert call.kwargs["project_id"] == PROJECT
    assert call.kwargs["sources"] == ["project_context", "memory"]
    assert call.kwargs["skill_id"] == "fastapi-production"
    assert call.kwargs["skill_version"] == "1.0.0"
    assert call.kwargs["category"] == "backend"
    assert call.kwargs["context_types"] == ["architecture_decision"]
    assert call.kwargs["memory_types"] == ["preference"]
    assert call.kwargs["memory_scopes"] == ["global", "project"]
    assert call.kwargs["tags"] == ["payments"]
    assert call.kwargs["trust_level"] == "trusted"
    assert call.kwargs["minimum_importance"] == 0.5
    assert call.kwargs["active_only"] is False
    assert call.kwargs["phrase"] is True
    assert call.kwargs["token_budget"] == 2_000
    assert call.kwargs["limit"] == 25


@pytest.mark.asyncio
async def test_an_unscoped_request_reports_that_it_answered_for_no_project() -> None:
    application = build_application(searching(result(project_id=None)))

    response = await post_search(application, {"query": "payment idempotency"})

    payload = response.json()
    assert "project_id" in payload
    assert payload["project_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": "a" * 501},
        {"query": "payment", "limit": 0},
        {"query": "payment", "limit": 51},
        {"query": "payment", "token_budget": 10},
        {"query": "payment", "minimum_importance": 1.5},
        {"query": "payment", "minimum_importance": -0.1},
        {"query": "payment", "sources": []},
        {"query": "payment", "sources": ["memory", "memory"]},
        {"query": "payment", "sources": ["conversations"]},
        {"query": "payment", "tags": ["a", "a"]},
        {"query": "payment", "context_types": ["not_a_type"]},
        {"query": "payment", "memory_scopes": []},
        {"query": "payment", "memory_scopes": ["project"]},
        {"query": "payment", "memory_scopes": ["global", "global"]},
        {"query": "payment", "skill_version": "1.0.0"},
        {"query": "payment", "skill_id": "x", "skill_version": "not-semver"},
        {"query": "payment", "trust_level": "unknown"},
        {"query": "payment", "project_id": "not a project id"},
        {"query": "payment", "unexpected": True},
    ],
)
async def test_malformed_requests_are_rejected(payload: dict[str, Any]) -> None:
    service = searching(result())
    application = build_application(service)

    response = await post_search(application, payload)

    assert response.status_code == 422
    service.search_knowledge.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_memory_can_be_asked_for_when_a_project_is_named() -> None:
    application = build_application(searching(result()))

    response = await post_search(
        application,
        {"query": "payment", "project_id": PROJECT, "memory_scopes": ["project"]},
    )

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        SearchUnavailableError("index is not queryable"),
        EmbeddingUnavailableError("the model is not loaded"),
    ],
)
async def test_unavailable_infrastructure_is_reported_without_internals(
    error: Exception,
) -> None:
    application = build_application(searching(error))

    response = await post_search(application, {"query": "payment idempotency"})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail == "knowledge search is unavailable"
    assert "index" not in detail
    assert "model" not in detail
