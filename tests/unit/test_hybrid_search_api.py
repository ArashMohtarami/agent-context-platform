"""Hybrid search REST endpoint contract tests."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.skills import get_retrieval_service, router
from app.db.errors import SearchUnavailableError
from app.retrieval.context_builder import ContextItem, RetrievalContext
from app.retrieval.ranking import DEFAULT_WEIGHTS
from app.retrieval.types import RetrievalDiagnostics, ScoreBreakdown
from tests.api_doubles import open_application

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def context(
    *,
    items: tuple[ContextItem, ...] | None = None,
    project_id: str | None = None,
    token_count: int = 120,
) -> RetrievalContext:
    """Build one budgeted retrieval context."""
    return RetrievalContext(
        query="payment idempotency",
        project_id=project_id,
        items=context_items() if items is None else items,
        token_count=token_count,
        token_budget=4_000,
        diagnostics=RetrievalDiagnostics(
            methods=("vector", "lexical"),
            weights=DEFAULT_WEIGHTS.as_mapping(),
            vector_matches=2,
            lexical_matches=1,
            merged=2,
            filtered_out=1,
            duplicates_removed=1,
            ranked=1,
            token_budget=4_000,
            dropped_for_budget=1,
        ),
    )


def context_items() -> tuple[ContextItem, ...]:
    """Build one compact context item with a full score breakdown."""
    return (
        ContextItem(
            key="skill_chunk:fastapi-production:1.0.0:chunk-0000",
            source="active_skill",
            title="FastAPI Production: Rules",
            content="Payment requests use idempotency keys.",
            score=0.8775,
            tokens=120,
            metadata={
                "skill_id": "fastapi-production",
                "skill_version": "1.0.0",
                "chunk_id": "chunk-0000",
                "section": "Rules",
            },
            explanation=ScoreBreakdown(
                vector=0.9,
                lexical=1.0,
                importance=0.5,
                recency=1.0,
                source_priority=0.95,
                final=0.8775,
                methods=("vector", "lexical"),
                raw_vector=0.9,
                raw_lexical=8.0,
            ),
        ),
    )


def build_application(service: MagicMock) -> FastAPI:
    """Build an app whose retrieval dependency is the given double."""
    application = open_application(router)

    async def override_service() -> MagicMock:
        return service

    application.dependency_overrides[get_retrieval_service] = override_service
    return application


async def post_search(application: FastAPI, payload: dict[str, Any]) -> Any:
    """Send one hybrid search request."""
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/skills/search/hybrid", json=payload)


@pytest.mark.asyncio
async def test_results_are_compact_and_carry_their_source() -> None:
    service = MagicMock()
    service.hybrid_search = AsyncMock(return_value=context())
    application = build_application(service)

    response = await post_search(application, {"query": "payment idempotency"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "payment idempotency"
    assert payload["token_count"] == 120
    assert payload["token_budget"] == 4_000
    result = payload["results"][0]
    assert result["source"] == "active_skill"
    assert result["title"] == "FastAPI Production: Rules"
    assert result["score"] == 0.8775
    assert result["tokens"] == 120
    assert result["metadata"]["skill_id"] == "fastapi-production"


@pytest.mark.asyncio
async def test_retrieval_metadata_stays_internal_unless_it_is_asked_for() -> None:
    service = MagicMock()
    service.hybrid_search = AsyncMock(return_value=context())
    application = build_application(service)

    response = await post_search(application, {"query": "payment idempotency"})

    payload = response.json()
    # The keys stay present so the response shape never varies, but carry
    # nothing: explaining a ranking is opt-in.
    assert payload["diagnostics"] is None
    assert payload["results"][0]["explanation"] is None


@pytest.mark.asyncio
async def test_an_explained_request_shows_why_each_result_ranked() -> None:
    service = MagicMock()
    service.hybrid_search = AsyncMock(return_value=context())
    application = build_application(service)

    response = await post_search(application, {"query": "payment idempotency", "explain": True})

    payload = response.json()
    explanation = payload["results"][0]["explanation"]
    assert explanation["vector"] == 0.9
    assert explanation["lexical"] == 1.0
    assert explanation["source_priority"] == 0.95
    assert explanation["methods"] == ["vector", "lexical"]
    diagnostics = payload["diagnostics"]
    assert diagnostics["weights"]["vector"] == 0.45
    assert diagnostics["duplicates_removed"] == 1
    assert diagnostics["dropped_for_budget"] == 1
    assert diagnostics["filtered_out"] == 1


@pytest.mark.asyncio
async def test_every_supported_filter_reaches_the_service() -> None:
    service = MagicMock()
    service.hybrid_search = AsyncMock(return_value=context(items=(), token_count=0))
    application = build_application(service)

    response = await post_search(
        application,
        {
            "query": "OAuth2",
            "project_id": "sampleco-payment",
            "skill_id": "security-baseline",
            "skill_version": "1.0.0",
            "category": "security",
            "tags": ["keycloak"],
            "trust_level": "trusted",
            "active_only": False,
            "minimum_importance": 0.25,
            "token_budget": 1_500,
            "limit": 9,
        },
    )

    assert response.status_code == 200
    assert service.hybrid_search.await_args.kwargs == {
        "limit": 9,
        "token_budget": 1_500,
        "project_id": "sampleco-payment",
        "skill_id": "security-baseline",
        "skill_version": "1.0.0",
        "category": "security",
        "tags": ["keycloak"],
        "trust_level": "trusted",
        "active_only": False,
        "minimum_importance": 0.25,
    }


@pytest.mark.asyncio
async def test_a_project_scoped_request_reports_the_project_it_answered_for() -> None:
    service = MagicMock()
    service.hybrid_search = AsyncMock(return_value=context(project_id="sampleco-payment"))
    application = build_application(service)

    response = await post_search(
        application,
        {"query": "payment idempotency", "project_id": "sampleco-payment"},
    )

    assert response.json()["project_id"] == "sampleco-payment"


@pytest.mark.asyncio
async def test_a_query_matching_nothing_returns_an_empty_context() -> None:
    service = MagicMock()
    service.hybrid_search = AsyncMock(return_value=context(items=(), token_count=0))
    application = build_application(service)

    response = await post_search(application, {"query": "Kubernetes"})

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert response.json()["token_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "x" * 501},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": 51},
        {"query": "x", "tags": ["a", "a"]},
        {"query": "x", "unexpected": True},
        {"query": "x", "skill_version": "1.0.0"},
        {"query": "x", "trust_level": "unknown"},
        {"query": "x", "minimum_importance": 1.5},
        {"query": "x", "minimum_importance": -0.1},
        {"query": "x", "token_budget": 10},
        {"query": "x", "token_budget": 0},
        {"query": "x", "project_id": "not a valid identifier"},
        {"query": "x", "explain": "maybe"},
        {},
    ],
)
async def test_malformed_requests_are_rejected(payload: dict[str, Any]) -> None:
    service = MagicMock()
    service.hybrid_search = AsyncMock(return_value=context())
    application = build_application(service)

    response = await post_search(application, payload)

    assert response.status_code == 422
    service.hybrid_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_search_infrastructure_returns_a_safe_503() -> None:
    service = MagicMock()
    service.hybrid_search = AsyncMock(side_effect=SearchUnavailableError("index is not queryable"))
    application = build_application(service)

    response = await post_search(application, {"query": "payment idempotency"})

    assert response.status_code == 503
    assert response.json() == {"detail": "semantic search is unavailable"}
