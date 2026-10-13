"""Vector search REST endpoint contract tests."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.skills import get_retrieval_service, router
from app.core.config import Settings
from app.db.errors import SearchUnavailableError
from app.embeddings.errors import EmbeddingUnavailableError
from app.models.skill import SkillDocument
from app.models.skill_chunk import SkillChunkDocument
from app.skills.types import SkillChunkSearchResult
from tests.api_doubles import open_application

DIMENSIONS = 4


def search_result(score: float = 0.87) -> SkillChunkSearchResult:
    """Build one scored retrieval result."""
    return SkillChunkSearchResult(
        skill=SkillDocument(
            skill_id="fastapi-production",
            name="FastAPI Production",
            version="1.0.0",
            description="Production API patterns",
            category="backend",
            tags=["fastapi", "python"],
            trust_level="trusted",
            source_path="skills/fastapi/production.md",
            content_hash="a" * 64,
        ),
        chunk=SkillChunkDocument(
            skill_id="fastapi-production",
            skill_version="1.0.0",
            chunk_id="chunk-0000",
            content="Payment requests use idempotency keys.",
            section="Rules",
            chunk_index=0,
            tokens=6,
            embedding=[0.5] * DIMENSIONS,
            embedding_model="stub-embedding-model",
            embedding_dimensions=DIMENSIONS,
            embedding_generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        score=score,
        method="vector",
    )


def build_application(service: MagicMock) -> FastAPI:
    """Build an app whose retrieval dependency is the given double."""
    application = open_application(router)
    application.state.embeddings = MagicMock(
        model="stub-embedding-model",
        dimensions=DIMENSIONS,
    )

    async def override_service() -> MagicMock:
        return service

    application.dependency_overrides[get_retrieval_service] = override_service
    return application


async def post_search(application: FastAPI, payload: dict[str, Any]) -> Any:
    """Send one vector search request."""
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/skills/search/vector", json=payload)


@pytest.mark.asyncio
async def test_results_expose_scores_metadata_and_no_vectors() -> None:
    service = MagicMock()
    service.vector_search = AsyncMock(return_value=(search_result(),))
    application = build_application(service)

    response = await post_search(
        application,
        {"query": "how do we prevent duplicate payment requests?", "limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["embedding_model"] == "stub-embedding-model"
    assert payload["embedding_dimensions"] == DIMENSIONS
    result = payload["results"][0]
    assert result["source"] == "skill_chunk"
    assert result["score"] == 0.87
    assert result["skill"]["skill_id"] == "fastapi-production"
    assert result["skill"]["tags"] == ["fastapi", "python"]
    assert result["chunk"]["section"] == "Rules"
    assert "embedding" not in result["chunk"]
    assert "_id" not in result["skill"]


@pytest.mark.asyncio
async def test_every_supported_filter_reaches_the_service() -> None:
    service = MagicMock()
    service.vector_search = AsyncMock(return_value=())
    application = build_application(service)

    response = await post_search(
        application,
        {
            "query": "idempotency",
            "skill_id": "fastapi-production",
            "skill_version": "1.0.0",
            "category": "backend",
            "tags": ["python"],
            "trust_level": "trusted",
            "active_only": False,
            "limit": 7,
        },
    )

    assert response.status_code == 200
    assert service.vector_search.await_args.kwargs == {
        "limit": 7,
        "skill_id": "fastapi-production",
        "skill_version": "1.0.0",
        "category": "backend",
        "tags": ["python"],
        "trust_level": "trusted",
        "active_only": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": 51},
        {"query": "x", "tags": ["a", "a"]},
        {"query": "x", "unexpected": True},
        {"query": "x", "skill_version": "1.0.0"},
        {"query": "x", "trust_level": "unknown"},
    ],
)
async def test_malformed_requests_are_rejected(payload: dict[str, Any]) -> None:
    service = MagicMock()
    service.vector_search = AsyncMock(return_value=())
    application = build_application(service)

    response = await post_search(application, payload)

    assert response.status_code == 422
    service.vector_search.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [SearchUnavailableError("index is not queryable"), EmbeddingUnavailableError("no model")],
)
async def test_unavailable_infrastructure_returns_a_safe_503(error: Exception) -> None:
    service = MagicMock()
    service.vector_search = AsyncMock(side_effect=error)
    application = build_application(service)

    response = await post_search(application, {"query": "idempotency"})

    assert response.status_code == 503
    assert response.json() == {"detail": "semantic search is unavailable"}


@pytest.mark.asyncio
async def test_disabled_embeddings_make_semantic_search_unavailable(settings: Settings) -> None:
    application = open_application(router)
    application.state.embeddings = None
    application.state.settings = settings
    application.state.mongodb = MagicMock()

    response = await post_search(application, {"query": "idempotency"})

    assert response.status_code == 503
    assert response.json() == {"detail": "semantic search is unavailable"}
