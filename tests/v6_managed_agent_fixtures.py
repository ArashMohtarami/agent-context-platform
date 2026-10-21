"""Real Phase6→7 operator cutover and fictional independently reviewed machine policy."""

from datetime import timedelta
from types import SimpleNamespace

import pytest_asyncio

from app.db.initializer import initialize_database
from app.identity.models import MANAGEMENT_ACTIONS
from app.managed_agents.models import ClientApproval, CredentialIssue
from app.managed_agents.operator import MachineCutover
from app.managed_agents.service import ManagedAgents
from app.management.memberships import Memberships
from app.management.models import RootApproval
from app.models.common import utc_now
from app.repositories.agent_repository import (
    MongoAgentActivityRepository,
    MongoAgentClientRepository,
    MongoAgentSessionRepository,
)
from app.repositories.credential_repository import MongoCredentialResolver
from app.security.policy.engine import PolicyEngine
from app.security.policy.governance import Governance
from app.security.policy.models import Grant, Issue, Selector
from app.security.tenant import TenantContext, tenant_scope
from app.services.agent_activity_service import AgentActivityService
from app.services.agent_session_service import AgentSessionService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService


def g(action, kind="project", ids=("payment-service",)):
    return Grant(action=action, resource_kind=kind, selector="include", ids=list(ids))


def domain(h):
    source = h.source
    return (
        g("context:read", ids=("payment-service", "payments-api")),
        g("context:write", ids=("payment-service", "payments-api")),
        g("projects:read", "project_directory"),
        g("skills:read", "skill", ("retry-skill",)),
        *(
            g(a, kind, ids)
            for a in ("agent:self", "session:write", "audit:self:read")
            for kind, ids in (
                ("client", ("managed-worker",)),
                ("project", ("payment-service",)),
                ("organization_knowledge", ("org-a",)),
            )
        ),
        *(
            g(a, "project", tuple(source["authorization"]["project_ids"]))
            for a in ("research:read", "research:write", "research:approve")
        ),
        *(
            g(a, "research_project", (source["research_project_id"],))
            for a in ("research:read", "research:write", "research:approve")
        ),
        *(
            g(a, kind, ids)
            for a in ("content:read", "content:write")
            for kind, ids in (
                ("creator_profile", ("personal-brand",)),
                ("project", ("payment-service",)),
            )
        ),
    )


@pytest_asyncio.fixture
async def managed_agents_db(management_db):
    h = management_db
    await initialize_database(
        h.db,
        tenant_foundation=True,
        tenant_isolation=True,
        local_identity=True,
        sso=True,
        management_policy=True,
        human_management=True,
        managed_agents=True,
        management_cutover=True,
    )
    h.engine = PolicyEngine(h.store, human_management=True, managed_agents=True)
    h.governance = Governance(h.engine)
    h.agents = ManagedAgents(h.engine)
    h.cutover = MachineCutover(h.engine)
    h.local.policy_engine = h.local.sessions.policy_engine = h.engine
    h.source = await h.db.research_sources.find_one({"org_id": "org-a"})
    root = await h.db.policy_assignments.find_one({"_id": h.root})
    org_caps = tuple(
        g(a, "organization", ("org-a",))
        for a in ("clients:manage:org", "clients:read:org", "credentials:revoke:org")
    )
    team_caps = tuple(
        g(a, "team", ("alpha",))
        for a in ("clients:manage:team", "clients:read:team", "credentials:revoke:team")
    )
    own_caps = tuple(
        g(a, "member", ("member",))
        for a in (
            "credentials:issue:own",
            "credentials:revoke:own",
            "clients:read:own",
            "clients:manage:own",
        )
    )
    reviews = (
        g("reviews:decide", "creator_profile", ("personal-brand",)),
        g("reviews:decide", "research_project", (h.source["research_project_id"],)),
    )
    h.domain = domain(h)
    # Client references must exist before included client resources can be reviewed.
    # Gateway identity/session grants use a reviewed org-wide selector, with the
    # server's exact key/client check retaining the operational ownership boundary.
    h.domain = tuple(
        Grant(action=x.action, resource_kind=x.resource_kind, selector="all_in_org")
        if x.resource_kind == "client"
        else x
        for x in h.domain
    )
    ceiling = (
        tuple(Grant.model_validate(x) for x in root["ceiling"])
        + h.domain
        + org_caps
        + team_caps
        + own_caps
        + reviews
    )
    with tenant_scope(TenantContext("org-a")):
        await Memberships(h.engine).operator_approve_root(
            "org-a",
            "owner",
            RootApproval(
                tuples=tuple(
                    Grant.model_validate(x)
                    for x in root["tuples"]
                    if x["action"] in MANAGEMENT_ACTIONS
                )
                + org_caps,
                ceiling=ceiling,
                targets=Selector(mode="all_in_org"),
                allowed_roles=("team_lead", "contributor", "reviewer"),
                expires_at=utc_now() + timedelta(days=10),
            ),
            "fictional-independent-phase7-root-review",
        )
    h.root_generation = 3
    h.sponsor_assignment = await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="member",
            parent_id=h.root,
            expected_parent_generation=3,
            tuples=h.domain + own_caps,
            expires_at=utc_now() + timedelta(days=4),
            team_id="alpha",
            expected_team_generation=1,
        ),
    )
    h.lead = await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="lead",
            role="team_lead",
            parent_id=h.root,
            expected_parent_generation=3,
            tuples=(*team_caps, g("grants:delegate", "team", ("alpha",))),
            ceiling=h.domain + reviews,
            remaining_depth=1,
            allowed_roles=("contributor",),
            expires_at=utc_now() + timedelta(days=5),
            team_id="alpha",
            expected_team_generation=1,
        ),
    )
    # Separate reviewer already belongs to beta, and never holds custody of alpha.
    h.reviewer_assignment = await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="reviewer",
            role="reviewer",
            parent_id=h.root,
            expected_parent_generation=3,
            tuples=h.domain + reviews,
            expires_at=utc_now() + timedelta(days=4),
            team_id="beta",
            expected_team_generation=1,
        ),
    )
    from tests.integration.test_v6_mcp_isolation import application_settings

    settings = application_settings(h.db)
    h.app.state.settings = settings.model_copy(
        update={
            "v6_browser_origin": "https://identity.example",
            "v6_management_policy": True,
            "v6_human_management": True,
            "v6_managed_agents": True,
        }
    )
    h.app.state.mongodb = SimpleNamespace(database=h.db)
    h.app.state.embeddings = None
    h.app.state.auth = AuthService(
        MongoCredentialResolver(
            h.db, management_policy=True, human_management=True, managed_agents=True
        ),
        MongoAgentClientRepository(h.db),
    )
    h.app.state.audit = AuditService(None)
    from app.repositories.idempotency_repository import MongoGatewayIdempotencyRepository
    from app.services.gateway_idempotency_service import GatewayIdempotencyService

    h.app.state.gateway_idempotency_service = GatewayIdempotencyService(
        MongoGatewayIdempotencyRepository(h.db),
        retention_seconds=settings.mcp_idempotency_retention_seconds,
    )
    h.app.state.agent_session_service = AgentSessionService(MongoAgentSessionRepository(h.db))
    h.app.state.agent_activity_service = AgentActivityService(MongoAgentActivityRepository(h.db))
    h.legacy_keys = await h.db.api_keys.find({"status": "active"}).to_list(100)
    # Explicit fictional operator decisions, no automatic runtime grandfathering.
    for key in h.legacy_keys:
        if not await h.db.policy_anchors.find_one({"_id": key["org_id"]}):
            await h.store.insert(
                "policy_anchors",
                {
                    "_id": key["org_id"],
                    "org_id": key["org_id"],
                    "revision": 1,
                    "fence": 0,
                    "status": "active",
                    "created_at": utc_now(),
                },
            )
        with tenant_scope(TenantContext(key["org_id"])):
            await h.cutover.revoke_legacy(
                key["org_id"], key["key_id"], "fictional-reviewed-legacy-retirement"
            )
    with tenant_scope(TenantContext("org-a")):
        await h.cutover.activate("org-a", "fictional-reviewed-machine-activation")
    yield h


def approval(h, **changes):
    return ClientApproval(
        client_id="managed-worker",
        name="Fictional Coding Worker",
        client_type="codex",
        subject_id="member",
        parent_id=h.lead["_id"],
        expected_parent_generation=1,
        team_id="alpha",
        expected_team_generation=1,
        tuples=h.domain,
        expires_at=utc_now() + timedelta(days=1),
        **changes,
    )


async def provision(h, **changes):
    row = await h.agents.approve("org-a", "lead", approval(h, **changes))
    command = CredentialIssue(
        issuance_id="first-delivery",
        expected_client_revision=row["revision"],
        tuples=tuple(Grant.model_validate(g) for g in row["tuples"]),
        expires_at=utc_now() + timedelta(hours=12),
    )
    delivered = await h.agents.issue("org-a", "member", row["client_id"], command)
    return row, command, delivered
