"""Declared mixed background corpus, fictional and without issued credentials.

Model validation and strict real MongoDB validators remain enabled. Repeated
texts/vectors establish throughput and isolation, never semantic relevance.
"""

import os
from pathlib import Path

import pytest_asyncio

from app.models.agent import AgentClientDocument
from app.models.common import utc_now
from app.models.content import ContentItemDocument, ContentVersionDocument
from app.models.memory import MemoryDocument
from app.models.project import ProjectDocument
from app.models.project_context import ProjectContextDocument
from app.models.research import KnowledgeCandidateDocument
from app.security.tenant import TenantContext, tenant_scope


@pytest_asyncio.fixture
async def performance_embeddings(managed_agents_db):
    import torch

    from app.services.embedding_service import build_embedding_service

    torch.set_num_threads(2)
    h = managed_agents_db
    settings = h.app.state.settings.model_copy(
        update={
            "embedding_enabled": True,
            "embedding_device": "cpu",
            "embedding_model_cache_directory": Path(os.environ["V6_ACCEPTANCE_MODEL_CACHE"]),
            "embedding_allow_downloads": False,
        }
    )
    embedder = build_embedding_service(settings)
    assert embedder is not None
    await embedder.start()
    h.app.state.settings = settings
    try:
        yield embedder
    finally:
        await embedder.close()


async def seed_reference_background(h, vectors, texts):
    result = {}
    now = utc_now()
    user = await h.db.users.find_one({"_id": "member"})
    member = await h.db.organization_memberships.find_one({"org_id": "org-a", "user_id": "member"})

    async def seed_org(org):
        users = await h.db.organization_memberships.distinct("user_id", {"org_id": org})
        for number in range(200 - len(users)):
            identifier = f"reference-person-{number:03d}"
            await h.db.users.update_one(
                {"_id": identifier},
                {
                    "$setOnInsert": {
                        **user,
                        "_id": identifier,
                        "display_name": f"Fictional شخص {number}",
                    }
                },
                upsert=True,
            )
            await h.db.user_identities.update_one(
                {"_id": "reference-local:" + identifier},
                {
                    "$setOnInsert": {
                        "_id": "reference-local:" + identifier,
                        "user_id": identifier,
                        "provider": "local",
                        "subject": identifier + "@example.test",
                        "verified_at": now,
                        "created_at": now,
                    }
                },
                upsert=True,
            )
            await h.db.organization_memberships.insert_one(
                {
                    **member,
                    "_id": f"{org}:{identifier}",
                    "org_id": org,
                    "user_id": identifier,
                    "display_name": f"Fictional شخص {number}",
                    "role": "contributor",
                    "bootstrap_owner": False,
                    "bootstrap_intent": None,
                }
            )
        teams = await h.db.teams.distinct("team_id", {"org_id": org})
        for number in range(20 - len(teams)):
            identifier = f"reference-team-{number:02d}"
            await h.db.teams.insert_one(
                {
                    "_id": f"{org}:{identifier}",
                    "org_id": org,
                    "team_id": identifier,
                    "name": f"Fictional team {number}",
                    "status": "active",
                    "revision": 1,
                    "generation": 1,
                    "created_at": now,
                }
            )
        teams = sorted(await h.db.teams.distinct("team_id", {"org_id": org}))
        users = sorted(await h.db.organization_memberships.distinct("user_id", {"org_id": org}))
        for index, identifier in enumerate(users):
            team = teams[index % len(teams)]
            await h.db.team_memberships.update_one(
                {"org_id": org, "team_id": team, "user_id": identifier},
                {
                    "$setOnInsert": {
                        "_id": f"{org}:{team}:{identifier}",
                        "org_id": org,
                        "team_id": team,
                        "user_id": identifier,
                        "status": "active",
                        "revision": 1,
                        "created_at": now,
                    }
                },
                upsert=True,
            )
        with tenant_scope(TenantContext(org)):
            projects = await h.db.projects.distinct("project_id", {"org_id": org})
            for number in range(100 - len(projects)):
                row = ProjectDocument(
                    project_id=f"reference-project-{number:03d}",
                    name=f"Fictional project {number}",
                    description="Synthetic capacity fixture",
                    repository="https://example.test/repo",
                    environment="disposable",
                )
                await h.db.projects.insert_one(row.model_dump(by_alias=True))
            projects = sorted(await h.db.projects.distinct("project_id", {"org_id": org}))
            clients = await h.db.agent_clients.distinct("client_id", {"org_id": org})
            for number in range(500 - len(clients)):
                row = AgentClientDocument(
                    client_id=f"reference-client-{number:03d}",
                    client_type="codex",
                    name=f"Fictional unissued client {number}",
                    owner_id="unverified-fixture",
                    scopes=[],
                    project_mode="none",
                    project_ids=[],
                    status="disabled",
                )
                await h.db.agent_clients.insert_one(row.model_dump(by_alias=True))
            clients = sorted(await h.db.agent_clients.distinct("client_id", {"org_id": org}))

            async def fill(collection, target, make):
                existing = await h.db[collection].count_documents({"org_id": org})
                for offset in range(0, target - existing, 500):
                    rows = [
                        make(i).model_dump(by_alias=True)
                        for i in range(offset, min(offset + 500, target - existing))
                    ]
                    await h.db[collection].insert_many(rows)

            def embedded(index):
                vector = vectors[org][index % len(vectors[org])]
                return dict(
                    embedding=list(vector.values),
                    embedding_model=vector.model,
                    embedding_dimensions=vector.dimensions,
                    embedding_generated_at=now,
                )

            await fill(
                "project_context",
                20000,
                lambda i: ProjectContextDocument(
                    project_id=projects[i % len(projects)],
                    type="implementation",
                    title=f"Fictional context {i}",
                    content=texts[org][i % 32],
                    importance=0.5,
                    source="synthetic-fixture",
                    external_id=f"reference-context-{i}",
                    **embedded(i),
                ),
            )

            def memory(index):
                scope = ("global", "project", "agent", "project_agent")[index % 4]
                return MemoryDocument(
                    scope=scope,
                    project_id=projects[index % len(projects)]
                    if scope in {"project", "project_agent"}
                    else None,
                    client_id=clients[index % len(clients)]
                    if scope in {"agent", "project_agent"}
                    else None,
                    type="fact",
                    content=(
                        f"PRIVATE_AGENT_SENTINEL_{org[-1].upper()} "
                        if scope in {"agent", "project_agent"}
                        else ""
                    )
                    + texts[org][index % 32],
                    importance=0.5,
                    confidence=0.5,
                    source="synthetic-fixture",
                    external_id=f"reference-memory-{index}",
                    **embedded(index),
                )

            await fill("memories", 20000, memory)
            count = await h.db.content_items.count_documents({"org_id": org})
            for offset in range(0, 5000 - count, 500):
                drafts, versions = [], []
                for index in range(offset, min(offset + 500, 5000 - count)):
                    project = projects[index % len(projects)]
                    common = dict(
                        profile_id="personal-brand",
                        content_id=f"reference-draft-{index}",
                        title=f"Fictional manual draft {index}",
                        body=texts[org][index % 32],
                        resource_project_ids=[project],
                    )
                    drafts.append(
                        ContentItemDocument(
                            **common,
                            platform="linkedin",
                            content_type="post",
                            project_ids=[project],
                            source_refs=[],
                            **embedded(index),
                        ).model_dump(by_alias=True)
                    )
                    versions.append(
                        ContentVersionDocument(**common, version=1, status="drafting").model_dump(
                            by_alias=True
                        )
                    )
                await h.db.content_items.insert_many(drafts)
                await h.db.content_versions.insert_many(versions)
            source = await h.db.research_sources.find_one({"org_id": org})
            await fill(
                "knowledge_candidates",
                1000,
                lambda i: KnowledgeCandidateDocument(
                    research_project_id=source["research_project_id"],
                    authorization=source["authorization"],
                    candidate_id=f"reference-candidate-{i}",
                    candidate_type="memory",
                    title=f"Fictional unreviewed candidate {i}",
                    content=texts[org][i % 32],
                    **embedded(i),
                ),
            )
        counts = {
            name: await h.db[name].count_documents({"org_id": org})
            for name in (
                "organization_memberships",
                "teams",
                "agent_clients",
                "projects",
                "project_context",
                "memories",
                "content_items",
                "knowledge_candidates",
            )
        }
        assert list(counts.values()) == [200, 20, 500, 100, 20000, 20000, 5000, 1000]
        result[org] = counts

    for org in ("org-a", "org-b"):
        await seed_org(org)
    return result
