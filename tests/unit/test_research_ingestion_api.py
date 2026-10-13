"""V3 Phase 3 REST ingestion authorization and transport tests."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import (
    get_research_ingestion_service,
    get_research_project_service,
    get_research_source_service,
)
from app.api.routes.research_sources import router
from app.models.research import (
    IngestionJobDocument,
    ResearchAuthorization,
    ResearchProjectDocument,
    ResearchSourceDocument,
    SourceSnapshotDocument,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.research_ingestion_service import IngestionResult
from tests.api_doubles import authenticated_application, open_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROJECT = "payments-api"
PRIVATE = "private-ledger"
RESEARCH = "payment-evidence"
SOURCE = "manual-retry-guide"
WHEN = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


def project(links: list[str] | None = None) -> ResearchProjectDocument:
    related = [PROJECT] if links is None else links
    return ResearchProjectDocument(
        research_project_id=RESEARCH,
        name="Payment evidence",
        question="What do retries require?",
        type="technical_research",
        related_project_ids=related,
        authorization=ResearchAuthorization(project_ids=related, owner_key_id="writer"),
    )


def source(links: list[str] | None = None) -> ResearchSourceDocument:
    related = [PROJECT] if links is None else links
    return ResearchSourceDocument(
        research_project_id=RESEARCH,
        source_id=SOURCE,
        source_type="manual",
        uri="manual:retry-guide",
        canonical_uri="manual:retry-guide",
        title="Retry guide",
        source_class="user_provided",
        authorization=ResearchAuthorization(project_ids=related, owner_key_id="writer"),
    )


def result() -> IngestionResult:
    snapshot = SourceSnapshotDocument(
        research_project_id=RESEARCH,
        source_id=SOURCE,
        snapshot_id="snapshot-a",
        retrieved_at=WHEN,
        content_hash="a" * 64,
        version_identifier="sha256:" + "a" * 64,
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer"),
    )
    job = IngestionJobDocument(
        research_project_id=RESEARCH,
        job_id="ingest-a",
        source_id=SOURCE,
        snapshot_id=snapshot.snapshot_id,
        status="completed",
        started_at=WHEN,
        completed_at=WHEN,
        documents_created=1,
        chunks_created=2,
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer"),
    )
    return IngestionResult(job=job, snapshot=snapshot, idempotent=False)


class SourceService:
    def __init__(self, record: ResearchSourceDocument) -> None:
        self.get_source = AsyncMock(return_value=record)


class ProjectService:
    def __init__(self, record: ResearchProjectDocument) -> None:
        self.get_project = AsyncMock(return_value=record)


class IngestionService:
    def __init__(self) -> None:
        self.ingest = AsyncMock(return_value=result())
        self.get_job = AsyncMock(return_value=result().job)


def application(
    *,
    sources: SourceService,
    projects: ProjectService,
    ingestion: IngestionService,
) -> Any:
    app = open_application(router)
    app.dependency_overrides[get_research_source_service] = lambda: sources
    app.dependency_overrides[get_research_project_service] = lambda: projects
    app.dependency_overrides[get_research_ingestion_service] = lambda: ingestion
    return app


def secured_application(
    *,
    sources: SourceService,
    projects: ProjectService,
    ingestion: IngestionService,
) -> tuple[Any, str, FakeAuditRepository]:
    key, token = issued_key(scopes=("research:read", "research:write"), projects=(PROJECT,))
    audit = FakeAuditRepository()
    app = authenticated_application(
        router,
        auth=AuthService(FakeApiKeyRepository(key)),
        audit=AuditService(audit),
    )
    app.dependency_overrides[get_research_source_service] = lambda: sources
    app.dependency_overrides[get_research_project_service] = lambda: projects
    app.dependency_overrides[get_research_ingestion_service] = lambda: ingestion
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
async def test_ingest_route_forwards_only_explicit_text_after_source_authorization() -> None:
    ingestion = IngestionService()
    app = application(
        sources=SourceService(source()), projects=ProjectService(project()), ingestion=ingestion
    )

    response = await call(
        app,
        "POST",
        f"/api/v3/research/sources/{SOURCE}/ingest",
        {"content": "Use a stable retry key.", "content_type": "text/plain", "language": "en"},
    )
    status_response = await call(
        app,
        "GET",
        f"/api/v3/research/sources/{SOURCE}/ingestion-jobs/ingest-a",
    )

    assert response.status_code == 200
    assert response.json()["job"]["chunks_created"] == 2
    payload = ingestion.ingest.await_args.kwargs["payload"]
    assert payload.data == b"Use a stable retry key."
    assert payload.filename is None
    assert ingestion.ingest.await_args.kwargs["manually_provided"] is True
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_ingest_route_allows_only_registered_url_acquisition_after_authorization() -> None:
    url_source = ResearchSourceDocument(
        research_project_id=RESEARCH,
        source_id="public-retry-guide",
        source_type="url",
        uri="https://public.example/retries",
        canonical_uri="https://public.example/retries",
        title="Retry guide",
        source_class="official_documentation",
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer"),
    )
    ingestion = IngestionService()
    app = application(
        sources=SourceService(url_source), projects=ProjectService(project()), ingestion=ingestion
    )

    response = await call(
        app,
        "POST",
        "/api/v3/research/sources/public-retry-guide/ingest",
        {"fetch_url": True},
    )

    assert response.status_code == 200
    payload = ingestion.ingest.await_args.kwargs["payload"]
    assert payload.data == b""
    assert payload.acquire_url is True
    assert payload.content_type == "application/octet-stream"
    assert ingestion.ingest.await_args.kwargs["manually_provided"] is False


@pytest.mark.asyncio
async def test_ingest_route_rejects_a_url_fetch_request_that_carries_content() -> None:
    ingestion = IngestionService()
    app = application(
        sources=SourceService(source()), projects=ProjectService(project()), ingestion=ingestion
    )

    response = await call(
        app,
        "POST",
        f"/api/v3/research/sources/{SOURCE}/ingest",
        {
            "fetch_url": True,
            "content": "https://not-allowed.example",
            "url": "https://also-not-allowed.example",
        },
    )

    assert response.status_code == 422
    ingestion.ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_route_refuses_private_v1_links_and_invalid_base64_before_service() -> None:
    private_ingestion = IngestionService()
    app, token, audit = secured_application(
        sources=SourceService(source([PRIVATE])),
        projects=ProjectService(project([PRIVATE])),
        ingestion=private_ingestion,
    )
    forbidden = await call(
        app,
        "POST",
        f"/api/v3/research/sources/{SOURCE}/ingest",
        {"content": "hidden", "content_type": "text/plain"},
        headers=bearer(token),
    )
    assert forbidden.status_code == 403
    assert PRIVATE not in forbidden.text
    private_ingestion.ingest.assert_not_awaited()
    assert any(item.action == "authorize" and item.outcome == "denied" for item in audit.records)

    ingestion = IngestionService()
    invalid_app = application(
        sources=SourceService(source()), projects=ProjectService(project()), ingestion=ingestion
    )
    invalid = await call(
        invalid_app,
        "POST",
        f"/api/v3/research/sources/{SOURCE}/ingest",
        {"content_base64": "not base64!", "content_type": "application/pdf"},
    )
    assert invalid.status_code == 422
    ingestion.ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_route_allows_registered_repository_after_authorization() -> None:
    repository_source = ResearchSourceDocument(
        research_project_id=RESEARCH,
        source_id="payments-repository",
        source_type="git",
        uri="https://public.example/acme/payments.git",
        canonical_uri="https://public.example/acme/payments.git",
        title="Payments repository",
        source_class="source_code",
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer"),
    )
    ingestion = IngestionService()
    app = application(
        sources=SourceService(repository_source),
        projects=ProjectService(project()),
        ingestion=ingestion,
    )

    response = await call(
        app,
        "POST",
        "/api/v3/research/sources/payments-repository/ingest",
        {"fetch_repository": True},
    )

    assert response.status_code == 200
    payload = ingestion.ingest.await_args.kwargs["payload"]
    assert payload.data == b""
    assert payload.acquire_repository is True
    assert payload.acquire_url is False
    assert ingestion.ingest.await_args.kwargs["manually_provided"] is False


@pytest.mark.asyncio
async def test_ingest_route_rejects_mixed_repository_acquisition_before_service() -> None:
    ingestion = IngestionService()
    app = application(
        sources=SourceService(source()), projects=ProjectService(project()), ingestion=ingestion
    )

    response = await call(
        app,
        "POST",
        f"/api/v3/research/sources/{SOURCE}/ingest",
        {"fetch_repository": True, "content": "not allowed"},
    )

    assert response.status_code == 422
    ingestion.ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_route_returns_429_when_the_shared_ingestion_gate_is_saturated() -> None:
    from app.services.research_ingestion_service import ResearchIngestionCapacityError

    ingestion = IngestionService()
    ingestion.ingest = AsyncMock(side_effect=ResearchIngestionCapacityError("capacity exhausted"))
    app = application(
        sources=SourceService(source()), projects=ProjectService(project()), ingestion=ingestion
    )

    response = await call(
        app,
        "POST",
        f"/api/v3/research/sources/{SOURCE}/ingest",
        {"content": "bounded", "content_type": "text/plain", "language": "en"},
    )

    assert response.status_code == 429
    assert response.json()["detail"] == "capacity exhausted"
