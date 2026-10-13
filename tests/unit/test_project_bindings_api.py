"""REST contracts and authorization boundaries for V4 project resolution."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_project_binding_service
from app.api.routes.project_bindings import router
from app.models.agent import (
    ProjectBindingDocument,
    ProjectBindingType,
    ProjectResolutionMatch,
    ProjectResolutionStatus,
)
from app.models.common import Page
from app.services.auth_service import AuthService
from app.services.project_binding_service import ProjectResolution
from tests.api_doubles import authenticated_application, open_application
from tests.security_doubles import FakeApiKeyRepository, bearer, issued_key

PAYMENTS = "payment-service"
PRIVATE = "private-ledger"


def document() -> ProjectBindingDocument:
    return ProjectBindingDocument(
        binding_id="payment-remote",
        project_id=PAYMENTS,
        binding_type=ProjectBindingType.GIT_REMOTE,
        value="git@github.com:example/payment-service.git",
    )


def application(service: MagicMock) -> FastAPI:
    app = open_application(router)

    async def override_service() -> MagicMock:
        return service

    app.dependency_overrides[get_project_binding_service] = override_service
    return app


async def call(app: FastAPI, method: str, path: str, **kwargs: Any) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.asyncio
async def test_binding_routes_create_list_update_and_delete_at_reviewed_revision() -> None:
    service = MagicMock()
    service.create_binding = AsyncMock(return_value=document())
    service.list_bindings = AsyncMock(return_value=Page(items=(document(),), next_cursor="next"))
    service.get_binding = AsyncMock(return_value=document())
    service.update_binding = AsyncMock(return_value=document().model_copy(update={"revision": 2}))
    service.delete_binding = AsyncMock()
    app = application(service)

    created = await call(
        app,
        "POST",
        "/api/v4/project-bindings",
        json={
            "project_id": PAYMENTS,
            "binding_type": "git_remote",
            "value": "git@github.com:example/payment-service.git",
        },
    )
    listed = await call(app, "GET", "/api/v4/project-bindings?limit=5")
    updated = await call(
        app,
        "PATCH",
        "/api/v4/project-bindings/payment-remote",
        json={"expected_revision": 1, "value": "https://github.com/example/payment-service.git"},
    )
    deleted = await call(
        app,
        "DELETE",
        "/api/v4/project-bindings/payment-remote?expected_revision=2",
    )

    assert created.status_code == 201
    assert created.json()["value"] == "github.com/example/payment-service"
    assert listed.status_code == 200
    assert listed.json()["next_cursor"] == "next"
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert deleted.status_code == 204
    assert service.list_bindings.await_args.kwargs == {
        "project_id": None,
        "allowed_project_ids": None,
        "binding_type": None,
        "limit": 5,
        "cursor": None,
    }
    assert service.update_binding.await_args.kwargs["expected_revision"] == 1
    assert service.delete_binding.await_args.kwargs == {
        "expected_revision": 2,
    }


@pytest.mark.asyncio
async def test_resolution_response_returns_ambiguous_candidates_without_selecting_one() -> None:
    service = MagicMock()
    service.resolve_project = AsyncMock(
        return_value=ProjectResolution(
            status=ProjectResolutionStatus.AMBIGUOUS,
            project_id=None,
            confidence=0.0,
            matched_by=ProjectResolutionMatch.REPOSITORY_NAME,
            candidates=(),
        )
    )
    app = application(service)

    response = await call(
        app,
        "POST",
        "/api/v4/project-bindings/resolve",
        json={"repository_name": "shared-service"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ambiguous",
        "project_id": None,
        "confidence": 0.0,
        "matched_by": "repository_name",
        "candidates": [],
    }
    assert service.resolve_project.await_args.kwargs["allowed_project_ids"] is None


@pytest.mark.asyncio
async def test_resolution_accepts_workspace_alias_and_delegates_to_the_v4_workspace_hint() -> None:
    service = MagicMock()
    service.resolve_project = AsyncMock(
        return_value=ProjectResolution(
            status=ProjectResolutionStatus.RESOLVED,
            project_id=PAYMENTS,
            confidence=1.0,
            matched_by=ProjectResolutionMatch.WORKSPACE_ALIAS,
        )
    )
    app = application(service)

    response = await call(
        app,
        "POST",
        "/api/v4/project-bindings/resolve",
        json={"workspace_alias": "payment-service"},
    )

    assert response.status_code == 200
    assert response.json()["project_id"] == PAYMENTS
    assert service.resolve_project.await_args.kwargs == {
        "project_id": None,
        "git_remote": None,
        "repository_url": None,
        "repository_name": None,
        "workspace_hint": "payment-service",
        "allowed_project_ids": None,
    }


@pytest.mark.asyncio
async def test_explicit_private_project_is_forbidden_before_resolution() -> None:
    record, token = issued_key(scopes=("projects:read",), projects=(PAYMENTS,))
    service = MagicMock()
    service.resolve_project = AsyncMock()
    app = authenticated_application(
        router,
        auth=AuthService(FakeApiKeyRepository(record)),
    )

    async def override_service() -> MagicMock:
        return service

    app.dependency_overrides[get_project_binding_service] = override_service

    response = await call(
        app,
        "POST",
        "/api/v4/project-bindings/resolve",
        headers=bearer(token),
        json={"project_id": PRIVATE},
    )

    assert response.status_code == 403
    service.resolve_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolution_requires_one_explicit_identifier() -> None:
    service = MagicMock()
    service.resolve_project = AsyncMock()
    app = application(service)

    response = await call(app, "POST", "/api/v4/project-bindings/resolve", json={})

    assert response.status_code == 422
    service.resolve_project.assert_not_awaited()
