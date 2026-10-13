"""Project REST endpoint contract tests."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.projects import get_project_service, router
from app.models.common import Page
from app.projects.errors import ProjectAlreadyExistsError, ProjectNotFoundError
from tests.api_doubles import open_application
from tests.project_doubles import project

VALID_PROJECT = {
    "project_id": "sampleco-payment",
    "name": "Sample Company Payment Service",
    "description": "Payment orchestration",
    "repository": "git@example.invalid:sampleco/payment.git",
    "environment": "production",
    "tags": ["payments"],
}


def build_application(service: MagicMock) -> FastAPI:
    """Build an app whose project dependency is the given double."""
    application = open_application(router)

    async def override_service() -> MagicMock:
        return service

    application.dependency_overrides[get_project_service] = override_service
    return application


async def call(application: FastAPI, method: str, path: str, **kwargs: Any) -> Any:
    """Send one request to the project endpoints."""
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.asyncio
async def test_a_project_is_created_and_returned_without_internals() -> None:
    service = MagicMock()
    service.create_project = AsyncMock(return_value=project())
    application = build_application(service)

    response = await call(application, "POST", "/api/v1/projects", json=VALID_PROJECT)

    assert response.status_code == 201
    payload = response.json()
    assert payload["project_id"] == "sampleco-payment"
    assert payload["environment"] == "production"
    assert payload["tags"] == ["payments"]
    assert "_id" not in payload
    assert "revision" not in payload
    assert service.create_project.await_args.kwargs == {
        "project_id": "sampleco-payment",
        "name": "Sample Company Payment Service",
        "description": "Payment orchestration",
        "repository": "git@example.invalid:sampleco/payment.git",
        "environment": "production",
        "tags": ["payments"],
    }


@pytest.mark.asyncio
async def test_a_taken_project_identifier_reports_a_conflict() -> None:
    service = MagicMock()
    service.create_project = AsyncMock(side_effect=ProjectAlreadyExistsError("taken"))
    application = build_application(service)

    response = await call(application, "POST", "/api/v1/projects", json=VALID_PROJECT)

    assert response.status_code == 409
    assert response.json() == {"detail": "project already exists"}


@pytest.mark.asyncio
async def test_projects_are_listed_with_a_cursor() -> None:
    service = MagicMock()
    service.list_projects = AsyncMock(
        return_value=Page(items=(project(), project("sampleco-wallet")), next_cursor="next")
    )
    application = build_application(service)

    response = await call(application, "GET", "/api/v1/projects?limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert [item["project_id"] for item in payload["items"]] == [
        "sampleco-payment",
        "sampleco-wallet",
    ]
    assert payload["next_cursor"] == "next"
    assert service.list_projects.await_args.kwargs == {"limit": 2, "cursor": None}


@pytest.mark.asyncio
async def test_one_project_is_loaded_by_identifier() -> None:
    service = MagicMock()
    service.get_project = AsyncMock(return_value=project())
    application = build_application(service)

    response = await call(application, "GET", "/api/v1/projects/sampleco-payment")

    assert response.status_code == 200
    assert response.json()["name"] == "Sample Company Payment Service"


@pytest.mark.asyncio
async def test_an_unknown_project_returns_not_found() -> None:
    service = MagicMock()
    service.get_project = AsyncMock(side_effect=ProjectNotFoundError("absent"))
    application = build_application(service)

    response = await call(application, "GET", "/api/v1/projects/sampleco-wallet")

    assert response.status_code == 404
    assert response.json() == {"detail": "project was not found"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**VALID_PROJECT, "project_id": "not a valid identifier"},
        {**VALID_PROJECT, "project_id": ""},
        {**VALID_PROJECT, "name": ""},
        {**VALID_PROJECT, "description": ""},
        {**VALID_PROJECT, "repository": ""},
        {**VALID_PROJECT, "environment": "not valid"},
        {**VALID_PROJECT, "tags": ["a", "a"]},
        {**VALID_PROJECT, "unexpected": True},
    ],
)
async def test_malformed_project_requests_are_rejected(payload: dict[str, Any]) -> None:
    service = MagicMock()
    service.create_project = AsyncMock(return_value=project())
    application = build_application(service)

    response = await call(application, "POST", "/api/v1/projects", json=payload)

    assert response.status_code == 422
    service.create_project.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["limit=0", "limit=51"])
async def test_unbounded_project_listing_is_rejected(query: str) -> None:
    service = MagicMock()
    service.list_projects = AsyncMock(return_value=Page(items=(), next_cursor=None))
    application = build_application(service)

    response = await call(application, "GET", f"/api/v1/projects?{query}")

    assert response.status_code == 422
    service.list_projects.assert_not_awaited()
