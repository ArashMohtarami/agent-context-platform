"""Synthetic repeated-ID corpus + explicitly approved policies in disposable Mongo/mongot."""

import asyncio
from datetime import timedelta

from app.db.context_indexes import (
    CONTEXT_TEXT_INDEX,
    CONTEXT_VECTOR_INDEX,
    context_text_definition,
    context_vector_definition,
)
from app.db.memory_indexes import (
    MEMORY_TEXT_INDEX,
    MEMORY_VECTOR_INDEX,
    memory_text_definition,
    memory_vector_definition,
)
from app.db.text_indexes import SKILL_CHUNK_TEXT_INDEX, skill_chunk_text_definition
from app.db.vector_indexes import SKILL_CHUNK_VECTOR_INDEX, skill_chunk_vector_definition
from app.embeddings.types import EmbeddingVector
from app.identity.models import MANAGEMENT_ACTIONS
from app.management.memberships import Memberships
from app.management.models import RootApproval
from app.models.common import utc_now
from app.models.memory import MemoryDocument
from app.models.project_context import ProjectContextDocument
from app.models.skill import SkillDocument
from app.models.skill_chunk import SkillChunkDocument
from app.security.policy.models import Grant, Issue, Selector
from app.security.tenant import TenantContext, tenant_scope
from tests.v6_managed_agent_fixtures import g


class FixtureEmbeddings:
    """Deterministic 3D test embeddings; search itself uses real mongot, never mocks."""

    model = "deterministic-integration-fixture"
    dimensions = 3

    async def embed_query(self, text):
        return EmbeddingVector(self.model, self.dimensions, (1.0, 0.0, 0.0))

    async def embed_chunks(self, texts):
        return tuple(EmbeddingVector(self.model, self.dimensions, (1.0, 0.0, 0.0)) for _ in texts)


async def prepare_knowledge(h):
    now = utc_now()
    h.app.state.settings = h.app.state.settings.model_copy(update={"v6_console_enabled": True})
    h.app.state.embeddings = FixtureEmbeddings()
    # Recorded operator ceiling review invalidates earlier descendants. Issue
    # fresh bounded assignments; never change an existing grant behind the API.
    own = tuple(
        g(a, "member", ("member",))
        for a in (
            "clients:read:own",
            "clients:manage:own",
            "credentials:issue:own",
            "credentials:revoke:own",
        )
    )
    member = (
        g("projects:read", "project_directory", ("payment-service", "payments-api")),
        g("context:read"),
        g("context:write", ids=("payments-api",)),
        g("memory:read"),
        g("memory:write", ids=("payments-api",)),
        g("skills:read", "skill", ("retry-skill",)),
        g("search:read", ids=("payment-service", "payments-api")),
        g("search:read", "skill", ("retry-skill",)),
        *(
            g(a, "organization_knowledge", ("org-a",))
            for a in ("memory:read", "memory:write", "search:read")
        ),
    )
    member += tuple(
        g for g in h.domain if g.action in {"agent:self", "session:write", "audit:self:read"}
    )
    root = await h.db.policy_assignments.find_one({"_id": h.root})
    ceiling = tuple(Grant.model_validate(x) for x in root["ceiling"]) + member + own
    with tenant_scope(TenantContext("org-a")):
        result = await Memberships(h.engine).operator_approve_root(
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
                allowed_roles=("contributor", "team_lead", "reviewer"),
                expires_at=now + timedelta(days=5),
            ),
            "fictional-phase11-independent-ceiling-review",
        )
        h.root_generation = result["generation"]
        owner_corpus = (
            tuple(
                g(a, ids=("payment-service", "payments-api"))
                for a in (
                    "context:read",
                    "context:write",
                    "memory:read",
                    "memory:write",
                    "search:read",
                )
            )
            + tuple(
                g(a, "organization_knowledge", ("org-a",))
                for a in ("memory:read", "memory:write", "search:read")
            )
            + (
                g("skills:read", "skill", ("retry-skill",)),
                g("search:read", "skill", ("retry-skill",)),
            )
        )
        await h.governance.operator_recover_corpus(
            "org-a",
            "owner",
            owner_corpus,
            now + timedelta(days=2),
            "fictional-phase11-independent-data-use-review",
        )
    h.knowledge_grants = member
    h.sponsor_assignment = await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="member",
            parent_id=h.root,
            expected_parent_generation=h.root_generation,
            tuples=member + own,
            expires_at=now + timedelta(days=2),
            team_id="alpha",
            expected_team_generation=1,
        ),
    )
    # One verified global human belongs to both organizations. Fixture setup is
    # explicit operator provisioning, not an HTTP ownership claim or master key.
    owner = await h.db.organization_memberships.find_one({"org_id": "org-a", "user_id": "owner"})
    owner_b = {
        **owner,
        "_id": "knowledge-owner-b",
        "org_id": "org-b",
        "bootstrap_intent": {
            "state": "pending_phase5",
            "approval_reference": "fictional-phase11-b",
            "approved_at": now,
            "root_delegation": [
                g.model_dump() for g in member if g.resource_kind != "organization_knowledge"
            ],
            "bootstrap_policy": [
                {
                    "action": a,
                    "resource_kind": "project_directory",
                    "selector": "all_in_org",
                    "ids": [],
                }
                for a in ("projects:read", "projects:write")
            ],
            "further_self_grants": "independent_approval_or_documented_operator_recovery",
        },
    }
    if not await h.db.organization_memberships.find_one({"org_id": "org-b", "user_id": "owner"}):
        await h.db.organization_memberships.insert_one(owner_b)
    if not await h.db.organization_memberships.find_one({"org_id": "org-b", "user_id": "member"}):
        member_a = await h.db.organization_memberships.find_one(
            {"org_id": "org-a", "user_id": "member"}
        )
        await h.db.organization_memberships.insert_one(
            {**member_a, "_id": "knowledge-member-b", "org_id": "org-b", "role": "reviewer"}
        )
    # The prerequisite machine fixture seeded an empty org-B anchor only to
    # retire its legacy keys. Remove that test placeholder before real owner
    # bootstrap; it has no assignments or human traffic.
    assert await h.db.policy_assignments.count_documents({"org_id": "org-b"}) == 0
    await h.db.policy_anchors.delete_one({"_id": "org-b"})
    b = await h.governance.bootstrap(
        "org-b",
        "owner",
        targets=Selector(mode="include", ids=("member",)),
        allowed_roles=("reviewer",),
        expires_at=now + timedelta(days=5),
        approval="fictional-phase11-b-activation",
        approve_management_template=True,
    )
    await h.governance.issue(
        "org-b",
        "owner",
        Issue(
            subject_id="member",
            role="reviewer",
            parent_id=b["assignment_id"],
            expected_parent_generation=1,
            tuples=tuple(x for x in member if x.resource_kind != "organization_knowledge"),
            expires_at=now + timedelta(days=2),
        ),
    )
    for label in ("a", "b"):
        org = "org-" + label
        with tenant_scope(TenantContext(org)):
            original = await h.db.project_context.find_one(
                {"org_id": org, "project_id": "payment-service"}
            )
            clean = {
                k: v
                for k, v in original.items()
                if k not in {"_id", "identity_version", "legacy_adoption_id"}
            }
            for project_id, external_id in (
                ("payments-api", "write-only-context"),
                ("payment-service", "workspace-second"),
            ):
                row = ProjectContextDocument.model_validate(
                    {**clean, "project_id": project_id, "external_id": external_id}
                )
                await h.db.project_context.insert_one(row.model_dump(by_alias=True, mode="python"))
            for collection, model, field in (
                ("skills", SkillDocument, "version"),
                ("skill_chunks", SkillChunkDocument, "skill_version"),
            ):
                original = await h.db[collection].find_one({"org_id": org})
                clean = {
                    k: v
                    for k, v in original.items()
                    if k not in {"_id", "identity_version", "legacy_adoption_id"}
                }
                clean[field] = "0.9.0"
                if collection == "skills":
                    clean["status"] = "superseded"
                else:
                    clean["content"] = "HISTORICAL_SKILL_" + label.upper()
                row = model.model_validate(clean)
                await h.db[collection].insert_one(row.model_dump(by_alias=True, mode="python"))
            for scope in ("global", "agent", "project_agent"):
                row = MemoryDocument(
                    scope=scope,
                    project_id="payment-service" if scope == "project_agent" else None,
                    client_id="agent-one" if scope in {"agent", "project_agent"} else None,
                    type="decision",
                    content="PRIVATE_AGENT_SENTINEL_" + label.upper()
                    if scope != "global"
                    else "GLOBAL_SENTINEL_" + label.upper() + " isolation",
                    importance=0.7,
                    confidence=0.8,
                    source="fixture",
                    external_id="same-scope-memory",
                )
                await h.db.memories.insert_one(row.model_dump(by_alias=True, mode="python"))
        await h.db.project_context.update_many(
            {"org_id": org},
            {
                "$set": {
                    "title": "Context " + label.upper(),
                    "content": "PRIVATE_SENTINEL_"
                    + label.upper()
                    + (
                        " isolation\n\n**Safe bold**\n\n"
                        "<script>window.__knowledge_xss=1</script>\n\n"
                        "[Hostile link](javascript:alert(1))\n\n"
                        "![tracking](https://example.test/pixel)"
                    ),
                }
            },
        )
    for name in ("project_context", "memories", "skill_chunks"):
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
    definitions = (
        (
            "project_context",
            CONTEXT_TEXT_INDEX,
            context_text_definition(),
            CONTEXT_VECTOR_INDEX,
            context_vector_definition(3),
        ),
        (
            "memories",
            MEMORY_TEXT_INDEX,
            memory_text_definition(),
            MEMORY_VECTOR_INDEX,
            memory_vector_definition(3),
        ),
        (
            "skill_chunks",
            SKILL_CHUNK_TEXT_INDEX,
            skill_chunk_text_definition(),
            SKILL_CHUNK_VECTOR_INDEX,
            skill_chunk_vector_definition(3),
        ),
    )
    for name, text, text_def, vector, vector_def in definitions:
        existing = await (await h.db[name].list_search_indexes()).to_list(None)
        if not any(i["name"] == text for i in existing):
            await h.db[name].create_search_index({"name": text, "definition": text_def})
        if not any(i["name"] == vector for i in existing):
            await h.db[name].create_search_index(
                {"name": vector, "type": "vectorSearch", "definition": vector_def}
            )
    # All six indexes build together; waiting collection-by-collection before
    # creating the next pair needlessly serializes real mongot startup.
    for name, *_ in definitions:
        for _ in range(200):
            indexes = await (await h.db[name].list_search_indexes()).to_list(None)
            if len(indexes) == 2 and all(i.get("queryable") for i in indexes):
                break
            await asyncio.sleep(0.25)
        else:
            raise AssertionError("real mongot fixture indexes not ready: " + name)
    return h
