"""V3 Phase 11 REST contracts, authorization, export, and no-mutation tests."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_research_project_service, get_skill_update_proposal_service
from app.api.routes.skill_update_proposals import router
from app.models.common import Page
from app.models.research import (
    ResearchAuthorization,
    ResearchProjectDocument,
    SkillUpdateProposalDocument,
)
from app.research.errors import SkillUpdateProposalVersionMismatchError
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from tests.api_doubles import authenticated_application, open_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

RESEARCH = "retry-evidence"
PROJECT = "payments-api"
PRIVATE = "private-ledger"


def project(related: list[str] | None = None) -> ResearchProjectDocument:
    linked = [PROJECT] if related is None else related
    return ResearchProjectDocument(
        research_project_id=RESEARCH,
        name="Retry evidence",
        question="What should canonical backend guidance say?",
        type="technical_research",
        related_project_ids=linked,
        authorization=ResearchAuthorization(project_ids=linked, owner_key_id="owner"),
    )


def proposal() -> SkillUpdateProposalDocument:
    return SkillUpdateProposalDocument(
        research_project_id=RESEARCH,
        proposal_id="idempotency-guidance",
        skill_id="backend-guidance",
        current_version="1.2.3",
        proposed_change="Clarify idempotency key guidance.",
        proposed_patch="--- a/skills/backend-guidance.md\n+++ b/skills/backend-guidance.md",
        source_claim_ids=["retry-key"],
        source_citation_ids=["provider-support"],
        reason="The retained provider evidence changed.",
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer"),
    )


class ProjectServiceDouble:
    def __init__(self, value: ResearchProjectDocument) -> None:
        self.get_project = AsyncMock(return_value=value)


class ProposalServiceDouble:
    def __init__(self) -> None:
        self.create_proposal = AsyncMock(return_value=proposal())
        self.get_proposal = AsyncMock(return_value=proposal())
        self.list_proposals = AsyncMock(
            return_value=Page(items=(proposal(),), next_cursor="next-page")
        )
        self.export_patch = AsyncMock(return_value=proposal())


def application(
    proposals: ProposalServiceDouble,
    projects: ProjectServiceDouble,
    *,
    scopes: tuple[str, ...] | None = None,
) -> tuple[Any, str | None, FakeAuditRepository | None]:
    if scopes is None:
        app = open_application(router)
        token = None
        audit = None
    else:
        key, token = issued_key(scopes=scopes, projects=(PROJECT,))
        audit = FakeAuditRepository()
        app = authenticated_application(
            router,
            auth=AuthService(FakeApiKeyRepository(key)),
            audit=AuditService(audit),
        )
    app.dependency_overrides[get_skill_update_proposal_service] = lambda: proposals
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


def payload() -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "proposal_id": "idempotency-guidance",
        "skill_id": "backend-guidance",
        "current_version": "1.2.3",
        "proposed_change": "Clarify idempotency key guidance.",
        "proposed_patch": "--- a/skills/backend-guidance.md\n+++ b/skills/backend-guidance.md",
        "source_claim_ids": ["retry-key"],
        "source_citation_ids": ["provider-support"],
        "reason": "The retained provider evidence changed.",
    }


@pytest.mark.asyncio
async def test_routes_create_list_show_and_export_generated_patch_with_audit() -> None:
    proposals = ProposalServiceDouble()
    app, token, audit = application(
        proposals,
        ProjectServiceDouble(project()),
        scopes=("research:read", "research:write", "skills:read"),
    )
    assert token is not None
    assert audit is not None

    created = await call(
        app,
        "POST",
        "/api/v3/skill-update-proposals",
        payload(),
        headers=bearer(token),
    )
    listed = await call(
        app,
        "GET",
        f"/api/v3/skill-update-proposals?research_project_id={RESEARCH}&skill_id=backend-guidance",
        headers=bearer(token),
    )
    shown = await call(
        app,
        "GET",
        f"/api/v3/skill-update-proposals/idempotency-guidance?research_project_id={RESEARCH}",
        headers=bearer(token),
    )
    exported = await call(
        app,
        "GET",
        f"/api/v3/skill-update-proposals/idempotency-guidance/export?research_project_id={RESEARCH}",
        headers=bearer(token),
    )

    assert created.status_code == 201
    assert created.json()["trust_level"] == "generated"
    assert listed.json()["next_cursor"] == "next-page"
    assert shown.json()["proposal_id"] == "idempotency-guidance"
    assert exported.json()["proposed_patch"].startswith("--- a/")
    assert proposals.create_proposal.await_args.kwargs["allowed_project_ids"] == (PROJECT,)
    assert proposals.list_proposals.await_args.kwargs["skill_id"] == "backend-guidance"
    assert [entry.action for entry in audit.records] == ["research.write"]


@pytest.mark.asyncio
async def test_route_requires_existing_skill_read_scope_and_link_authorization() -> None:
    proposals = ProposalServiceDouble()
    app, token, _ = application(
        proposals, ProjectServiceDouble(project()), scopes=("research:write",)
    )
    assert token is not None
    denied_skill = await call(
        app, "POST", "/api/v3/skill-update-proposals", payload(), headers=bearer(token)
    )
    assert denied_skill.status_code == 403
    proposals.create_proposal.assert_not_awaited()

    private_app, private_token, _ = application(
        ProposalServiceDouble(),
        ProjectServiceDouble(project([PRIVATE])),
        scopes=("research:write", "skills:read"),
    )
    assert private_token is not None
    denied_project = await call(
        private_app,
        "POST",
        "/api/v3/skill-update-proposals",
        payload(),
        headers=bearer(private_token),
    )
    assert denied_project.status_code == 403


@pytest.mark.asyncio
async def test_route_rejects_generated_trust_override_and_surfaces_stale_base() -> None:
    proposals = ProposalServiceDouble()
    app, token, _ = application(
        proposals,
        ProjectServiceDouble(project()),
        scopes=("research:write", "skills:read"),
    )
    assert token is not None
    invalid = payload() | {"trust_level": "trusted"}
    refused = await call(
        app,
        "POST",
        "/api/v3/skill-update-proposals",
        invalid,
        headers=bearer(token),
    )
    assert refused.status_code == 422
    proposals.create_proposal.assert_not_awaited()

    proposals.create_proposal.side_effect = SkillUpdateProposalVersionMismatchError("stale base")
    stale = await call(
        app,
        "POST",
        "/api/v3/skill-update-proposals",
        payload(),
        headers=bearer(token),
    )
    assert stale.status_code == 409
