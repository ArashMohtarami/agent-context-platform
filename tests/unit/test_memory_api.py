"""Memory REST endpoint contract tests."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.memory import get_memory_service, get_retrieval_service, router
from app.api.security import current_principal
from app.db.errors import SearchUnavailableError
from app.embeddings.errors import EmbeddingUnavailableError
from app.memory.errors import MemoryNotFoundError, MemorySupersessionError
from app.memory.types import MemorySearchResult
from app.models.common import Page
from app.projects.errors import ProjectNotFoundError
from app.repositories.errors import ConcurrencyConflictError
from app.security.principal import Principal
from tests.api_doubles import open_application
from tests.memory_doubles import memory

PROJECT = "sampleco-payment"
CLAUDE = "claude-code-main"
VALID_MEMORY = {
    "type": "preference",
    "content": "Use strict typing in Python projects.",
    "importance": 0.8,
    "confidence": 0.95,
    "source": "user",
    "tags": ["python"],
}


def build_application(
    service: MagicMock | None = None,
    retrieval: MagicMock | None = None,
) -> FastAPI:
    """Build an app whose memory dependencies are the given doubles."""
    application = open_application(router)

    async def override_service() -> MagicMock:
        return service or MagicMock()

    async def override_retrieval() -> MagicMock:
        return retrieval or MagicMock()

    application.dependency_overrides[get_memory_service] = override_service
    application.dependency_overrides[get_retrieval_service] = override_retrieval
    return application


async def call(application: FastAPI, method: str, path: str, **kwargs: Any) -> Any:
    """Send one request to the memory endpoints."""
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.asyncio
async def test_a_global_memory_is_created_and_returned_without_its_vector() -> None:
    stored = memory(embedded=True)
    service = MagicMock()
    service.create_memory = AsyncMock(return_value=stored)
    application = build_application(service)

    response = await call(application, "POST", "/api/v1/memory", json=VALID_MEMORY)

    assert response.status_code == 201
    payload = response.json()
    assert payload["memory_id"] == str(stored.id)
    assert payload["scope"] == "global"
    assert payload["project_id"] is None
    assert payload["supersedes"] is None
    assert payload["superseded_by"] is None
    assert payload["revision"] == 1
    assert "embedding" not in payload
    assert service.create_memory.await_args.kwargs == {
        "memory_type": "preference",
        "content": "Use strict typing in Python projects.",
        "importance": 0.8,
        "confidence": 0.95,
        "source": "user",
        "project_id": None,
        "summary": None,
        "tags": ["python"],
        "status": "active",
        "external_id": None,
        "supersedes": None,
    }


@pytest.mark.asyncio
async def test_a_project_memory_names_its_project() -> None:
    stored = memory(PROJECT)
    service = MagicMock()
    service.create_memory = AsyncMock(return_value=stored)
    application = build_application(service)

    response = await call(
        application,
        "POST",
        "/api/v1/memory",
        json={**VALID_MEMORY, "project_id": PROJECT},
    )

    assert response.status_code == 201
    assert response.json()["scope"] == "project"
    assert response.json()["project_id"] == PROJECT
    assert service.create_memory.await_args.kwargs["project_id"] == PROJECT


@pytest.mark.asyncio
async def test_agent_memory_owner_is_derived_from_the_authenticated_client_not_the_body() -> None:
    stored = memory(scope="agent", client_id=CLAUDE)
    service = MagicMock()
    service.create_memory = AsyncMock(return_value=stored)
    application = build_application(service)
    agent = Principal(
        key_id="0123456789abcdef",
        name="Claude Code",
        scopes=("memory:read", "memory:write"),  # type: ignore[arg-type]
        client_id=CLAUDE,
        client_type="claude_code",
    )

    async def authenticated_client() -> Principal:
        return agent

    application.dependency_overrides[current_principal] = authenticated_client
    spoofed = await call(
        application,
        "POST",
        "/api/v1/memory",
        json={**VALID_MEMORY, "scope": "agent", "client_id": "codex-main"},
    )
    accepted = await call(
        application,
        "POST",
        "/api/v1/memory",
        json={**VALID_MEMORY, "scope": "agent"},
    )

    assert spoofed.status_code == 422
    assert accepted.status_code == 201
    assert service.create_memory.await_count == 1
    assert service.create_memory.await_args.kwargs["client_id"] == CLAUDE
    assert "client_id" not in VALID_MEMORY


@pytest.mark.asyncio
async def test_memory_for_an_unknown_project_is_refused() -> None:
    service = MagicMock()
    service.create_memory = AsyncMock(side_effect=ProjectNotFoundError("absent"))
    application = build_application(service)

    response = await call(
        application,
        "POST",
        "/api/v1/memory",
        json={**VALID_MEMORY, "project_id": PROJECT},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "project was not found"}


@pytest.mark.asyncio
async def test_a_supersession_reaches_the_service_as_an_identifier() -> None:
    replaced = ObjectId()
    stored = memory(supersedes=replaced)
    service = MagicMock()
    service.create_memory = AsyncMock(return_value=stored)
    application = build_application(service)

    response = await call(
        application,
        "POST",
        "/api/v1/memory",
        json={**VALID_MEMORY, "supersedes": str(replaced)},
    )

    assert response.status_code == 201
    assert response.json()["supersedes"] == str(replaced)
    assert service.create_memory.await_args.kwargs["supersedes"] == replaced


@pytest.mark.asyncio
async def test_superseding_an_unknown_memory_returns_not_found() -> None:
    service = MagicMock()
    service.create_memory = AsyncMock(side_effect=MemoryNotFoundError("absent"))
    application = build_application(service)

    response = await call(
        application,
        "POST",
        "/api/v1/memory",
        json={**VALID_MEMORY, "supersedes": str(ObjectId())},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "the memory to supersede was not found"}


@pytest.mark.asyncio
async def test_superseding_an_already_replaced_memory_reports_a_conflict() -> None:
    service = MagicMock()
    service.create_memory = AsyncMock(
        side_effect=MemorySupersessionError("that memory has already been superseded")
    )
    application = build_application(service)

    response = await call(
        application,
        "POST",
        "/api/v1/memory",
        json={**VALID_MEMORY, "supersedes": str(ObjectId())},
    )

    assert response.status_code == 409
    assert "already been superseded" in response.json()["detail"]


@pytest.mark.asyncio
async def test_an_unusable_embedding_model_stops_a_memory_write() -> None:
    service = MagicMock()
    service.create_memory = AsyncMock(side_effect=EmbeddingUnavailableError("not loaded"))
    application = build_application(service)

    response = await call(application, "POST", "/api/v1/memory", json=VALID_MEMORY)

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_one_memory_is_loaded_from_its_own_scope() -> None:
    stored = memory(PROJECT)
    service = MagicMock()
    service.get_memory = AsyncMock(return_value=stored)
    application = build_application(service)

    response = await call(
        application,
        "GET",
        f"/api/v1/memory/{stored.id}?project_id={PROJECT}",
    )

    assert response.status_code == 200
    assert service.get_memory.await_args.args == (stored.id,)
    assert service.get_memory.await_args.kwargs == {"project_id": PROJECT}


@pytest.mark.asyncio
async def test_a_global_memory_is_read_without_naming_a_project() -> None:
    stored = memory()
    service = MagicMock()
    service.get_memory = AsyncMock(return_value=stored)
    application = build_application(service)

    response = await call(application, "GET", f"/api/v1/memory/{stored.id}")

    assert response.status_code == 200
    assert service.get_memory.await_args.kwargs == {"project_id": None}


@pytest.mark.asyncio
async def test_memory_missing_from_this_scope_returns_not_found() -> None:
    service = MagicMock()
    service.get_memory = AsyncMock(side_effect=MemoryNotFoundError("absent"))
    application = build_application(service)

    response = await call(application, "GET", f"/api/v1/memory/{ObjectId()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "memory was not found"}


@pytest.mark.asyncio
async def test_a_malformed_memory_identifier_is_rejected() -> None:
    service = MagicMock()
    service.get_memory = AsyncMock(return_value=memory())
    application = build_application(service)

    response = await call(application, "GET", "/api/v1/memory/not-an-id")

    assert response.status_code == 422
    service.get_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_update_reaches_the_service_with_only_the_named_fields() -> None:
    stored = memory()
    service = MagicMock()
    service.update_memory = AsyncMock(return_value=stored)
    application = build_application(service)

    response = await call(
        application,
        "PATCH",
        f"/api/v1/memory/{stored.id}",
        json={"confidence": 0.4, "status": "archived", "expected_revision": 1},
    )

    assert response.status_code == 200
    assert service.update_memory.await_args.kwargs == {
        "project_id": None,
        "expected_revision": 1,
        "memory_type": None,
        "content": None,
        "summary": None,
        "importance": None,
        "confidence": 0.4,
        "tags": None,
        "status": "archived",
    }


@pytest.mark.asyncio
async def test_a_stale_revision_reports_a_conflict() -> None:
    service = MagicMock()
    service.update_memory = AsyncMock(side_effect=ConcurrencyConflictError("changed"))
    application = build_application(service)

    response = await call(
        application,
        "PATCH",
        f"/api/v1/memory/{ObjectId()}",
        json={"importance": 0.2, "expected_revision": 1},
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "REVISION_CONFLICT",
            "message": "Resource changed since it was retrieved.",
        }
    }


@pytest.mark.asyncio
async def test_updating_memory_in_another_scope_returns_not_found() -> None:
    service = MagicMock()
    service.update_memory = AsyncMock(side_effect=MemoryNotFoundError("absent"))
    application = build_application(service)

    response = await call(
        application,
        "PATCH",
        f"/api/v1/memory/{ObjectId()}?project_id=sampleco-wallet",
        json={"importance": 0.2, "expected_revision": 1},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_memory_is_listed_for_one_scope() -> None:
    service = MagicMock()
    service.list_memories = AsyncMock(return_value=Page(items=(memory(),), next_cursor="next"))
    application = build_application(service)

    response = await call(application, "GET", "/api/v1/memory?type=preference&limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "global"
    assert payload["project_id"] is None
    assert payload["next_cursor"] == "next"
    assert service.list_memories.await_args.kwargs["memory_type"] == "preference"
    assert service.list_memories.await_args.kwargs["status"] == "active"


@pytest.mark.asyncio
async def test_memory_search_returns_ranked_results() -> None:
    stored = memory()
    retrieval = MagicMock()
    retrieval.memory_search = AsyncMock(
        return_value=(MemorySearchResult(memory=stored, score=0.77, methods=("vector",)),)
    )
    application = build_application(retrieval=retrieval)

    response = await call(
        application,
        "POST",
        "/api/v1/memory/search",
        json={"query": "strict typing", "limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    result = payload["results"][0]
    assert result["source"] == "memory"
    assert result["score"] == 0.77
    assert result["methods"] == ["vector"]
    assert result["memory"]["memory_id"] == str(stored.id)
    assert "embedding" not in result["memory"]


@pytest.mark.asyncio
async def test_memory_search_passes_every_filter_to_retrieval() -> None:
    retrieval = MagicMock()
    retrieval.memory_search = AsyncMock(return_value=())
    application = build_application(retrieval=retrieval)

    response = await call(
        application,
        "POST",
        "/api/v1/memory/search",
        json={
            "query": "strict typing",
            "project_id": PROJECT,
            "scopes": ["global", "project"],
            "types": ["preference"],
            "tags": ["python"],
            "status": "archived",
            "minimum_importance": 0.4,
            "phrase": True,
            "limit": 9,
        },
    )

    assert response.status_code == 200
    assert retrieval.memory_search.await_args.kwargs == {
        "project_id": PROJECT,
        "scopes": ["global", "project"],
        "limit": 9,
        "phrase": True,
        "memory_types": ["preference"],
        "tags": ["python"],
        "status": "archived",
        "minimum_importance": 0.4,
    }


@pytest.mark.asyncio
async def test_unavailable_memory_search_returns_a_safe_503() -> None:
    retrieval = MagicMock()
    retrieval.memory_search = AsyncMock(side_effect=SearchUnavailableError("not queryable"))
    application = build_application(retrieval=retrieval)

    response = await call(
        application,
        "POST",
        "/api/v1/memory/search",
        json={"query": "strict typing"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "memory search is unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**VALID_MEMORY, "type": "not a valid identifier"},
        {**VALID_MEMORY, "content": ""},
        {**VALID_MEMORY, "importance": 1.5},
        {**VALID_MEMORY, "confidence": -0.1},
        {**VALID_MEMORY, "status": "deleted"},
        {**VALID_MEMORY, "tags": ["a", "a"]},
        {**VALID_MEMORY, "source": "not a valid identifier"},
        {**VALID_MEMORY, "project_id": "not a valid identifier"},
        {**VALID_MEMORY, "supersedes": "not-an-object-id"},
        {**VALID_MEMORY, "superseded_by": str(ObjectId())},
        {**VALID_MEMORY, "unexpected": True},
    ],
)
async def test_malformed_memory_creates_are_rejected(payload: dict[str, Any]) -> None:
    service = MagicMock()
    service.create_memory = AsyncMock(return_value=memory())
    application = build_application(service)

    response = await call(application, "POST", "/api/v1/memory", json=payload)

    assert response.status_code == 422
    service.create_memory.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"expected_revision": 1},
        # A valid mutation without its last-read revision must fail closed.
        {"importance": 0.5},
        {"importance": 2.0},
        {"confidence": 2.0},
        {"status": "deleted"},
        {"tags": ["a", "a"]},
        {"scope": "project"},
        {"project_id": "sampleco-wallet"},
        {"supersedes": str(ObjectId())},
        {"superseded_by": str(ObjectId())},
        {"expected_revision": 0, "importance": 0.5},
    ],
)
async def test_malformed_memory_updates_are_rejected(payload: dict[str, Any]) -> None:
    service = MagicMock()
    service.update_memory = AsyncMock(return_value=memory())
    application = build_application(service)

    response = await call(
        application,
        "PATCH",
        f"/api/v1/memory/{ObjectId()}",
        json=payload,
    )

    assert response.status_code == 422
    service.update_memory.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": ""},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": 51},
        {"query": "x", "scopes": []},
        {"query": "x", "scopes": ["global", "global"]},
        {"query": "x", "scopes": ["unknown"]},
        {"query": "x", "scopes": ["project"]},
        {"query": "x", "tags": ["a", "a"]},
        {"query": "x", "minimum_importance": 1.5},
        {"query": "x", "unexpected": True},
    ],
)
async def test_malformed_memory_searches_are_rejected(payload: dict[str, Any]) -> None:
    retrieval = MagicMock()
    retrieval.memory_search = AsyncMock(return_value=())
    application = build_application(retrieval=retrieval)

    response = await call(application, "POST", "/api/v1/memory/search", json=payload)

    assert response.status_code == 422
    retrieval.memory_search.assert_not_awaited()


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
