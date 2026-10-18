"""Disposable Phase 11 prerequisites plus independently approved research policies."""

from datetime import timedelta

from app.api.dependencies import get_research_ingestion_service, get_research_source_service
from app.management.memberships import Memberships
from app.management.models import RootApproval
from app.models.common import utc_now
from app.models.research import ResearchRecordStatus, ResearchSourceType, SourceClass
from app.research.providers import IngestionPayload
from app.security.policy.models import Grant, Issue, Selector
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_policy import TenantPolicy, policy_scope
from tests.v6_knowledge_fixtures import prepare_knowledge
from tests.v6_managed_agent_fixtures import g

PROJECT = "payment-retry-research"


async def prepare_research(h):
    await prepare_knowledge(h)
    now = utc_now()
    common = (
        tuple(
            g(action, kind, ids)
            for action in (
                "research:read",
                "research:write",
                "research:approve",
                "research:promote",
            )
            for kind, ids in (
                ("project", ("payment-service", "payments-api")),
                ("organization_knowledge", ("org-a",)),
                ("skill", ("retry-skill",)),
            )
        )
        + tuple(
            Grant(action=a, resource_kind="research_project", selector="all_in_org")
            for a in ("research:read", "research:write", "research:approve", "research:promote")
        )
        + (g("skills:read", "skill", ("retry-skill",)),)
    )
    review = g("reviews:decide", "research_project", (PROJECT,))
    root = await h.db.policy_assignments.find_one({"_id": h.root})
    ceiling = tuple(Grant.model_validate(x) for x in root["ceiling"]) + common + (review,)
    from app.identity.models import MANAGEMENT_ACTIONS

    with tenant_scope(TenantContext("org-a")):
        approved = await Memberships(h.engine).operator_approve_root(
            "org-a",
            "owner",
            RootApproval(
                tuples=tuple(
                    Grant.model_validate(x)
                    for x in root["tuples"]
                    if x["action"] in MANAGEMENT_ACTIONS
                ),
                ceiling=ceiling,
                targets=Selector(mode="all_in_org"),
                allowed_roles=("reviewer", "contributor", "team_lead"),
                expires_at=now + timedelta(days=4),
            ),
            "fictional-independent-phase12-ceiling-review",
        )
        h.root_generation = approved["generation"]
        await h.governance.operator_recover_corpus(
            "org-a",
            "owner",
            common,
            now + timedelta(days=2),
            "fictional-independent-phase12-promotion-review",
        )
    for user in ("member", "reviewer"):
        tuples = (
            *tuple(
                x
                for x in common
                if x.action
                in (
                    {"research:read", "research:write", "research:approve", "skills:read"}
                    if user == "member"
                    else {"research:read", "research:approve", "skills:read"}
                )
            ),
            review,
        )
        await h.governance.issue(
            "org-a",
            "owner",
            Issue(
                subject_id=user,
                role="reviewer",
                parent_id=h.root,
                expected_parent_generation=h.root_generation,
                tuples=tuples,
                expires_at=now + timedelta(days=2),
            ),
        )
    # Same logical source ID in org B, different content/hash/snapshot. There is
    # no HTTP administrator token: test setup uses the explicit host boundary.
    request = type("FixtureRequest", (), {"app": h.app})()
    with (
        tenant_scope(TenantContext("org-b")),
        policy_scope(
            TenantPolicy("org-b", "installation_operator", (), operator=True, engine=h.engine),
            "research:write",
        ),
    ):
        source = await get_research_source_service(request).create_source(
            research_project_id=PROJECT,
            source_id="browser-evidence",
            source_type=ResearchSourceType.MANUAL,
            uri="manual:fictional-beta",
            title="Fictional Beta private evidence",
            source_class=SourceClass.USER_PROVIDED,
            owner_key_id=None,
            publisher=None,
            author=None,
            published_at=None,
            retrieved_at=None,
            trust_metadata={},
            status=ResearchRecordStatus.ACTIVE,
        )
        result = await get_research_ingestion_service(request).ingest(
            source=source.source,
            payload=IngestionPayload(
                data=b"PRIVATE_RESEARCH_B evidence",
                content_type="text/plain",
                language="en",
                title="Fictional Beta evidence",
            ),
            manually_provided=True,
        )
    h.foreign_snapshot = result.snapshot.snapshot_id
    return {"research_project_id": PROJECT, "foreign_snapshot_id": h.foreign_snapshot}
