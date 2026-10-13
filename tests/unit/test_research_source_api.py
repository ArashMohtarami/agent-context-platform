"""V3 Phase 2 REST source-registry authorization and transport tests."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_research_project_service, get_research_source_service
from app.api.routes.research_sources import router
from app.models.common import Page
from app.models.research import (
    ResearchAuthorization,
    ResearchProjectDocument,
    ResearchSourceDocument,
    SourceSnapshotDocument,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.research_source_service import RegisteredResearchSource
from tests.api_doubles import authenticated_application, open_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROJECT = "payments-api"
PRIVATE_PROJECT = "private-ledger"
RESEARCH_ID = "source-registry"
SOURCE_ID = "provider-docs"
WHEN = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)


def project(related: list[str] | None = None) -> ResearchProjectDocument:
    linked = [PROJECT] if related is None else related
    return ResearchProjectDocument(
        research_project_id=RESEARCH_ID,
        name="Source registry",
        question="Which source metadata can be registered safely?",
        type="technical_research",
        related_project_ids=linked,
        authorization=ResearchAuthorization(project_ids=linked, owner_key_id="owner-key"),
    )


def source(related: list[str] | None = None) -> ResearchSourceDocument:
    linked = [PROJECT] if related is None else related
    return ResearchSourceDocument(
        research_project_id=RESEARCH_ID,
        source_id=SOURCE_ID,
        source_type="url",
        uri="https://example.test/retries",
        canonical_uri="https://example.test/retries",
        title="Provider retry guide",
        publisher="Example",
        author="Documentation team",
        published_at=WHEN,
        retrieved_at=WHEN,
        source_class="official_documentation",
        trust_metadata={"origin": "caller-provided"},
        authorization=ResearchAuthorization(project_ids=linked, owner_key_id="writer-key"),
    )


def snapshot() -> SourceSnapshotDocument:
    return SourceSnapshotDocument(
        research_project_id=RESEARCH_ID,
        source_id=SOURCE_ID,
        snapshot_id="snapshot-1",
        retrieved_at=WHEN,
        content_hash="a" * 64,
        version_identifier="etag-1",
        metadata={"etag": "etag-1"},
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer-key"),
    )


class ProjectServiceDouble:
    def __init__(self, record: ResearchProjectDocument | None = None) -> None:
        self.get_project = AsyncMock(return_value=record or project())


class SourceServiceDouble:
    def __init__(self, record: ResearchSourceDocument | None = None) -> None:
        self.source = record or source()
        self.create_source = AsyncMock(
            return_value=RegisteredResearchSource(self.source, snapshot())
        )
        self.get_source = AsyncMock(return_value=self.source)
        self.list_sources = AsyncMock(
            return_value=Page(items=(self.source,), next_cursor="next-page")
        )


def application(sources: SourceServiceDouble, projects: ProjectServiceDouble) -> Any:
    app = open_application(router)
    app.dependency_overrides[get_research_source_service] = lambda: sources
    app.dependency_overrides[get_research_project_service] = lambda: projects
    return app


def secured_application(
    sources: SourceServiceDouble, projects: ProjectServiceDouble
) -> tuple[Any, str, FakeAuditRepository]:
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
    app.dependency_overrides[get_research_source_service] = lambda: sources
    app.dependency_overrides[get_research_project_service] = lambda: projects
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


def create_body() -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH_ID,
        "source_id": SOURCE_ID,
        "source_type": "url",
        "uri": "https://example.test/retries",
        "title": "Provider retry guide",
        "source_class": "official_documentation",
        "snapshot": {
            "snapshot_id": "snapshot-1",
            "retrieved_at": "2026-09-28T10:00:00Z",
            "content_hash": "a" * 64,
            "metadata": {"etag": "etag-1"},
        },
    }


@pytest.mark.asyncio
async def test_source_routes_register_read_and_page_metadata_without_an_acquisition_path() -> None:
    sources = SourceServiceDouble()
    app = application(sources, ProjectServiceDouble())

    created = await call(app, "POST", "/api/v3/research/sources", create_body())
    listed = await call(
        app,
        "GET",
        f"/api/v3/research/sources?research_project_id={RESEARCH_ID}&status=active&limit=5&cursor=before",
    )
    loaded = await call(app, "GET", f"/api/v3/research/sources/{SOURCE_ID}")

    assert created.status_code == 201
    assert created.json()["registered_snapshot"]["content_hash"] == "a" * 64
    assert loaded.status_code == 200
    assert loaded.json()["source_class"] == "official_documentation"
    assert listed.json()["next_cursor"] == "next-page"
    assert sources.list_sources.await_args.kwargs == {
        "research_project_id": RESEARCH_ID,
        "allowed_project_ids": None,
        "status": "active",
        "source_class": None,
        "limit": 5,
        "cursor": "before",
    }


@pytest.mark.asyncio
async def test_source_routes_refuse_private_project_links_and_audit_allowed_registration() -> None:
    private_sources = SourceServiceDouble(source([PRIVATE_PROJECT]))
    private_projects = ProjectServiceDouble(project([PRIVATE_PROJECT]))
    app, token, audit = secured_application(private_sources, private_projects)

    refused_create = await call(
        app, "POST", "/api/v3/research/sources", create_body(), headers=bearer(token)
    )
    refused_get = await call(
        app,
        "GET",
        f"/api/v3/research/sources/{SOURCE_ID}",
        headers=bearer(token),
    )

    assert refused_create.status_code == 403
    assert refused_get.status_code == 403
    assert PRIVATE_PROJECT not in refused_get.text
    private_sources.create_source.assert_not_awaited()
    assert any(item.action == "authorize" and item.outcome == "denied" for item in audit.records)

    allowed_sources = SourceServiceDouble()
    allowed_app, allowed_token, allowed_audit = secured_application(
        allowed_sources, ProjectServiceDouble()
    )
    allowed = await call(
        allowed_app,
        "POST",
        "/api/v3/research/sources",
        create_body(),
        headers=bearer(allowed_token),
    )

    assert allowed.status_code == 201
    assert allowed_sources.create_source.await_args.kwargs["owner_key_id"]
    assert [item.action for item in allowed_audit.records].count("research.write") == 1


@pytest.mark.asyncio
async def test_source_route_rejects_invalid_uri_before_service_or_network_access() -> None:
    sources = SourceServiceDouble()
    app = application(sources, ProjectServiceDouble())
    body = create_body()
    body["uri"] = "ftp://example.test/retries"

    response = await call(app, "POST", "/api/v3/research/sources", body)

    assert response.status_code == 422
    sources.create_source.assert_not_awaited()
