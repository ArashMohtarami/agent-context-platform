"""Project-context REST endpoint contract tests."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.context import get_context_service, get_retrieval_service, router
from app.db.errors import SearchUnavailableError
from app.embeddings.errors import EmbeddingUnavailableError
from app.models.common import Page
from app.projects.errors import ContextNotFoundError, ProjectNotFoundError
from app.projects.types import ContextSearchResult
from app.repositories.errors import ConcurrencyConflictError
from tests.api_doubles import open_application
from tests.project_doubles import context

PROJECT = "sampleco-payment"
VALID_CONTEXT = {
    "project_id": PROJECT,
    "type": "architecture_decision",
    "title": "Payment idempotency strategy",
    "content": "Payment requests carry a UUID4 idempotency key.",
    "importance": 0.95,
    "source": "user",
    "tags": ["payments"],
}


def build_application(
    service: MagicMock | None = None,
    retrieval: MagicMock | None = None,
) -> FastAPI:
    """Build an app whose context dependencies are the given doubles."""
    application = open_application(router)

    async def override_service() -> MagicMock:
        return service or MagicMock()

    async def override_retrieval() -> MagicMock:
        return retrieval or MagicMock()

    application.dependency_overrides[get_context_service] = override_service
    application.dependency_overrides[get_retrieval_service] = override_retrieval
    return application


async def call(application: FastAPI, method: str, path: str, **kwargs: Any) -> Any:
    """Send one request to the context endpoints."""
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.asyncio
async def test_context_is_created_and_returned_without_its_vector() -> None:
    stored = context(PROJECT, embedded=True)
    service = MagicMock()
    service.create_context = AsyncMock(return_value=stored)
    application = build_application(service)

    response = await call(application, "POST", "/api/v1/context", json=VALID_CONTEXT)

    assert response.status_code == 201
    payload = response.json()
    assert payload["context_id"] == str(stored.id)
    assert payload["project_id"] == PROJECT
    assert payload["type"] == "architecture_decision"
    assert payload["importance"] == 0.95
    assert payload["revision"] == 1
    assert "embedding" not in payload
    assert "_id" not in payload
    assert service.create_context.await_args.args == (PROJECT,)
    assert service.create_context.await_args.kwargs == {
        "context_type": "architecture_decision",
        "title": "Payment idempotency strategy",
        "content": "Payment requests carry a UUID4 idempotency key.",
        "importance": 0.95,
        "source": "user",
        "tags": ["payments"],
        "status": "active",
        "external_id": None,
    }


@pytest.mark.asyncio
async def test_context_for_an_unknown_project_is_refused() -> None:
    service = MagicMock()
    service.create_context = AsyncMock(side_effect=ProjectNotFoundError("absent"))
    application = build_application(service)

    response = await call(application, "POST", "/api/v1/context", json=VALID_CONTEXT)

    assert response.status_code == 404
    assert response.json() == {"detail": "project was not found"}


@pytest.mark.asyncio
async def test_context_write_rejects_caller_supplied_actor_provenance() -> None:
    """The actor is derived by the gateway/request context, never the body."""
    service = MagicMock()
    service.create_context = AsyncMock(return_value=context(PROJECT))
    application = build_application(service)

    response = await call(
        application,
        "POST",
        "/api/v1/context",
        json={
            **VALID_CONTEXT,
            "actor": {
                "actor_type": "agent",
                "client_id": "claude-code-main",
                "client_type": "claude_code",
            },
        },
    )

    assert response.status_code == 422
    service.create_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unusable_embedding_model_stops_a_context_write() -> None:
    service = MagicMock()
    service.create_context = AsyncMock(side_effect=EmbeddingUnavailableError("not loaded"))
    application = build_application(service)

    response = await call(application, "POST", "/api/v1/context", json=VALID_CONTEXT)

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_one_context_item_is_loaded_from_inside_its_project() -> None:
    stored = context(PROJECT)
    service = MagicMock()
    service.get_context = AsyncMock(return_value=stored)
    application = build_application(service)

    response = await call(
        application,
        "GET",
        f"/api/v1/context/{stored.id}?project_id={PROJECT}",
    )

    assert response.status_code == 200
    assert response.json()["context_id"] == str(stored.id)
    assert service.get_context.await_args.args == (PROJECT, stored.id)


@pytest.mark.asyncio
async def test_reading_context_requires_naming_its_project() -> None:
    service = MagicMock()
    service.get_context = AsyncMock(return_value=context(PROJECT))
    application = build_application(service)

    response = await call(application, "GET", f"/api/v1/context/{ObjectId()}")

    assert response.status_code == 422
    service.get_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_missing_from_this_project_returns_not_found() -> None:
    service = MagicMock()
    service.get_context = AsyncMock(side_effect=ContextNotFoundError("absent"))
    application = build_application(service)

    response = await call(
        application,
        "GET",
        f"/api/v1/context/{ObjectId()}?project_id={PROJECT}",
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "context was not found"}


@pytest.mark.asyncio
async def test_a_malformed_context_identifier_is_rejected() -> None:
    service = MagicMock()
    service.get_context = AsyncMock(return_value=context(PROJECT))
    application = build_application(service)

    response = await call(application, "GET", f"/api/v1/context/not-an-id?project_id={PROJECT}")

    assert response.status_code == 422
    service.get_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_update_reaches_the_service_with_only_the_named_fields() -> None:
    stored = context(PROJECT)
    service = MagicMock()
    service.update_context = AsyncMock(return_value=stored)
    application = build_application(service)

    response = await call(
        application,
        "PATCH",
        f"/api/v1/context/{stored.id}?project_id={PROJECT}",
        json={"importance": 0.2, "status": "archived", "expected_revision": 1},
    )

    assert response.status_code == 200
    assert service.update_context.await_args.args == (PROJECT, stored.id)
    assert service.update_context.await_args.kwargs == {
        "expected_revision": 1,
        "context_type": None,
        "title": None,
        "content": None,
        "importance": 0.2,
        "tags": None,
        "status": "archived",
    }


@pytest.mark.asyncio
async def test_a_stale_revision_reports_a_conflict() -> None:
    service = MagicMock()
    service.update_context = AsyncMock(side_effect=ConcurrencyConflictError("changed"))
    application = build_application(service)

    response = await call(
        application,
        "PATCH",
        f"/api/v1/context/{ObjectId()}?project_id={PROJECT}",
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
async def test_updating_context_in_another_project_returns_not_found() -> None:
    service = MagicMock()
    service.update_context = AsyncMock(side_effect=ContextNotFoundError("absent"))
    application = build_application(service)

    response = await call(
        application,
        "PATCH",
        f"/api/v1/context/{ObjectId()}?project_id=sampleco-wallet",
        json={"importance": 0.2, "expected_revision": 1},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_context_is_listed_for_one_project() -> None:
    service = MagicMock()
    service.list_context = AsyncMock(
        return_value=Page(items=(context(PROJECT),), next_cursor="next")
    )
    application = build_application(service)

    response = await call(
        application,
        "GET",
        f"/api/v1/context?project_id={PROJECT}&type=architecture_decision&limit=5",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == PROJECT
    assert payload["next_cursor"] == "next"
    assert service.list_context.await_args.kwargs["context_type"] == "architecture_decision"
    assert service.list_context.await_args.kwargs["status"] == "active"


@pytest.mark.asyncio
async def test_context_search_returns_ranked_results_for_one_project() -> None:
    stored = context(PROJECT)
    retrieval = MagicMock()
    retrieval.context_search = AsyncMock(
        return_value=(
            ContextSearchResult(context=stored, score=0.82, methods=("vector", "lexical")),
        )
    )
    application = build_application(retrieval=retrieval)

    response = await call(
        application,
        "POST",
        "/api/v1/context/search",
        json={"project_id": PROJECT, "query": "idempotency", "limit": 5},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == PROJECT
    result = payload["results"][0]
    assert result["source"] == "project_context"
    assert result["score"] == 0.82
    assert result["methods"] == ["vector", "lexical"]
    assert result["context"]["context_id"] == str(stored.id)
    assert "embedding" not in result["context"]


@pytest.mark.asyncio
async def test_context_search_passes_every_filter_to_retrieval() -> None:
    retrieval = MagicMock()
    retrieval.context_search = AsyncMock(return_value=())
    application = build_application(retrieval=retrieval)

    response = await call(
        application,
        "POST",
        "/api/v1/context/search",
        json={
            "project_id": PROJECT,
            "query": "idempotency",
            "types": ["architecture_decision", "business_rule"],
            "tags": ["payments"],
            "status": "archived",
            "minimum_importance": 0.4,
            "phrase": True,
            "limit": 9,
        },
    )

    assert response.status_code == 200
    assert retrieval.context_search.await_args.kwargs == {
        "project_id": PROJECT,
        "limit": 9,
        "phrase": True,
        "context_types": ["architecture_decision", "business_rule"],
        "tags": ["payments"],
        "status": "archived",
        "minimum_importance": 0.4,
    }


@pytest.mark.asyncio
async def test_unavailable_context_search_returns_a_safe_503() -> None:
    retrieval = MagicMock()
    retrieval.context_search = AsyncMock(side_effect=SearchUnavailableError("not queryable"))
    application = build_application(retrieval=retrieval)

    response = await call(
        application,
        "POST",
        "/api/v1/context/search",
        json={"project_id": PROJECT, "query": "idempotency"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "context search is unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**VALID_CONTEXT, "type": "unknown_type"},
        {**VALID_CONTEXT, "title": ""},
        {**VALID_CONTEXT, "content": ""},
        {**VALID_CONTEXT, "importance": 1.5},
        {**VALID_CONTEXT, "importance": -0.1},
        {**VALID_CONTEXT, "status": "deleted"},
        {**VALID_CONTEXT, "tags": ["a", "a"]},
        {**VALID_CONTEXT, "source": "not a valid identifier"},
        {**VALID_CONTEXT, "project_id": "not a valid identifier"},
        {**VALID_CONTEXT, "unexpected": True},
    ],
)
async def test_malformed_context_creates_are_rejected(payload: dict[str, Any]) -> None:
    service = MagicMock()
    service.create_context = AsyncMock(return_value=context(PROJECT))
    application = build_application(service)

    response = await call(application, "POST", "/api/v1/context", json=payload)

    assert response.status_code == 422
    service.create_context.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"expected_revision": 1},
        # A valid mutation without its last-read revision must fail closed.
        {"importance": 0.5},
        {"importance": 2.0},
        {"status": "deleted"},
        {"tags": ["a", "a"]},
        {"project_id": "sampleco-wallet"},
        {"external_id": "moved"},
        {"expected_revision": 0, "importance": 0.5},
    ],
)
async def test_malformed_context_updates_are_rejected(payload: dict[str, Any]) -> None:
    service = MagicMock()
    service.update_context = AsyncMock(return_value=context(PROJECT))
    application = build_application(service)

    response = await call(
        application,
        "PATCH",
        f"/api/v1/context/{ObjectId()}?project_id={PROJECT}",
        json=payload,
    )

    assert response.status_code == 422
    service.update_context.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"query": "idempotency"},
        {"project_id": PROJECT},
        {"project_id": PROJECT, "query": ""},
        {"project_id": PROJECT, "query": "x", "limit": 0},
        {"project_id": PROJECT, "query": "x", "limit": 51},
        {"project_id": PROJECT, "query": "x", "types": ["unknown"]},
        {"project_id": PROJECT, "query": "x", "tags": ["a", "a"]},
        {"project_id": PROJECT, "query": "x", "minimum_importance": 1.5},
        {"project_id": PROJECT, "query": "x", "unexpected": True},
    ],
)
async def test_malformed_context_searches_are_rejected(payload: dict[str, Any]) -> None:
    retrieval = MagicMock()
    retrieval.context_search = AsyncMock(return_value=())
    application = build_application(retrieval=retrieval)

    response = await call(application, "POST", "/api/v1/context/search", json=payload)

    assert response.status_code == 422
    retrieval.context_search.assert_not_awaited()
