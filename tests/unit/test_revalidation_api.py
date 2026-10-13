"""V3 Phase 14 REST authorization and non-mutation contract tests."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_research_project_service, get_revalidation_service
from app.api.routes.revalidation import router
from app.models.freshness import FreshnessClass, RevalidationReason
from app.models.research import (
    CandidateType,
    KnowledgeCandidateDocument,
    ResearchAuthorization,
    ResearchProjectDocument,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.revalidation_service import (
    RevalidationProposal,
    SourceChange,
    StaleKnowledge,
    StaleKnowledgePage,
)
from tests.api_doubles import authenticated_application, open_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROJECT = "payments"
PRIVATE = "private-ledger"
RESEARCH = "retry-evidence"
WHEN = datetime(2026, 10, 8, tzinfo=UTC)


def research_project(related: list[str] | None = None) -> ResearchProjectDocument:
    project_ids = [PROJECT] if related is None else related
    return ResearchProjectDocument(
        research_project_id=RESEARCH,
        name="Retry evidence",
        question="When should retry knowledge be reviewed?",
        type="technical_research",
        related_project_ids=project_ids,
        authorization=ResearchAuthorization(project_ids=project_ids, owner_key_id="writer"),
    )


def candidate() -> KnowledgeCandidateDocument:
    return KnowledgeCandidateDocument(
        research_project_id=RESEARCH,
        candidate_id="retry-knowledge",
        candidate_type=CandidateType.MEMORY,
        target_project_id=PROJECT,
        title="Retry key",
        content="Preserve the idempotency key.",
        source_claim_ids=["retry-claim"],
        source_citation_ids=["retry-citation"],
        revalidation_of_candidate_id="original-retry-knowledge",
        revalidation_reasons=[RevalidationReason.CONTENT_HASH_CHANGED],
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer"),
    )


def stale() -> StaleKnowledge:
    return StaleKnowledge(
        research_project_id=RESEARCH,
        candidate_id="original-retry-knowledge",
        target_type="memory",
        target_id="0123456789abcdef01234567",
        title="Retry key",
        verified_at=datetime(2026, 10, 1, tzinfo=UTC),
        source_snapshot_ids=("retry-v1",),
        review_after=None,
        freshness_class=FreshnessClass.VERSION_DEPENDENT,
        reasons=(RevalidationReason.CONTENT_HASH_CHANGED,),
        source_changes=(
            SourceChange(
                source_id="retry-source",
                baseline_snapshot_id="retry-v1",
                observed_snapshot_id="retry-v2",
                reasons=(RevalidationReason.CONTENT_HASH_CHANGED,),
            ),
        ),
    )


class Projects:
    def __init__(self, value: ResearchProjectDocument) -> None:
        self.get_project = AsyncMock(return_value=value)


class Revalidation:
    def __init__(self) -> None:
        self.list_stale_knowledge = AsyncMock(
            return_value=StaleKnowledgePage(items=(stale(),), next_cursor=None)
        )
        self.propose_revalidation = AsyncMock(
            return_value=RevalidationProposal(candidate=candidate(), stale_knowledge=stale())
        )


def application(
    revalidation: Revalidation,
    projects: Projects,
    *,
    secured: bool = False,
    scopes: tuple[str, ...] = ("research:read", "research:write"),
) -> tuple[Any, str | None, FakeAuditRepository | None]:
    if secured:
        key, token = issued_key(scopes=scopes, projects=(PROJECT,))
        audit = FakeAuditRepository()
        app = authenticated_application(
            router, auth=AuthService(FakeApiKeyRepository(key)), audit=AuditService(audit)
        )
    else:
        app = open_application(router)
        token = None
        audit = None
    app.dependency_overrides[get_revalidation_service] = lambda: revalidation
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
async def test_revalidation_routes_list_potential_staleness_and_create_only_pending_candidate() -> (
    None
):
    service = Revalidation()
    app, token, audit = application(service, Projects(research_project()), secured=True)
    assert token is not None
    assert audit is not None

    listed = await call(
        app,
        "GET",
        f"/api/v3/research/revalidation/stale?research_project_id={RESEARCH}&as_of=2026-10-08T00:00:00Z",
        headers=bearer(token),
    )
    proposed = await call(
        app,
        "POST",
        "/api/v3/research/revalidation/candidates/original-retry-knowledge",
        {"research_project_id": RESEARCH, "as_of": "2026-10-08T00:00:00Z"},
        headers=bearer(token),
    )

    assert listed.status_code == 200
    assert listed.json()["items"][0]["reasons"] == ["content_hash_changed"]
    assert proposed.status_code == 201
    assert proposed.json()["candidate"]["status"] == "pending_review"
    assert service.propose_revalidation.await_args.kwargs["created_by"]
    assert [record.action for record in audit.records] == ["research.write"]


@pytest.mark.asyncio
async def test_revalidation_routes_reject_scope_and_private_project() -> None:
    service = Revalidation()
    app, token, _ = application(
        service, Projects(research_project()), secured=True, scopes=("research:read",)
    )
    assert token is not None

    denied_write = await call(
        app,
        "POST",
        "/api/v3/research/revalidation/candidates/original-retry-knowledge",
        {"research_project_id": RESEARCH},
        headers=bearer(token),
    )
    assert denied_write.status_code == 403
    service.propose_revalidation.assert_not_awaited()

    private_service = Revalidation()
    private_app, private_token, _ = application(
        private_service, Projects(research_project([PRIVATE])), secured=True
    )
    assert private_token is not None
    denied_private = await call(
        private_app,
        "GET",
        f"/api/v3/research/revalidation/stale?research_project_id={RESEARCH}",
        headers=bearer(private_token),
    )
    assert denied_private.status_code == 403
    private_service.list_stale_knowledge.assert_not_awaited()
