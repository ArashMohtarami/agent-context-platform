"""Lexical search REST endpoint contract tests."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.skills import get_retrieval_service, router
from app.db.errors import SearchUnavailableError
from app.models.skill import SkillDocument
from app.models.skill_chunk import SkillChunkDocument
from app.skills.types import SkillChunkSearchResult
from tests.api_doubles import open_application

DIMENSIONS = 4


def search_result(score: float = 6.5) -> SkillChunkSearchResult:
    """Build one scored lexical result."""
    return SkillChunkSearchResult(
        skill=SkillDocument(
            skill_id="security-baseline",
            name="Security Baseline",
            version="1.0.0",
            description="Authentication and authorization practices",
            category="security",
            tags=["keycloak", "oauth2"],
            trust_level="trusted",
            source_path="skills/security/baseline.md",
            content_hash="a" * 64,
        ),
        chunk=SkillChunkDocument(
            skill_id="security-baseline",
            skill_version="1.0.0",
            chunk_id="chunk-0000",
            content="Keycloak issues OAuth2 tokens with the wallet:read scope.",
            section="Rules",
            chunk_index=0,
            tokens=9,
            embedding=[0.5] * DIMENSIONS,
            embedding_model="stub-embedding-model",
            embedding_dimensions=DIMENSIONS,
            embedding_generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        score=score,
        method="lexical",
    )


def build_application(service: MagicMock, *, embeddings: object = None) -> FastAPI:
    """Build an app whose retrieval dependency is the given double."""
    application = open_application(router)
    application.state.embeddings = embeddings

    async def override_service() -> MagicMock:
        return service

    application.dependency_overrides[get_retrieval_service] = override_service
    return application


async def post_search(application: FastAPI, payload: dict[str, Any]) -> Any:
    """Send one lexical search request."""
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/skills/search/lexical", json=payload)


@pytest.mark.asyncio
async def test_results_expose_relevance_scores_and_source_metadata() -> None:
    service = MagicMock()
    service.lexical_search = AsyncMock(return_value=(search_result(),))
    application = build_application(service)

    response = await post_search(application, {"query": "wallet:read", "limit": 5})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "wallet:read"
    assert payload["phrase"] is False
    result = payload["results"][0]
    assert result["source"] == "skill_chunk"
    assert result["method"] == "lexical"
    assert result["score"] == 6.5
    assert result["skill"]["skill_id"] == "security-baseline"
    assert "wallet:read" in result["chunk"]["content"]
    assert "embedding" not in result["chunk"]
    assert "_id" not in result["skill"]


@pytest.mark.asyncio
async def test_lexical_search_does_not_require_an_embedding_model() -> None:
    service = MagicMock()
    service.lexical_search = AsyncMock(return_value=(search_result(),))
    application = build_application(service, embeddings=None)

    response = await post_search(application, {"query": "Keycloak"})

    assert response.status_code == 200
    assert len(response.json()["results"]) == 1


@pytest.mark.asyncio
async def test_every_supported_filter_reaches_the_service() -> None:
    service = MagicMock()
    service.lexical_search = AsyncMock(return_value=())
    application = build_application(service)

    response = await post_search(
        application,
        {
            "query": "OAuth2",
            "phrase": True,
            "skill_id": "security-baseline",
            "skill_version": "1.0.0",
            "category": "security",
            "tags": ["keycloak"],
            "trust_level": "trusted",
            "active_only": False,
            "limit": 9,
        },
    )

    assert response.status_code == 200
    assert service.lexical_search.await_args.kwargs == {
        "limit": 9,
        "phrase": True,
        "skill_id": "security-baseline",
        "skill_version": "1.0.0",
        "category": "security",
        "tags": ["keycloak"],
        "trust_level": "trusted",
        "active_only": False,
    }


@pytest.mark.asyncio
async def test_a_query_matching_nothing_returns_an_empty_result_set() -> None:
    service = MagicMock()
    service.lexical_search = AsyncMock(return_value=())
    application = build_application(service)

    response = await post_search(application, {"query": "Kubernetes"})

    assert response.status_code == 200
    assert response.json()["results"] == []


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
        {"query": "x", "phrase": "maybe"},
        {},
    ],
)
async def test_malformed_requests_are_rejected(payload: dict[str, Any]) -> None:
    service = MagicMock()
    service.lexical_search = AsyncMock(return_value=())
    application = build_application(service)

    response = await post_search(application, payload)

    assert response.status_code == 422
    service.lexical_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_search_infrastructure_returns_a_safe_503() -> None:
    service = MagicMock()
    service.lexical_search = AsyncMock(side_effect=SearchUnavailableError("index is not queryable"))
    application = build_application(service)

    response = await post_search(application, {"query": "Keycloak"})

    assert response.status_code == 503
    assert response.json() == {"detail": "semantic search is unavailable"}
