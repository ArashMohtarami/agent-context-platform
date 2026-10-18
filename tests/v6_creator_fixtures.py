"""Real repeated-ID creator corpus and explicitly approved human policy fixtures."""

import asyncio
from datetime import timedelta

from app.db.content_indexes import (
    CONTENT_COLLECTIONS,
    CONTENT_TEXT_INDEXES,
    CONTENT_VECTOR_INDEXES,
    content_text_definition,
    content_vector_definition,
)
from app.identity.models import MANAGEMENT_ACTIONS
from app.management.memberships import Memberships
from app.management.models import RootApproval
from app.models.common import utc_now
from app.security.policy.models import Grant, Issue, Selector
from app.security.tenant import TenantContext, tenant_scope
from tests.v6_knowledge_fixtures import FixtureEmbeddings
from tests.v6_managed_agent_fixtures import g
from tests.v6_research_console_fixtures import PROJECT, prepare_research

PROFILE = "personal-brand"


def merged(grants):
    dimensions = {}
    for grant in grants:
        dimensions.setdefault((grant.action, grant.resource_kind), []).append(grant)
    result = []
    for (action, kind), rows in dimensions.items():
        if any(r.selector == "all_in_org" for r in rows):
            result.append(Grant(action=action, resource_kind=kind, selector="all_in_org"))
        else:
            ids = sorted({i for r in rows if r.selector == "include" for i in r.ids})
            if ids:
                result.append(g(action, kind, tuple(ids)))
    return tuple(result)


async def prepare_creator(h):
    await prepare_research(h)
    now = utc_now()
    common = (
        *(
            g(a, "creator_profile", (PROFILE,))
            for a in (
                "content:read",
                "content:write",
                "content:publish",
                "reviews:decide",
            )
        ),
        *(
            g(a, "project", ("payment-service", "payments-api"))
            for a in ("content:read", "content:write", "content:publish")
        ),
        *(
            g(a, "research_project", (PROJECT,))
            for a in ("content:read", "content:write", "content:publish")
        ),
        *(g(a, "skill", ("retry-skill",)) for a in ("content:read", "content:write")),
        *(g(a, "organization_knowledge", ("org-a",)) for a in ("content:read", "content:write")),
    )
    common = (
        *common,
        Grant(action="creator:read", resource_kind="creator_profile", selector="all_in_org"),
        Grant(action="creator:write", resource_kind="creator_profile", selector="all_in_org"),
    )
    root = await h.db.policy_assignments.find_one({"_id": h.root})
    ceiling = merged((*tuple(Grant.model_validate(x) for x in root["ceiling"]), *common))
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
                expires_at=now + timedelta(days=3),
            ),
            "fictional-independent-phase13-creator-ceiling",
        )
        h.root_generation = approved["generation"]
        await h.governance.operator_recover_corpus(
            "org-a",
            "owner",
            tuple(x for x in common if x.action != "reviews:decide"),
            now + timedelta(days=2),
            "fictional-independent-phase13-publisher",
        )
    for user in ("member", "reviewer"):
        assignment = await h.governance.issue(
            "org-a",
            "owner",
            Issue(
                subject_id=user,
                role="reviewer",
                parent_id=h.root,
                expected_parent_generation=h.root_generation,
                tuples=tuple(x for x in common if x.action != "content:publish"),
                expires_at=now + timedelta(days=2),
            ),
        )
        if user == "member":
            h.creator_member_assignment = assignment
    for name in CONTENT_COLLECTIONS.values():
        await h.db[name].update_many(
            {},
            {
                "$set": {
                    "embedding": [1.0, 0.0, 0.0],
                    "embedding_model": FixtureEmbeddings.model,
                    "embedding_dimensions": 3,
                    "embedding_generated_at": now,
                }
            },
        )
    for label in ("a", "b"):
        await h.db.content_items.update_many(
            {"org_id": "org-" + label},
            {"$set": {"body": "PRIVATE_CREATOR_" + label.upper() + " retry guidance"}},
        )
    for kind, name in CONTENT_COLLECTIONS.items():
        existing = await (await h.db[name].list_search_indexes()).to_list(None)
        if not any(i["name"] == CONTENT_TEXT_INDEXES[kind] for i in existing):
            await h.db[name].create_search_index(
                {"name": CONTENT_TEXT_INDEXES[kind], "definition": content_text_definition(kind)}
            )
        if not any(i["name"] == CONTENT_VECTOR_INDEXES[kind] for i in existing):
            await h.db[name].create_search_index(
                {
                    "name": CONTENT_VECTOR_INDEXES[kind],
                    "type": "vectorSearch",
                    "definition": content_vector_definition(kind, 3),
                }
            )
    for name in CONTENT_COLLECTIONS.values():
        for _ in range(240):
            indexes = await (await h.db[name].list_search_indexes()).to_list(None)
            if len(indexes) == 2 and all(i.get("queryable") for i in indexes):
                break
            await asyncio.sleep(0.25)
        else:
            raise AssertionError("real creator mongot indexes unavailable: " + name)
    return {"profile_id": PROFILE, "research_project_id": PROJECT}
