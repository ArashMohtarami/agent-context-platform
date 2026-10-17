"""Real Phase 5→6 cutover and explicitly reviewed fictional team policies."""

from datetime import timedelta
from types import SimpleNamespace

import pytest_asyncio

from app.db.initializer import initialize_database
from app.management.memberships import Memberships
from app.management.models import RootApproval, TeamCreate, TeamMemberChange
from app.management.teams import Teams
from app.models.common import utc_now
from app.security.policy.engine import PolicyEngine
from app.security.policy.governance import Governance
from app.security.policy.models import Grant, Issue, Selector
from app.security.tenant import TenantContext, tenant_scope
from tests.integration.test_v6_policy_fencing import policy_db as policy_db


def grant(action, kind="project", ids=("payment-service",)):
    return Grant(action=action, resource_kind=kind, selector="include", ids=list(ids))


def team_grants(team="alpha"):
    return tuple(
        grant(action, "team", (team,))
        for action in (
            "members:read",
            "members:invite",
            "members:suspend",
            "members:reinstate",
            "grants:read",
            "grants:delegate",
            "teams:read",
        )
    )


@pytest_asyncio.fixture
async def management_db(policy_db):  # noqa: F811 -- re-export the real prerequisite fixture
    h = policy_db
    await initialize_database(
        h.db,
        tenant_foundation=True,
        tenant_isolation=True,
        local_identity=True,
        sso=True,
        management_policy=True,
        human_management=True,
        management_cutover=True,
    )
    h.engine = PolicyEngine(h.store, human_management=True)
    h.governance = Governance(h.engine)
    h.local.policy_engine = h.local.sessions.policy_engine = h.engine
    h.app.state.settings = SimpleNamespace(
        v6_browser_origin="https://identity.example",
        v6_management_policy=True,
        v6_human_management=True,
    )
    root = await h.db.policy_assignments.find_one({"_id": h.root})
    member_actions = (
        "members:read",
        "members:invite",
        "members:suspend",
        "members:reinstate",
        "grants:read",
        "grants:delegate",
    )
    own = tuple(
        Grant(action=a, resource_kind="member", selector="all_in_org") for a in member_actions
    ) + tuple(
        grant(a, "organization", ("org-a",))
        for a in (
            "teams:read",
            "teams:manage",
            "org:read",
            "org:manage",
            "org:transfer-owner",
            "grants:all-in-org",
        )
    )
    ceiling = (
        tuple(Grant.model_validate(g) for g in root["ceiling"])
        + tuple(
            Grant(action=a, resource_kind="team", selector="all_in_org")
            for a in (*member_actions, "teams:read")
        )
        + (
            grant("context:write"),
            grant(
                "research:write",
                ids=tuple(
                    (await h.db.research_sources.find_one({"org_id": "org-a"}))["authorization"][
                        "project_ids"
                    ]
                ),
            ),
            grant(
                "research:write",
                "research_project",
                (
                    (await h.db.research_sources.find_one({"org_id": "org-a"}))[
                        "research_project_id"
                    ],
                ),
            ),
        )
    )
    with tenant_scope(TenantContext("org-a")):
        await Memberships(h.engine).operator_approve_root(
            "org-a",
            "owner",
            RootApproval(
                tuples=own,
                ceiling=ceiling,
                targets=Selector(mode="all_in_org"),
                allowed_roles=("team_lead", "contributor", "reviewer"),
                expires_at=utc_now() + timedelta(days=10),
            ),
            "fictional-independent-operator-review",
        )
    for team_id, users in (("alpha", ("lead", "member")), ("beta", ("other", "reviewer"))):
        await Teams(h.engine).create(
            "org-a", "owner", TeamCreate(team_id=team_id, name="Fictional " + team_id)
        )
        for user in users:
            await Teams(h.engine).change_member(
                "org-a",
                "owner",
                team_id,
                user,
                TeamMemberChange(expected_revision=0, expected_team_revision=1, status="active"),
            )
    h.lead = await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="lead",
            role="team_lead",
            parent_id=h.root,
            expected_parent_generation=2,
            tuples=(
                *team_grants(),
                grant("context:read"),
                grant("grants:read", "member", ("lead",)),
            ),
            ceiling=(grant("context:read"), grant("context:write")),
            remaining_depth=1,
            allowed_roles=("contributor",),
            expires_at=utc_now() + timedelta(days=5),
            team_id="alpha",
            expected_team_generation=1,
        ),
    )
    h.other_assignment = await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="other",
            parent_id=h.root,
            expected_parent_generation=2,
            tuples=(grant("context:read"),),
            expires_at=utc_now() + timedelta(days=5),
            team_id="beta",
            expected_team_generation=1,
        ),
    )
    yield h
