"""V3 Phase 9 REST candidate review contracts and authorization tests."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_knowledge_candidate_service, get_research_project_service
from app.api.routes.knowledge_candidates import router
from app.models.common import Page
from app.models.promotion import PromotionProvenance
from app.models.research import (
    CandidateState,
    CandidateType,
    KnowledgeCandidateDocument,
    PromotionEventDocument,
    ResearchAuthorization,
    ResearchProjectDocument,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.knowledge_candidate_service import CandidatePromotion, CandidateReview
from tests.api_doubles import authenticated_application, open_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROJECT = "payments-api"
PRIVATE_PROJECT = "private-ledger"
RESEARCH = "retry-evidence"


def research_project(related: list[str] | None = None) -> ResearchProjectDocument:
    project_ids = [PROJECT] if related is None else related
    return ResearchProjectDocument(
        research_project_id=RESEARCH,
        name="Retry evidence",
        question="What constrains safe payment retries?",
        type="technical_research",
        related_project_ids=project_ids,
        authorization=ResearchAuthorization(project_ids=project_ids, owner_key_id="writer-key"),
    )


def candidate(status: CandidateState = CandidateState.PENDING_REVIEW) -> KnowledgeCandidateDocument:
    values: dict[str, Any] = {
        "research_project_id": RESEARCH,
        "candidate_id": "retry-context",
        "candidate_type": CandidateType.PROJECT_CONTEXT,
        "target_project_id": PROJECT,
        "title": "Retry idempotency requirement",
        "content": "Use an idempotency key for retryable writes.",
        "source_claim_ids": ["retry-key"],
        "source_citation_ids": ["provider-support"],
        "confidence": 0.9,
        "confidence_rationale": "Explicit reviewer assessment.",
        "status": status,
        "authorization": ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer-key"),
    }
    if status is not CandidateState.PENDING_REVIEW:
        values.update(reviewer="reviewer-key", reviewed_at="2026-10-01T12:00:00Z")
    return KnowledgeCandidateDocument.model_validate(values)


def event(decision: CandidateState) -> PromotionEventDocument:
    return PromotionEventDocument(
        research_project_id=RESEARCH,
        candidate_id="retry-context",
        event_id="review-20261001",
        target_type=CandidateType.PROJECT_CONTEXT,
        target_id=PROJECT,
        reviewer="reviewer-key",
        decision=decision,
        decided_at="2026-10-01T12:00:00Z",
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="reviewer-key"),
    )


class ProjectServiceDouble:
    def __init__(self, value: ResearchProjectDocument) -> None:
        self.get_project = AsyncMock(return_value=value)


class CandidateServiceDouble:
    def __init__(self) -> None:
        self.create_candidate = AsyncMock(return_value=candidate())
        self.list_candidates = AsyncMock(
            return_value=Page(items=(candidate(),), next_cursor="next")
        )
        self.get_candidate = AsyncMock(return_value=candidate())
        self.review_candidate = AsyncMock(
            return_value=CandidateReview(
                candidate(CandidateState.APPROVED), event(CandidateState.APPROVED)
            )
        )
        self.promote_candidate = AsyncMock(
            return_value=CandidatePromotion(
                candidate=candidate(CandidateState.PROMOTED),
                event=event(CandidateState.PROMOTED),
                target_type="project_context",
                target_id="0123456789abcdef01234567",
                provenance=PromotionProvenance(
                    candidate_id="retry-context",
                    research_project_id=RESEARCH,
                    claim_ids=["retry-key"],
                    citation_ids=["provider-support"],
                    source_ids=["provider-docs"],
                    verified_at="2026-10-01T12:00:00Z",
                ),
            )
        )


def application(
    candidates: CandidateServiceDouble,
    projects: ProjectServiceDouble,
    *,
    secured: bool = False,
    scopes: tuple[str, ...] = (
        "research:read",
        "research:write",
        "research:approve",
        "research:promote",
    ),
) -> tuple[Any, str | None, FakeAuditRepository | None]:
    if secured:
        key, token = issued_key(scopes=scopes, projects=(PROJECT,))
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
    app.dependency_overrides[get_knowledge_candidate_service] = lambda: candidates
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
        "research_project_id": RESEARCH,
        "candidate_id": "retry-context",
        "candidate_type": "project_context",
        "target_project_id": PROJECT,
        "title": "Retry idempotency requirement",
        "content": "Use an idempotency key for retryable writes.",
        "source_claim_ids": ["retry-key"],
        "source_citation_ids": ["provider-support"],
        "confidence": 0.9,
    }


@pytest.mark.asyncio
async def test_candidate_routes_create_list_and_approve_with_audited_non_promotion() -> None:
    candidates = CandidateServiceDouble()
    app, token, audit = application(
        candidates, ProjectServiceDouble(research_project()), secured=True
    )
    assert token is not None
    assert audit is not None

    created = await call(
        app, "POST", "/api/v3/knowledge-candidates", create_body(), headers=bearer(token)
    )
    listed = await call(
        app,
        "GET",
        f"/api/v3/knowledge-candidates?research_project_id={RESEARCH}&limit=1",
        headers=bearer(token),
    )
    approved = await call(
        app,
        "POST",
        "/api/v3/knowledge-candidates/retry-context/approve",
        {"research_project_id": RESEARCH, "expected_revision": 1, "reason": "Reviewed."},
        headers=bearer(token),
    )

    assert created.status_code == 201
    assert created.json()["status"] == "pending_review"
    assert listed.status_code == 200
    assert listed.json()["next_cursor"] == "next"
    assert approved.status_code == 200
    assert approved.json()["candidate"]["status"] == "approved"
    assert approved.json()["event"]["decision"] == "approved"
    assert candidates.create_candidate.await_args.kwargs["allowed_project_ids"] == (PROJECT,)
    assert candidates.review_candidate.await_args.kwargs["decision"] is CandidateState.APPROVED
    assert [record.action for record in audit.records] == [
        "research.write",
        "research.write",
    ]


@pytest.mark.asyncio
async def test_candidate_route_rejects_invalid_state_and_private_reviewer_before_service_work() -> (
    None
):
    candidates = CandidateServiceDouble()
    app, token, _ = application(
        candidates, ProjectServiceDouble(research_project([PRIVATE_PROJECT])), secured=True
    )
    assert token is not None

    invalid = await call(
        app,
        "POST",
        "/api/v3/knowledge-candidates",
        {**create_body(), "status": "approved"},
        headers=bearer(token),
    )
    forbidden = await call(
        app,
        "POST",
        "/api/v3/knowledge-candidates/retry-context/approve",
        {"research_project_id": RESEARCH, "expected_revision": 1},
        headers=bearer(token),
    )

    assert invalid.status_code == 422
    assert forbidden.status_code == 403
    candidates.create_candidate.assert_not_awaited()
    candidates.get_candidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_requires_approval_scope_and_reject_allows_needs_changes() -> None:
    candidates = CandidateServiceDouble()
    app, token, _ = application(
        candidates,
        ProjectServiceDouble(research_project()),
        secured=True,
        scopes=("research:read", "research:write"),
    )
    assert token is not None

    unauthorized = await call(
        app,
        "POST",
        "/api/v3/knowledge-candidates/retry-context/approve",
        {"research_project_id": RESEARCH, "expected_revision": 1},
        headers=bearer(token),
    )

    assert unauthorized.status_code == 403
    candidates.get_candidate.assert_not_awaited()

    candidates = CandidateServiceDouble()
    candidates.review_candidate = AsyncMock(
        return_value=CandidateReview(
            candidate(CandidateState.NEEDS_CHANGES), event(CandidateState.NEEDS_CHANGES)
        )
    )
    open_app, _, _ = application(candidates, ProjectServiceDouble(research_project()))
    changes = await call(
        open_app,
        "POST",
        "/api/v3/knowledge-candidates/retry-context/reject",
        {
            "research_project_id": RESEARCH,
            "expected_revision": 1,
            "decision": "needs_changes",
            "reason": "Narrow the scope.",
        },
    )

    assert changes.status_code == 200
    assert changes.json()["candidate"]["status"] == "needs_changes"
    assert candidates.review_candidate.await_args.kwargs["decision"] is CandidateState.NEEDS_CHANGES


@pytest.mark.asyncio
async def test_candidate_promotion_requires_matching_v1_scope_and_audits_both_writes() -> None:
    candidates = CandidateServiceDouble()
    app, token, audit = application(
        candidates,
        ProjectServiceDouble(research_project()),
        secured=True,
        scopes=("research:read", "research:write", "research:promote", "context:write"),
    )
    assert token is not None
    assert audit is not None

    promoted = await call(
        app,
        "POST",
        "/api/v3/knowledge-candidates/retry-context/promote",
        {"research_project_id": RESEARCH, "expected_revision": 2},
        headers=bearer(token),
    )

    assert promoted.status_code == 200
    assert promoted.json()["candidate"]["status"] == "promoted"
    assert promoted.json()["event"]["decision"] == "promoted"
    assert promoted.json()["target"]["provenance"]["source_ids"] == ["provider-docs"]
    assert candidates.promote_candidate.await_args.kwargs["reviewer"]
    assert [record.action for record in audit.records] == ["research.write", "context.write"]

    denied_candidates = CandidateServiceDouble()
    denied_app, denied_token, _ = application(
        denied_candidates,
        ProjectServiceDouble(research_project()),
        secured=True,
        scopes=("research:read", "research:write", "context:write"),
    )
    assert denied_token is not None
    denied = await call(
        denied_app,
        "POST",
        "/api/v3/knowledge-candidates/retry-context/promote",
        {"research_project_id": RESEARCH, "expected_revision": 2},
        headers=bearer(denied_token),
    )
    assert denied.status_code == 403
    denied_candidates.promote_candidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidate_promotion_respects_related_project_authorization_before_lookup() -> None:
    candidates = CandidateServiceDouble()
    app, token, _ = application(
        candidates,
        ProjectServiceDouble(research_project([PRIVATE_PROJECT])),
        secured=True,
        scopes=("research:read", "research:write", "research:promote", "context:write"),
    )
    assert token is not None

    response = await call(
        app,
        "POST",
        "/api/v3/knowledge-candidates/retry-context/promote",
        {"research_project_id": RESEARCH, "expected_revision": 2},
        headers=bearer(token),
    )

    assert response.status_code == 403
    candidates.get_candidate.assert_not_awaited()
