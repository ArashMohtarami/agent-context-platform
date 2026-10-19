"""Real Phase7 prerequisites, colliding identifiers and fictional private sentinels."""

from datetime import timedelta

import pytest_asyncio

from app.identity.models import MANAGEMENT_ACTIONS
from app.management.memberships import Memberships
from app.management.models import RootApproval
from app.models.agent import AgentActivityDocument
from app.models.common import normalize_mongo_timestamp, utc_now
from app.security.policy.models import Grant, Issue, Selector
from app.security.tenant import TenantContext, tenant_scope
from app.summaries.models import Checkpoint, SummaryQuery
from app.summaries.operator import checkpoint


def grant(action, kind="organization", ids=("org-a",)):
    return Grant(action=action, resource_kind=kind, selector="include", ids=list(ids))


@pytest_asyncio.fixture
async def summary_db(managed_agents_db):
    yield await prepare_summary(managed_agents_db)


async def prepare_summary(h, *, begin=None, end=None):
    root = await h.db.policy_assignments.find_one({"_id": h.root})
    caps = (
        grant("activity:read:org"),
        grant("audit:read:org"),
        grant("reports:export"),
        grant("activity:read:self", "member", ("member",)),
        grant("activity:read:self", "project", ("payment-service",)),
        grant("activity:read:team", "team", ("alpha", "beta")),
        grant("activity:read:team", "project", ("payment-service",)),
    )
    lead = await h.db.policy_assignments.find_one({"_id": h.lead["_id"]})
    sponsor = await h.db.policy_assignments.find_one({"_id": h.sponsor_assignment["_id"]})
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
                + caps[:3],
                ceiling=tuple(Grant.model_validate(x) for x in root["ceiling"]) + caps,
                targets=Selector(mode="all_in_org"),
                allowed_roles=("team_lead", "contributor", "executive", "auditor"),
                expires_at=utc_now() + timedelta(days=10),
            ),
            "fictional-independent-summary-root-review",
        )

    h.root_generation = 4

    async def issue(subject, role, tuples, team=None, **kwargs):
        return await h.governance.issue(
            "org-a",
            "owner",
            Issue(
                subject_id=subject,
                role=role,
                parent_id=h.root,
                expected_parent_generation=4,
                tuples=tuples,
                team_id=team,
                expected_team_generation=1 if team else None,
                expires_at=utc_now() + timedelta(days=2),
                **kwargs,
            ),
        )

    h.lead = await issue(
        "lead",
        "team_lead",
        (
            *(Grant.model_validate(x) for x in lead["tuples"]),
            grant("activity:read:team", "team", ("alpha",)),
            caps[-1],
        ),
        "alpha",
        ceiling=tuple(Grant.model_validate(x) for x in lead["ceiling"]),
        remaining_depth=1,
        allowed_roles=("contributor",),
    )
    h.sponsor_assignment = await issue(
        "member",
        "contributor",
        tuple(Grant.model_validate(x) for x in sponsor["tuples"]) + caps[3:5],
        "alpha",
    )
    h.beta = await issue(
        "reviewer", "team_lead", (grant("activity:read:team", "team", ("beta",)), caps[-1]), "beta"
    )
    h.executive = await issue("other", "executive", (caps[0],))
    h.coverage_start = (await h.db["_managed_agents_control"].find_one({"_id": "installation"}))[
        "created_at"
    ]
    h.begin = normalize_mongo_timestamp(begin or utc_now() - timedelta(hours=2))
    h.event_ids = {}
    for org, specs in (
        (
            "org-a",
            (
                ("alpha-one", "alpha", "payment-service", "succeeded"),
                ("alpha-two", "alpha", "payment-service", "failed"),
                ("beta-one", "beta", "payment-service", "denied"),
                ("hidden-project", "alpha", "payments-api", "succeeded"),
                ("legacy", None, "payment-service", "succeeded"),
            ),
        ),
        ("org-b", (("foreign-private-sentinel", "alpha", "payment-service", "failed"),)),
    ):
        for index, (event_id, team, project, outcome) in enumerate(specs):
            with tenant_scope(TenantContext(org)):
                document = AgentActivityDocument(
                    event_id=event_id,
                    client_id="retired-agent",
                    client_type="codex",
                    operation="search_knowledge",
                    tool="search_knowledge",
                    project_id=project,
                    status=outcome,
                    latency_ms=3,
                    created_at=h.begin + timedelta(minutes=index + 1),
                    sponsor_id="member" if team else None,
                    sponsor_team_id=team,
                    sponsorship_revision=1 if team else None,
                )
                await h.db.agent_activity.insert_one(
                    document.model_dump(mode="python", by_alias=True)
                )
                h.event_ids[event_id] = str(document.id)
    h.end = normalize_mongo_timestamp(end or utc_now())
    h.query = SummaryQuery(start=h.begin, end=h.end, scope="org")
    h.declaration = Checkpoint(
        source="gateway_activity",
        coverage_start=h.coverage_start,
        retained_from=h.begin,
        approval_reference="fictional-stream-declaration",
    )
    with tenant_scope(TenantContext("org-a")):
        await checkpoint(h.engine, "org-a", h.declaration)
    return h


def params(query):
    return {k: v for k, v in query.model_dump(mode="json").items() if v is not None}
