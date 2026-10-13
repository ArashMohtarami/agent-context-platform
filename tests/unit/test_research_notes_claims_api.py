"""V3 Phase 6 REST contracts, authorization, audit, and validation tests."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import (
    get_research_notes_claims_service,
    get_research_project_service,
)
from app.api.routes.research_notes_claims import router
from app.models.common import Page
from app.models.research import (
    ClaimState,
    ResearchAuthorization,
    ResearchClaimDocument,
    ResearchNoteDocument,
    ResearchProjectDocument,
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


def note(*, revision: int = 1) -> ResearchNoteDocument:
    return ResearchNoteDocument(
        research_project_id=RESEARCH,
        note_id="retry-note",
        title="Retry notes",
        body="Use idempotency keys for retryable writes.",
        source_refs=[],
        created_by="writer-key",
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer-key"),
        revision=revision,
    )


def claim(
    *, revision: int = 1, status: ClaimState = ClaimState.UNREVIEWED
) -> ResearchClaimDocument:
    return ResearchClaimDocument(
        research_project_id=RESEARCH,
        claim_id="retry-key",
        statement="Retryable writes require an idempotency key.",
        status=status,
        confidence=0.8,
        confidence_rationale="A reviewer assessed the retained evidence.",
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer-key"),
        revision=revision,
    )


class ProjectServiceDouble:
    def __init__(self, value: ResearchProjectDocument) -> None:
        self.get_project = AsyncMock(return_value=value)


class ArtifactsServiceDouble:
    def __init__(self) -> None:
        self.create_note = AsyncMock(return_value=note())
        self.get_note = AsyncMock(return_value=note())
        self.update_note = AsyncMock(return_value=note(revision=2))
        self.search_notes = AsyncMock(return_value=())
        self.create_claim = AsyncMock(return_value=claim(status=ClaimState.SUPPORTED))
        self.get_claim = AsyncMock(return_value=claim())
        self.list_claims = AsyncMock(return_value=Page(items=(claim(),), next_cursor="next-page"))
        self.update_claim = AsyncMock(return_value=claim(revision=2, status=ClaimState.CONTESTED))


def application(artifacts: ArtifactsServiceDouble, projects: ProjectServiceDouble) -> Any:
    app = open_application(router)
    app.dependency_overrides[get_research_notes_claims_service] = lambda: artifacts
    app.dependency_overrides[get_research_project_service] = lambda: projects
    return app


def secured_application(
    artifacts: ArtifactsServiceDouble, projects: ProjectServiceDouble
) -> tuple[Any, str, FakeAuditRepository]:
    key, token = issued_key(scopes=("research:read", "research:write"), projects=(PROJECT,))
    audit = FakeAuditRepository()
    app = authenticated_application(
        router,
        auth=AuthService(FakeApiKeyRepository(key)),
        audit=AuditService(audit),
    )
    app.dependency_overrides[get_research_notes_claims_service] = lambda: artifacts
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
async def test_note_and_claim_routes_use_revision_guards_and_explicit_state() -> None:
    artifacts = ArtifactsServiceDouble()
    projects = ProjectServiceDouble(project())
    app = application(artifacts, projects)

    created_note = await call(
        app,
        "POST",
        "/api/v3/research/notes",
        {
            "research_project_id": RESEARCH,
            "note_id": "retry-note",
            "title": "Retry notes",
            "body": "Use idempotency keys for retryable writes.",
            "source_refs": [],
        },
    )
    updated_note = await call(
        app,
        "PATCH",
        "/api/v3/research/notes/retry-note",
        {"research_project_id": RESEARCH, "expected_revision": 1, "status": "reviewed"},
    )
    created_claim = await call(
        app,
        "POST",
        "/api/v3/research/claims",
        {
            "research_project_id": RESEARCH,
            "claim_id": "retry-key",
            "statement": "Retryable writes require an idempotency key.",
            "status": "supported",
            "confidence": 0.8,
            "confidence_rationale": "A reviewer assessed the retained evidence.",
        },
    )
    listed = await call(app, "GET", f"/api/v3/research/claims?research_project_id={RESEARCH}")
    shown = await call(
        app,
        "GET",
        f"/api/v3/research/claims/retry-key?research_project_id={RESEARCH}",
    )
    updated_claim = await call(
        app,
        "PATCH",
        "/api/v3/research/claims/retry-key",
        {"research_project_id": RESEARCH, "expected_revision": 1, "status": "contested"},
    )

    assert created_note.status_code == 201
    assert updated_note.json()["status"] == "working"
    assert created_claim.status_code == 201
    assert created_claim.json()["status"] == "supported"
    assert listed.json()["next_cursor"] == "next-page"
    assert shown.json()["confidence"] == 0.8
    assert updated_claim.json()["status"] == "contested"
    assert artifacts.update_note.await_args.kwargs["changes"] == {"status": "reviewed"}
    assert artifacts.update_claim.await_args.kwargs["changes"] == {"status": "contested"}


@pytest.mark.asyncio
async def test_note_and_claim_writes_are_audited_and_do_not_invoke_promotion() -> None:
    artifacts = ArtifactsServiceDouble()
    projects = ProjectServiceDouble(project())
    app, token, audit = secured_application(artifacts, projects)

    note_response = await call(
        app,
        "POST",
        "/api/v3/research/notes",
        {
            "research_project_id": RESEARCH,
            "note_id": "retry-note",
            "title": "Retry notes",
            "body": "This stays a working artifact.",
        },
        headers=bearer(token),
    )
    claim_response = await call(
        app,
        "POST",
        "/api/v3/research/claims",
        {
            "research_project_id": RESEARCH,
            "claim_id": "retry-key",
            "statement": "A claim does not create a candidate.",
            "status": "unreviewed",
        },
        headers=bearer(token),
    )

    assert note_response.status_code == 201
    assert claim_response.status_code == 201
    assert [item.action for item in audit.records] == ["research.write", "research.write"]
    assert not hasattr(artifacts, "promote")


@pytest.mark.asyncio
async def test_claim_validation_and_private_research_link_are_refused_before_service_work() -> None:
    artifacts = ArtifactsServiceDouble()
    projects = ProjectServiceDouble(project([PRIVATE_PROJECT]))
    app, token, _ = secured_application(artifacts, projects)

    invalid = await call(
        app,
        "POST",
        "/api/v3/research/claims",
        {
            "research_project_id": RESEARCH,
            "claim_id": "bad-confidence",
            "statement": "This should be rejected.",
            "confidence": 1.1,
        },
        headers=bearer(token),
    )
    forbidden = await call(
        app,
        "POST",
        "/api/v3/research/notes",
        {
            "research_project_id": RESEARCH,
            "note_id": "private-note",
            "title": "Private note",
            "body": "The service must not receive this.",
        },
        headers=bearer(token),
    )

    assert invalid.status_code == 422
    assert forbidden.status_code == 403
    artifacts.create_note.assert_not_awaited()
