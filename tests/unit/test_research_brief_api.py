"""V3 Phase 8 REST brief contracts, authorization, and audit tests."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_research_brief_service, get_research_project_service
from app.api.routes.research_briefs import router
from app.models.research import (
    CitationLocator,
    CitationRelation,
    ClaimState,
    ResearchAuthorization,
    ResearchBriefCitationReference,
    ResearchBriefClaimReference,
    ResearchBriefContradiction,
    ResearchBriefDocument,
    ResearchProjectDocument,
    ResearchSourceReference,
    SourceClass,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from tests.api_doubles import authenticated_application, open_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROJECT = "payments-api"
PRIVATE_PROJECT = "private-ledger"
RESEARCH = "retry-evidence"


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


def brief() -> ResearchBriefDocument:
    return ResearchBriefDocument(
        research_project_id=RESEARCH,
        brief_id="retry-summary",
        title="Retry evidence",
        question="What constrains safe payment retries?",
        scope="Provider documentation and incident reports.",
        summary="Structured research material only; this does not claim completeness.",
        key_findings=["Retry keys are required."],
        evidence_citation_ids=["provider-support"],
        contradicting_citation_ids=["legacy-contradicts"],
        source_ids=["provider-docs", "legacy-report"],
        claim_refs=[
            ResearchBriefClaimReference(
                claim_id="retry-key",
                statement="Retry keys are required.",
                status=ClaimState.CONTESTED,
                confidence=0.7,
            )
        ],
        citation_refs=[
            ResearchBriefCitationReference(
                citation_id="provider-support",
                claim_id="retry-key",
                source_id="provider-docs",
                snapshot_id="provider-v1",
                chunk_id="provider-chunk",
                source_class=SourceClass.OFFICIAL_DOCUMENTATION,
                locator=CitationLocator(url="https://example.test/retries", page=2, line_start=10),
                evidence_excerpt="Use an idempotency key.",
                relation=CitationRelation.SUPPORTS,
            ),
            ResearchBriefCitationReference(
                citation_id="legacy-contradicts",
                claim_id="retry-key",
                source_id="legacy-report",
                snapshot_id="legacy-v1",
                chunk_id=None,
                source_class=SourceClass.SECONDARY,
                locator=CitationLocator(url="https://example.test/legacy", line_start=3),
                evidence_excerpt="A legacy client retries without a key.",
                relation=CitationRelation.CONTRADICTS,
            ),
        ],
        contradictions=[
            ResearchBriefContradiction(
                claim_id="retry-key",
                statement="Retry keys are required.",
                supporting_citation_ids=["provider-support"],
                contradicting_citation_ids=["legacy-contradicts"],
            )
        ],
        source_refs=[
            ResearchSourceReference(
                source_id="provider-docs",
                snapshot_id="provider-v1",
                chunk_id="provider-chunk",
            ),
            ResearchSourceReference(source_id="legacy-report", snapshot_id="legacy-v1"),
        ],
        unknowns=["This brief is structured evidence material and does not claim completeness."],
        next_questions=["Which versions enforce idempotency keys?"],
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer-key"),
    )


class ProjectServiceDouble:
    def __init__(self, value: ResearchProjectDocument) -> None:
        self.get_project = AsyncMock(return_value=value)


class BriefServiceDouble:
    def __init__(self) -> None:
        self.build_brief = AsyncMock(return_value=brief())
        self.get_brief = AsyncMock(return_value=brief())


def application(
    briefs: BriefServiceDouble,
    projects: ProjectServiceDouble,
    *,
    secured: bool = False,
) -> tuple[Any, str | None, FakeAuditRepository | None]:
    if secured:
        key, token = issued_key(scopes=("research:read", "research:write"), projects=(PROJECT,))
        audit = FakeAuditRepository()
        app = authenticated_application(
            router,
            auth=AuthService(FakeApiKeyRepository(key)),
            audit=AuditService(audit),
        )
    else:
        app = open_application(router)
        token = None
        audit = None
    app.dependency_overrides[get_research_brief_service] = lambda: briefs
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
async def test_brief_routes_return_structured_provenance_and_audit_creation() -> None:
    briefs = BriefServiceDouble()
    app, token, audit = application(briefs, ProjectServiceDouble(project()), secured=True)
    assert token is not None
    assert audit is not None

    created = await call(
        app,
        "POST",
        "/api/v3/research/briefs",
        {
            "research_project_id": RESEARCH,
            "brief_id": "retry-summary",
            "scope": "Provider documentation and incident reports.",
            "next_questions": ["Which versions enforce idempotency keys?"],
        },
        headers=bearer(token),
    )
    shown = await call(
        app,
        "GET",
        f"/api/v3/research/briefs/retry-summary?research_project_id={RESEARCH}",
        headers=bearer(token),
    )

    assert created.status_code == 201
    assert created.json()["citation_refs"][0]["locator"]["page"] == 2
    assert created.json()["contradictions"][0]["claim_id"] == "retry-key"
    assert shown.status_code == 200
    assert shown.json()["sources"][0]["snapshot_id"] == "provider-v1"
    assert briefs.build_brief.await_args.kwargs["allowed_project_ids"] == (PROJECT,)
    assert [record.action for record in audit.records] == ["research.write"]


@pytest.mark.asyncio
async def test_brief_route_rejects_invalid_body_and_private_project_before_service_work() -> None:
    briefs = BriefServiceDouble()
    app, token, _ = application(
        briefs, ProjectServiceDouble(project([PRIVATE_PROJECT])), secured=True
    )
    assert token is not None

    invalid = await call(
        app,
        "POST",
        "/api/v3/research/briefs",
        {
            "research_project_id": RESEARCH,
            "brief_id": "invalid-brief",
            "scope": "A narrow scope.",
            "next_questions": ["Duplicate?", "Duplicate?"],
        },
        headers=bearer(token),
    )
    forbidden = await call(
        app,
        "POST",
        "/api/v3/research/briefs",
        {
            "research_project_id": RESEARCH,
            "brief_id": "private-brief",
            "scope": "A private scope.",
        },
        headers=bearer(token),
    )

    assert invalid.status_code == 422
    assert forbidden.status_code == 403
    briefs.build_brief.assert_not_awaited()
