"""V3 Phase 1 REST contract, V1-link authorization, and audit tests."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_research_project_service
from app.api.routes.research import router
from app.models.common import Page
from app.models.research import ResearchAuthorization, ResearchProjectDocument
from app.research.errors import ResearchProjectNotFoundError
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from tests.api_doubles import authenticated_application, open_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROJECT = "payments-api"
PRIVATE_PROJECT = "private-ledger"
RESEARCH_ID = "mongot-tradeoffs"


def record(
    *, related: list[str] | None = None, revision: int = 1, status: str = "active"
) -> ResearchProjectDocument:
    related_ids = [PROJECT] if related is None else related
    return ResearchProjectDocument(
        research_project_id=RESEARCH_ID,
        name="Mongot tradeoffs",
        question="What are the operational tradeoffs of self-managed mongot?",
        type="technical_research",
        related_project_ids=related_ids,
        authorization=ResearchAuthorization(
            project_ids=related_ids,
            owner_key_id="0123456789abcdef",
        ),
        revision=revision,
        status=status,
    )


class ResearchServiceDouble:
    """Expose the API boundary without a database dependency."""

    def __init__(self) -> None:
        self.project = record()
        self.create_project = AsyncMock(return_value=self.project)
        self.get_project = AsyncMock(return_value=self.project)
        self.list_projects = AsyncMock(
            return_value=Page(items=(self.project,), next_cursor="next-page")
        )
        self.update_project = AsyncMock(return_value=record(revision=2, status="archived"))


def application(service: ResearchServiceDouble) -> Any:
    app = open_application(router)
    app.dependency_overrides[get_research_project_service] = lambda: service
    return app


def secured_application(service: ResearchServiceDouble) -> tuple[Any, str, FakeAuditRepository]:
    key, token = issued_key(
        scopes=("research:read", "research:write"),
        projects=(PROJECT,),
    )
    audit = FakeAuditRepository()
    app = authenticated_application(
        router,
        auth=AuthService(FakeApiKeyRepository(key)),
        audit=AuditService(audit),
    )
    app.dependency_overrides[get_research_project_service] = lambda: service
    return app, token, audit


async def call(
    app: Any,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, json=body, headers=headers)


@pytest.mark.asyncio
async def test_research_project_crud_routes_are_revision_guarded_and_expose_server_metadata() -> (
    None
):
    service = ResearchServiceDouble()
    app = application(service)
    body = {
        "research_project_id": RESEARCH_ID,
        "name": "Mongot tradeoffs",
        "question": "What are the operational tradeoffs of self-managed mongot?",
        "type": "technical_research",
        "related_project_ids": [PROJECT],
    }

    created = await call(app, "POST", "/api/v3/research/projects", body)
    listed = await call(app, "GET", "/api/v3/research/projects?status=active&limit=5&cursor=before")
    loaded = await call(app, "GET", f"/api/v3/research/projects/{RESEARCH_ID}")
    updated = await call(
        app,
        "PATCH",
        f"/api/v3/research/projects/{RESEARCH_ID}",
        {"expected_revision": 1, "status": "archived"},
    )

    assert created.status_code == 201
    assert listed.json()["next_cursor"] == "next-page"
    assert loaded.json()["question"] == body["question"]
    assert updated.json()["status"] == "archived"
    assert service.create_project.await_args.kwargs["owner_key_id"] == "0123456789abcdef"
    assert service.list_projects.await_args.kwargs == {
        "allowed_project_ids": None,
        "status": "active",
        "limit": 5,
        "cursor": "before",
    }
    assert service.update_project.await_args.kwargs == {
        "expected_revision": 1,
        "changes": {"status": "archived"},
    }


@pytest.mark.asyncio
async def test_research_project_routes_refuse_private_v1_links_before_create() -> None:
    service = ResearchServiceDouble()
    app, token, audit = secured_application(service)
    create = await call(
        app,
        "POST",
        "/api/v3/research/projects",
        {
            "research_project_id": "private-link",
            "name": "Private link",
            "question": "Can an unauthorized caller link private V1 data?",
            "type": "technical_research",
            "related_project_ids": [PRIVATE_PROJECT],
        },
        headers=bearer(token),
    )
    service.project = record(related=[PROJECT, PRIVATE_PROJECT])
    service.get_project.return_value = service.project
    loaded = await call(
        app,
        "GET",
        f"/api/v3/research/projects/{RESEARCH_ID}",
        headers=bearer(token),
    )

    assert create.status_code == 403
    service.create_project.assert_not_awaited()
    assert loaded.status_code == 403
    assert "private-ledger" not in loaded.text
    assert any(item.action == "authorize" and item.outcome == "denied" for item in audit.records)


@pytest.mark.asyncio
async def test_research_project_writes_audit_creation_and_update_with_its_identifier() -> None:
    service = ResearchServiceDouble()
    app, token, audit = secured_application(service)
    body = {
        "research_project_id": RESEARCH_ID,
        "name": "Mongot tradeoffs",
        "question": "What are the operational tradeoffs of self-managed mongot?",
        "type": "technical_research",
        "related_project_ids": [PROJECT],
    }

    created = await call(app, "POST", "/api/v3/research/projects", body, headers=bearer(token))
    updated = await call(
        app,
        "PATCH",
        f"/api/v3/research/projects/{RESEARCH_ID}",
        {"expected_revision": 1, "name": "Updated tradeoffs"},
        headers=bearer(token),
    )

    assert created.status_code == 201
    assert updated.status_code == 200
    writes = [item for item in audit.records if item.action == "research.write"]
    assert len(writes) == 2
    assert {item.research_project_id for item in writes} == {RESEARCH_ID}


@pytest.mark.asyncio
async def test_research_project_validation_and_missing_project_are_safe() -> None:
    service = ResearchServiceDouble()
    service.get_project.side_effect = ResearchProjectNotFoundError("research project was not found")
    app = application(service)

    invalid = await call(
        app,
        "POST",
        "/api/v3/research/projects",
        {
            "research_project_id": "invalid",
            "name": "Invalid",
            "question": "Does malformed metadata get rejected?",
            "type": "technical_research",
            "related_project_ids": [PROJECT, PROJECT],
            "status": "completed",
        },
    )
    missing = await call(app, "GET", "/api/v3/research/projects/missing")
    empty_update = await call(
        app,
        "PATCH",
        f"/api/v3/research/projects/{RESEARCH_ID}",
        {"expected_revision": 1},
    )

    assert invalid.status_code == 422
    assert missing.status_code == 404
    assert empty_update.status_code == 422
