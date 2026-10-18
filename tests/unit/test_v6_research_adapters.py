"""Evidence console bounds, per-action gates and shared review operation hooks."""

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.api.routes.research_console import router
from app.models.research import ResearchSourceType
from app.research.console.contracts import (
    BrowserIngestion,
    ClaimDecision,
    Decision,
    ProposalDecision,
)
from app.research.console.service import browser_source_type, skill_selector
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.hooks import AUTHORED_COLLECTIONS, is_decision
from app.security.policy.reviews import require_review
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_policy import ResourceGrant, TenantPolicy, policy_scope


def policy(subject="reviewer", grants=()):
    return TenantPolicy(
        "org-a",
        subject,
        tuple(ResourceGrant(a, k, "include", ids) for a, k, ids in grants),
        managed=True,
        subject_kind="human",
    )


@pytest.mark.parametrize(
    "body",
    [
        {"fetch_repository": True},
        {"content": "x" * 8193, "content_type": "text/plain"},
        {"content_base64": "%%", "content_type": "text/plain"},
        {"content": "x", "content_type": "text/plain", "filename": "../secret"},
        {"fetch_url": True, "url": "http://127.0.0.1"},
    ],
)
def test_bounded_inputs_cannot_choose_repository_paths_or_override_registered_url(body):
    with pytest.raises(ValidationError):
        BrowserIngestion.model_validate(body)


def test_manual_file_url_modes_and_decision_revisions_are_explicit():
    assert (
        BrowserIngestion(content="safe", content_type="text/markdown").submitted_bytes() == b"safe"
    )
    assert BrowserIngestion(fetch_url=True).submitted_bytes() == b""
    for medium in (ResearchSourceType.GIT, ResearchSourceType.GITHUB, ResearchSourceType.RSS):
        with pytest.raises(PolicyDenied):
            browser_source_type(medium)
    with pytest.raises(ValidationError):
        Decision(decision="approved")
    with pytest.raises(ValidationError):
        ProposalDecision(expected_revision=1, decision="superseded")
    with pytest.raises(ValidationError):
        ClaimDecision(
            expected_revision=1, status="unreviewed", confidence_rationale="No assessment"
        )


def test_skill_selectors_intersect_the_exact_action_and_never_borrow_write():
    p = policy(
        grants=(
            ("research:read", "skill", ("a",)),
            ("research:write", "skill", ("b",)),
            ("skills:read", "skill", ("a", "b")),
        )
    )
    with tenant_scope(TenantContext("org-a")), policy_scope(p, "research:read"):
        assert skill_selector("research:read") == ("a",)
        assert skill_selector("research:write") == ("b",)
        assert skill_selector("research:approve") == ()


@pytest.mark.parametrize(
    "collection,status",
    [
        ("skill_update_proposals", "approved"),
        ("research_claims", "supported"),
        ("research_notes", "reviewed"),
        ("research_briefs", "ready"),
        ("knowledge_candidates", "rejected"),
    ],
)
def test_every_research_decision_uses_operation_read_and_independent_managed_review(
    collection, status
):
    assert (
        collection in AUTHORED_COLLECTIONS
        and is_decision(collection, {"status": status}) == "research:approve"
    )
    grants = (
        *tuple(
            (a, k, ids)
            for a in ("research:read", "research:approve")
            for k, ids in (("project", ("a",)), ("research_project", ("study",)))
        ),
        ("reviews:decide", "research_project", ("study",)),
    )
    row = {
        "org_id": "org-a",
        "research_project_id": "study",
        "authorization": {"project_ids": ["a"]},
        "actor": {"actor_type": "human", "user_id": "author", "org_id": "org-a"},
    }
    with tenant_scope(TenantContext("org-a")), policy_scope(policy(grants=grants)):
        require_review(row, collection, "research:approve")
        with pytest.raises(PolicyDenied):
            require_review({**row, "org_id": "org-b"}, collection, "research:approve")
    for p in (
        policy("author", grants),
        policy(grants=grants[:-1]),
        policy(grants=tuple(g for g in grants if g[0] != "research:approve")),
    ):
        with tenant_scope(TenantContext("org-a")), policy_scope(p), pytest.raises(PolicyDenied):
            require_review(row, collection, "research:approve")


def test_all_console_routes_are_typed_session_only_with_org_path_contract():
    assert len(router.routes) >= 24
    for route in router.routes:
        assert isinstance(route, APIRoute) and route.response_model is not None
        gates = [d for d in route.dependant.dependencies if d.call.__name__ == "gate"]
        assert len(gates) == 1
        assert any(p.name == "org_id" for p in gates[0].path_params)
        assert any(d.call.__name__ == "current_browser" for d in gates[0].dependencies)
        assert not any("principal" in d.call.__name__ for d in route.dependant.dependencies)


@pytest.mark.asyncio
async def test_rejected_session_is_not_reclassified_as_a_research_filter_error():
    import httpx
    from fastapi import APIRouter, FastAPI
    from fastapi.responses import JSONResponse

    from app.identity.crypto import AuthRejected
    from app.research.console.boundary import ResearchRoute

    app = FastAPI()
    routes = APIRouter(route_class=ResearchRoute)

    @routes.get("/session")
    async def rejected():
        raise AuthRejected("synthetic revoked session")

    @app.exception_handler(AuthRejected)
    async def unauthorized(request, error):
        return JSONResponse({"detail": "authentication was not accepted"}, status_code=401)

    app.include_router(routes)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="https://test"
    ) as client:
        response = await client.get("/session")
    assert response.status_code == 401 and "synthetic" not in response.text
