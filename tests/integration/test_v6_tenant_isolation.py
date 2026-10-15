"""Real MongoDB/mongot tenant gates with matching natural identifiers."""

import asyncio

import pytest
from pymongo.errors import OperationFailure

from app.db.tenant_collection import TenantCollection
from app.db.tenant_schema import TENANT_COLLECTIONS
from app.models.common import InvalidCursorError
from app.repositories.project_repository import MongoProjectRepository
from app.security.tenant import TenantContext, TenantContextRequired, tenant_scope

pytestmark = pytest.mark.integration


async def test_every_collection_crud_counts_distinct_and_foreign_ids(isolated_database):
    db = isolated_database
    control = await db["_tenant_control"].find_one({})
    with tenant_scope(TenantContext("org-a", control["cursor_secret"])):
        for name in TENANT_COLLECTIONS:
            adapter = TenantCollection(db[name], name, lambda: TenantContext("org-a"))
            ours = await adapter.find({}).to_list(length=100)
            assert ours and all(row["org_id"] == "org-a" for row in ours), name
            assert await adapter.count_documents({}) == len(ours), name
            assert await adapter.distinct("org_id") == ["org-a"], name
            foreign = await db[name].find_one({"org_id": "org-b"})
            assert await adapter.find_one({"_id": foreign["_id"]}) is None, name
            assert (
                await adapter.update_one({"_id": foreign["_id"]}, {"$set": {"revision": 9}})
            ).matched_count == 0
            assert (await adapter.delete_one({"_id": foreign["_id"]})).deleted_count == 0
            assert (await adapter.delete_many({"$or": [{"org_id": "org-b"}]})).deleted_count == 0
            assert await db[name].find_one({"_id": foreign["_id"]}) == foreign
        projects = MongoProjectRepository(db)
        first = await projects.list(limit=1)
        assert first.next_cursor
    with tenant_scope(TenantContext("org-b", control["cursor_secret"])):
        with pytest.raises(InvalidCursorError):
            await MongoProjectRepository(db).list(limit=1, cursor=first.next_cursor)
    adapter = TenantCollection(db["projects"], "projects", lambda: TenantContext("org-a"))
    with pytest.raises(TenantContextRequired):
        await adapter.count_documents({})
    with pytest.raises(OperationFailure):
        await db["_tenant_control"].update_one({}, {"$set": {"worker_schema_versions": [36, 37]}})


async def test_real_mongot_all_production_indexes_have_tenant_candidate_filters(isolated_database):
    from app.db.content_indexes import (
        CONTENT_COLLECTIONS,
        content_text_definition,
        content_vector_definition,
    )
    from app.db.context_indexes import context_text_definition, context_vector_definition
    from app.db.memory_indexes import memory_text_definition, memory_vector_definition
    from app.db.research_indexes import (
        RESEARCH_SEARCH_COLLECTIONS,
        research_text_definition,
        research_vector_definition,
    )
    from app.db.tenant_inventory import MODEL_BY_COLLECTION
    from app.db.text_indexes import skill_chunk_text_definition
    from app.db.vector_indexes import skill_chunk_vector_definition
    from app.models.common import utc_now
    from app.security.tenant_policy import ResourceGrant, TenantPolicy, policy_scope

    db = isolated_database
    definitions = [
        ("project_context", "content", context_text_definition(), context_vector_definition(3)),
        ("memories", "content", memory_text_definition(), memory_vector_definition(3)),
        (
            "skill_chunks",
            "content",
            skill_chunk_text_definition(),
            skill_chunk_vector_definition(3),
        ),
    ]
    content_fields = {"idea": "description", "brief": "objective", "item": "body"}
    research_fields = {
        "chunk": "content",
        "note": "body",
        "claim": "statement",
        "brief": "summary",
        "candidate": "content",
    }
    definitions.extend(
        (
            name,
            content_fields[kind],
            content_text_definition(kind),
            content_vector_definition(kind, 3),
        )
        for kind, name in CONTENT_COLLECTIONS.items()
    )
    definitions.extend(
        (
            name,
            research_fields[kind],
            research_text_definition(kind),
            research_vector_definition(kind, 3),
        )
        for kind, name in RESEARCH_SEARCH_COLLECTIONS.items()
    )
    assert len(definitions) == 11
    for name, field, text_definition, vector_definition in definitions:
        for label in ("a", "b"):
            await db[name].update_many(
                {"org_id": "org-" + label},
                {
                    "$set": {
                        field: "PRIVATE_SENTINEL_" + label.upper() + " retry isolation",
                        "embedding": [1.0, 0.0, 0.0],
                        "embedding_dimensions": 3,
                        "embedding_model": "deterministic-integration-fixture",
                        "embedding_generated_at": utc_now(),
                    }
                },
            )
        await db[name].create_search_index({"name": "v6_text", "definition": text_definition})
        await db[name].create_search_index(
            {"name": "v6_vector", "type": "vectorSearch", "definition": vector_definition}
        )
    for name, field, _, _ in definitions:
        collection = db[name]
        for _ in range(120):
            indexes = await (await collection.list_search_indexes()).to_list(length=None)
            if len(indexes) == 2 and all(index.get("queryable") for index in indexes):
                break
            await asyncio.sleep(0.5)
        else:
            pytest.fail("real mongot production indexes not queryable: " + name)
        for label in ("a", "b"):
            org = "org-" + label
            policy = TenantPolicy(
                org,
                "fixture-key",
                (
                    ResourceGrant(
                        "search:read", "project", "include", ("payment-service", "payments-api")
                    ),
                    ResourceGrant("search:read", "creator_profile", "include", ("personal-brand",)),
                ),
            )
            with tenant_scope(TenantContext(org)), policy_scope(policy, "search:read"):
                adapter = TenantCollection(collection, name, lambda org=org: TenantContext(org))
                for pipeline in (
                    [
                        {
                            "$search": {
                                "index": "v6_text",
                                "compound": {
                                    "must": [{"text": {"path": field, "query": "isolation"}}]
                                },
                            }
                        },
                        {"$limit": 1},
                    ],
                    [
                        {
                            "$vectorSearch": {
                                "index": "v6_vector",
                                "path": "embedding",
                                "queryVector": [1.0, 0.0, 0.0],
                                "numCandidates": 10,
                                "limit": 1,
                            }
                        }
                    ],
                ):
                    for _ in range(60):
                        rows = await (await adapter.aggregate(pipeline)).to_list(length=10)
                        if rows:
                            break
                        await asyncio.sleep(0.25)
                    assert rows and len(rows) == 1, name
                    assert rows[0]["org_id"] == org, name
                    MODEL_BY_COLLECTION[name].model_validate(
                        rows[0], context={"from_storage": True}
                    )
                    assert "PRIVATE_SENTINEL_" + label.upper() in rows[0][field], name
                    if name != "skill_chunks":
                        denied = TenantPolicy(
                            org,
                            "fixture-key",
                            (
                                ResourceGrant("search:read", "project", "none"),
                                ResourceGrant(
                                    "search:read", "creator_profile", "include", ("personal-brand",)
                                ),
                            ),
                        )
                        with policy_scope(denied, "search:read"):
                            assert (
                                await (await adapter.aggregate(pipeline)).to_list(length=10) == []
                            ), name


async def test_live_resource_denials_joins_references_and_org_global_memory(isolated_database):
    from app.models.content import ContentLinkDocument
    from app.models.memory import MemoryDocument
    from app.repositories.content_repository import MongoContentLinkRepository
    from app.repositories.memory_repository import MongoMemoryRepository
    from app.repositories.skill_repository import MongoSkillRepository
    from app.security.tenant_policy import ResourceGrant, TenantPolicy, policy_scope

    db = isolated_database
    for label in ("a", "b"):
        org = "org-" + label
        with tenant_scope(TenantContext(org)):
            await MongoMemoryRepository(db, project_id=None).create(
                MemoryDocument(
                    scope="global",
                    external_id="same-global-external-id",
                    type="decision",
                    content="ORG_GLOBAL_PRIVATE_" + label.upper(),
                    importance=0.7,
                    confidence=0.9,
                    source="human",
                )
            )
            matches = await MongoSkillRepository(db).search_active_lexical(
                "PRIVATE_SENTINEL_" + label.upper()
            )
            assert matches and all(match.skill.org_id == org for match in matches)
            assert all(chunk.org_id == org for match in matches for chunk in match.matched_chunks)
            foreign = "B" if label == "a" else "A"
            assert (
                await MongoSkillRepository(db).search_active_lexical("PRIVATE_SENTINEL_" + foreign)
                == ()
            )
        policy = TenantPolicy(
            org,
            "key",
            (
                ResourceGrant("memory:read", "project", "none"),
                ResourceGrant("content:write", "creator_profile", "all_in_org"),
            ),
        )
        with tenant_scope(TenantContext(org)), policy_scope(policy, "memory:read"):
            page = await MongoMemoryRepository(db, project_id=None).list()
            assert (
                len(page.items) == 1
                and page.items[0].content == "ORG_GLOBAL_PRIVATE_" + label.upper()
            )
    foreign_memory = await db["memories"].find_one({"org_id": "org-b", "scope": "global"})
    with tenant_scope(TenantContext("org-a")):
        with pytest.raises(ValueError, match="reference"):
            await MongoContentLinkRepository(db, "personal-brand").create(
                ContentLinkDocument(
                    profile_id="personal-brand",
                    link_id="foreign-ref",
                    content_id="payment-idempotency-linkedin-post",
                    source_type="memory",
                    source_id=str(foreign_memory["_id"]),
                    relation="derived_from",
                )
            )
    # An empty project grant must not turn into ALL, even for a direct count.
    with (
        tenant_scope(TenantContext("org-a")),
        policy_scope(
            TenantPolicy("org-a", "key", (ResourceGrant("context:read", "project", "none"),)),
            "context:read",
        ),
    ):
        adapter = TenantCollection(
            db["project_context"], "project_context", lambda: TenantContext("org-a")
        )
        assert await adapter.count_documents({}) == 0


async def test_idempotency_uses_org_and_rechecks_effective_authority(isolated_database):
    from app.repositories.idempotency_repository import MongoGatewayIdempotencyRepository
    from app.security.tenant_policy import ResourceGrant, TenantPolicy, policy_scope
    from app.services.gateway_idempotency_service import (
        GatewayIdempotencyService,
        IdempotencyConflictError,
    )

    db = isolated_database
    for label in ("a", "b"):
        org = "org-" + label
        policy = TenantPolicy(
            org,
            "same-subject-label",
            (ResourceGrant("memory:write", "project", "all_in_org"),),
            client_id="agent-one",
        )
        with tenant_scope(TenantContext(org)), policy_scope(policy, "memory:write"):
            service = GatewayIdempotencyService(
                MongoGatewayIdempotencyRepository(db), retention_seconds=120
            )
            options = dict(
                client_id="agent-one",
                operation="save_memory",
                idempotency_key="same-retry",
                payload_hash="a" * 64,
            )
            reservation = await service.acquire(**options)
            assert not reservation.replay
            await service.complete(
                reservation.document,
                result_text="PRIVATE_" + label.upper(),
                result_data={"org_result": label},
            )
            replay = await service.acquire(**options)
            assert replay.replay and replay.document.result_text == "PRIVATE_" + label.upper()
        reduced = TenantPolicy(
            org,
            "same-subject-label",
            (ResourceGrant("memory:write", "project", "none"),),
            client_id="agent-one",
        )
        with tenant_scope(TenantContext(org)), policy_scope(reduced, "memory:write"):
            with pytest.raises(IdempotencyConflictError, match="authority"):
                await GatewayIdempotencyService(
                    MongoGatewayIdempotencyRepository(db), retention_seconds=120
                ).acquire(**options)


async def test_operator_cutover_rehearsal_keeps_additional_org_sealed(isolated_database):
    import os
    from pathlib import Path

    from v6_isolation_fixtures import token

    from app.repositories.credential_repository import MongoCredentialResolver
    from app.security.principal import AuthenticationError
    from app.services.auth_service import AuthService
    from app.services.tenant_isolation_service import TenantIsolationService

    unit = Path(os.environ["V6_UNIT_EVIDENCE_XML"])
    real = Path(os.environ["V6_REAL_EVIDENCE_XML"])
    db = isolated_database
    await db["_tenant_control"].update_one(
        {}, {"$set": {"stage": "isolating", "domain_traffic_enabled": False}}
    )
    await db["organizations"].update_many({}, {"$set": {"status": "sealed"}})
    with tenant_scope(TenantContext("org-a")):
        result = await TenantIsolationService(db).activate(
            writers_stopped=True, worker_schema_versions=[37], evidence=[unit, real]
        )
        assert result["domain_traffic_enabled"]
    assert (await db["organizations"].find_one({"org_id": "org-b"}))["status"] == "sealed"
    await AuthService(MongoCredentialResolver(db)).authenticate(token("a"))
    with pytest.raises(AuthenticationError):
        await AuthService(MongoCredentialResolver(db)).authenticate(token("b"))
    with pytest.raises(RuntimeError, match="scoped"):
        from app.db.initializer import initialize_database

        await initialize_database(db, tenant_foundation=True)
