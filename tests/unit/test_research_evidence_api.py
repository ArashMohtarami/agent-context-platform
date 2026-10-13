"""V3 Phase 7 REST evidence contracts and authorization tests."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_research_evidence_service, get_research_project_service
from app.api.routes.research_evidence import router
from app.models.research import (
    CitationLocator,
    CitationRelation,
    ResearchAuthorization,
    ResearchCitationDocument,
    ResearchProjectDocument,
    SourceClass,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.research_evidence_service import EvidencePacket
from tests.api_doubles import authenticated_application, open_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROJECT = "payments-api"
PRIVATE_PROJECT = "private-ledger"
RESEARCH = "retry-evidence"
WHEN = datetime(2026, 9, 30, tzinfo=UTC)


def project(related: list[str] | None = None) -> ResearchProjectDocument:
    project_ids = [PROJECT] if related is None else related
    return ResearchProjectDocument(
        research_project_id=RESEARCH,
        name="Retry evidence",
        question="What constrains safe payment retries?",
        type="technical_research",
        related_project_ids=project_ids,
        authorization=ResearchAuthorization(project_ids=project_ids, owner_key_id="owner-key"),
    )


def citation() -> ResearchCitationDocument:
    return ResearchCitationDocument(
        research_project_id=RESEARCH,
        claim_id="retry-key",
        citation_id="provider-guide",
        source_id="provider-docs",
        snapshot_id="provider-v1",
        chunk_id="retry-chunk",
        source_class=SourceClass.OFFICIAL_DOCUMENTATION,
        locator=CitationLocator(url="https://example.test/retries", page=2, line_start=10),
        evidence_excerpt="Use an idempotency key for retryable requests.",
        relation=CitationRelation.SUPPORTS,
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer-key"),
    )


def packet() -> EvidencePacket:
    return EvidencePacket(
        question="What constrains retries?",
        research_project_id=RESEARCH,
        relevant_context=(),
        supporting_evidence=(),
        contradicting_evidence=(),
        qualifying_evidence=(),
        background_evidence=(),
        source_metadata=(),
        citation_locators=(),
        conflicts=(),
        uncertainties=("No persisted citation matched the retrieved context.",),
        token_budget=8_000,
        tokens_used=0,
    )


class ProjectServiceDouble:
    def __init__(self, value: ResearchProjectDocument) -> None:
        self.get_project = AsyncMock(return_value=value)


class EvidenceServiceDouble:
    def __init__(self) -> None:
        self.create_citation = AsyncMock(return_value=citation())
        self.get_claim_evidence = AsyncMock(return_value=())
        self.build_evidence_packet = AsyncMock(return_value=packet())


def application(
    evidence: EvidenceServiceDouble, projects: ProjectServiceDouble, *, authenticated: bool = False
) -> tuple[Any, str | None, FakeAuditRepository | None]:
    if not authenticated:
        app = open_application(router)
        token = None
        audit = None
    else:
        key, token = issued_key(scopes=("research:read", "research:write"), projects=(PROJECT,))
        audit = FakeAuditRepository()
        app = authenticated_application(
            router,
            auth=AuthService(FakeApiKeyRepository(key)),
            audit=AuditService(audit),
        )
    app.dependency_overrides[get_research_evidence_service] = lambda: evidence
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


@pytest.mark.asyncio
async def test_citation_write_and_evidence_packet_use_authorized_services() -> None:
    evidence = EvidenceServiceDouble()
    app, token, audit = application(evidence, ProjectServiceDouble(project()), authenticated=True)
    assert token is not None
    assert audit is not None

    created = await call(
        app,
        "POST",
        "/api/v3/research/citations",
        {
            "research_project_id": RESEARCH,
            "citation_id": "provider-guide",
            "claim_id": "retry-key",
            "source_id": "provider-docs",
            "snapshot_id": "provider-v1",
            "chunk_id": "retry-chunk",
            "locator": {"url": "https://example.test/retries", "page": 2, "line_start": 10},
            "evidence_excerpt": "Use an idempotency key for retryable requests.",
            "relation": "supports",
        },
        headers=bearer(token),
    )
    built = await call(
        app,
        "POST",
        "/api/v3/research/evidence",
        {"query": "retry idempotency", "research_project_id": RESEARCH, "project_id": PROJECT},
        headers=bearer(token),
    )
    shown = await call(
        app,
        "GET",
        f"/api/v3/research/claims/retry-key/evidence?research_project_id={RESEARCH}",
        headers=bearer(token),
    )

    assert created.status_code == 201
    assert created.json()["relation"] == "supports"
    assert created.json()["locator"]["page"] == 2
    assert built.status_code == 200
    assert built.json()["question"] == "What constrains retries?"
    assert shown.status_code == 200
    assert shown.json()["claim_id"] == "retry-key"
    assert evidence.create_citation.await_args.kwargs["allowed_project_ids"] == (PROJECT,)
    assert evidence.build_evidence_packet.await_args.kwargs["project_id"] == PROJECT
    assert [record.action for record in audit.records] == ["research.write"]


@pytest.mark.asyncio
async def test_invalid_or_private_evidence_is_refused_before_service_work() -> None:
    evidence = EvidenceServiceDouble()
    app, token, _ = application(
        evidence, ProjectServiceDouble(project([PRIVATE_PROJECT])), authenticated=True
    )
    assert token is not None

    invalid = await call(
        app,
        "POST",
        "/api/v3/research/citations",
        {
            "research_project_id": RESEARCH,
            "citation_id": "bad",
            "claim_id": "retry-key",
            "source_id": "provider-docs",
            "snapshot_id": "provider-v1",
            "evidence_excerpt": "This has no locator.",
            "relation": "supports",
        },
        headers=bearer(token),
    )
    forbidden = await call(
        app,
        "POST",
        "/api/v3/research/evidence",
        {"query": "retry idempotency", "research_project_id": RESEARCH},
        headers=bearer(token),
    )

    assert invalid.status_code == 422
    assert forbidden.status_code == 403
    evidence.create_citation.assert_not_awaited()
    evidence.build_evidence_packet.assert_not_awaited()
