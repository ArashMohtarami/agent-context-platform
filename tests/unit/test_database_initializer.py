"""Collection validation and database initialization tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_profile_schema import CLIENT_PROFILE_COLLECTION
from app.db.client_types import MongoDocument
from app.db.collections import ensure_collections
from app.db.health import MONGOT_PROBE_COLLECTION
from app.db.idempotency_schema import IDEMPOTENCY_COLLECTION
from app.db.initializer import initialize_database
from app.db.migrations.runner import MIGRATIONS
from app.db.migrations.v002_skill_chunk_embeddings import SkillChunkEmbeddingMigration
from app.db.migrations.v003_project_context_embeddings import (
    PREVIOUS_VALIDATOR as PREVIOUS_CONTEXT_VALIDATOR,
)
from app.db.migrations.v003_project_context_embeddings import (
    ProjectContextEmbeddingMigration,
)
from app.db.migrations.v004_memory_records import (
    PREVIOUS_VALIDATOR as PREVIOUS_MEMORY_VALIDATOR,
)
from app.db.migrations.v004_memory_records import (
    MemoryRecordMigration,
)
from app.db.migrations.v006_creator_content_schema import (
    V2_CONTENT_COLLECTIONS,
    CreatorContentSchemaMigration,
)
from app.db.migrations.v007_creator_profile_access import CreatorProfileAccessMigration
from app.db.migrations.v008_content_audit_action import ContentAuditActionMigration
from app.db.migrations.v009_content_retrieval_embeddings import (
    EMBEDDABLE_CONTENT_COLLECTIONS,
    ContentRetrievalEmbeddingMigration,
)
from app.db.migrations.v010_content_approval_audit_action import ContentApprovalAuditActionMigration
from app.db.migrations.v012_content_metric_source_metadata import ContentMetricSourceMigration
from app.db.migrations.v013_research_schema import (
    V3_RESEARCH_COLLECTIONS,
    ResearchSchemaMigration,
)
from app.db.migrations.v014_research_project_auth import ResearchProjectAuthorizationMigration
from app.db.migrations.v015_research_source_registry import ResearchSourceRegistryMigration
from app.db.migrations.v016_research_brief_structure import ResearchBriefStructureMigration
from app.db.migrations.v019_skill_update_proposals import SKILL_UPDATE_PROPOSAL_COLLECTION
from app.db.migrations.v022_agent_identity_schema import V4_AGENT_COLLECTIONS
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_missing_collections_are_created_with_strict_validation() -> None:
    database = MagicMock()
    database.list_collection_names = AsyncMock(return_value=["skills"])
    database.create_collection = AsyncMock()

    await ensure_collections(cast(AsyncDatabase[MongoDocument], database))

    created_names = {entry.args[0] for entry in database.create_collection.await_args_list}
    # Every application collection that was missing, plus the coordinator
    # probe's, which is not an application collection and carries no validator:
    # it holds nothing and exists only so that listing its search indexes has
    # to reach `mongot`.
    assert created_names == (set(COLLECTION_VALIDATORS) - {"skills"}) | {MONGOT_PROBE_COLLECTION}
    for entry in database.create_collection.await_args_list:
        if entry.args[0] == MONGOT_PROBE_COLLECTION:
            assert entry.kwargs == {}
            continue
        assert entry.kwargs["validator"] == COLLECTION_VALIDATORS[entry.args[0]]
        assert entry.kwargs["validationLevel"] == "strict"
        assert entry.kwargs["validationAction"] == "error"


@pytest.mark.asyncio
async def test_database_initialization_runs_migrations_then_reconciles_indexes() -> None:
    database = MagicMock()
    with (
        patch("app.db.initializer.run_migrations", new=AsyncMock()) as run_migrations,
        patch("app.db.initializer.ensure_indexes", new=AsyncMock()) as ensure_indexes,
    ):
        await initialize_database(cast(AsyncDatabase[MongoDocument], database))

    run_migrations.assert_awaited_once_with(database)
    ensure_indexes.assert_awaited_once_with(database)


def test_validators_cover_only_the_planned_collections() -> None:
    assert set(COLLECTION_VALIDATORS) == {
        "skills",
        "skill_chunks",
        "projects",
        "project_context",
        "memories",
        "conversations",
        "retrieval_logs",
        # Phase 15 adds the credentials and the record of what they did.
        "api_keys",
        "audit_logs",
        *V2_CONTENT_COLLECTIONS,
        *V3_RESEARCH_COLLECTIONS,
        SKILL_UPDATE_PROPOSAL_COLLECTION,
        *V4_AGENT_COLLECTIONS,
        CLIENT_PROFILE_COLLECTION,
        IDEMPOTENCY_COLLECTION,
    }


def test_migrations_are_ordered_and_uniquely_versioned() -> None:
    versions = [migration.version for migration in MIGRATIONS]

    assert versions == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
        31,
        32,
        33,
        34,
        35,
        36,
        37,
        38,
        39,
        40,
        41,
    ]
    assert len(set(versions)) == len(versions)
    assert all(migration.description for migration in MIGRATIONS)


@pytest.mark.asyncio
async def test_research_project_authorization_migration_reconciles_only_security_validators() -> (
    None
):
    database = MagicMock()
    database.command = AsyncMock()

    await ResearchProjectAuthorizationMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert [call.args[0]["collMod"] for call in database.command.await_args_list] == [
        "api_keys",
        "audit_logs",
    ]


@pytest.mark.asyncio
async def test_research_source_registry_migration_reconciles_only_source_validator() -> None:
    database = MagicMock()
    database.command = AsyncMock()

    await ResearchSourceRegistryMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert database.command.await_args.args[0] == {
        "collMod": "research_sources",
        "validator": COLLECTION_VALIDATORS["research_sources"],
        "validationLevel": "strict",
        "validationAction": "error",
    }


@pytest.mark.asyncio
async def test_research_brief_structure_migration_backfills_only_brief_fields() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)
    database.command = AsyncMock()

    await ResearchBriefStructureMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert collection.update_many.await_count == 4
    assert database.command.await_args.args[0]["collMod"] == "research_briefs"


@pytest.mark.asyncio
async def test_content_metric_source_migration_only_reconciles_metric_fields() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await ContentMetricSourceMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert database.command.await_count == 2
    assert collection.update_many.await_count == 2


@pytest.mark.asyncio
async def test_content_approval_migration_only_reconciles_the_audit_validator() -> None:
    database = MagicMock()
    database.command = AsyncMock()

    await ContentApprovalAuditActionMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert database.command.await_args.args[0] == {
        "collMod": "audit_logs",
        "validator": COLLECTION_VALIDATORS["audit_logs"],
        "validationLevel": "strict",
        "validationAction": "error",
    }


@pytest.mark.asyncio
async def test_the_embedding_migration_widens_the_validator_then_backfills() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await SkillChunkEmbeddingMigration().up(cast(AsyncDatabase[MongoDocument], database))

    command = database.command.await_args.args[0]
    assert command["collMod"] == "skill_chunks"
    assert command["validator"] == COLLECTION_VALIDATORS["skill_chunks"]
    assert command["validationLevel"] == "strict"
    assert command["validationAction"] == "error"
    collection.update_many.assert_awaited_once_with(
        {"embedding": {"$exists": False}},
        {
            "$set": {
                "embedding": None,
                "embedding_model": None,
                "embedding_dimensions": None,
                "embedding_generated_at": None,
            }
        },
    )


@pytest.mark.asyncio
async def test_the_embedding_migration_rolls_back_to_the_previous_validator() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await SkillChunkEmbeddingMigration().down(cast(AsyncDatabase[MongoDocument], database))

    command = database.command.await_args.args[0]
    schema = command["validator"]["$jsonSchema"]
    assert "embedding" not in schema["properties"]
    assert "embedding" not in schema["required"]
    assert command["validationAction"] == "error"
    collection.update_many.assert_awaited_once_with(
        {},
        {
            "$unset": {
                "embedding": "",
                "embedding_model": "",
                "embedding_dimensions": "",
                "embedding_generated_at": "",
            }
        },
    )


@pytest.mark.asyncio
async def test_the_context_migration_widens_the_validator_then_backfills() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await ProjectContextEmbeddingMigration().up(cast(AsyncDatabase[MongoDocument], database))

    command = database.command.await_args.args[0]
    assert command["collMod"] == "project_context"
    assert command["validator"] == COLLECTION_VALIDATORS["project_context"]
    assert command["validationLevel"] == "strict"
    assert command["validationAction"] == "error"
    collection.update_many.assert_awaited_once_with(
        {"embedding": {"$exists": False}},
        {
            "$set": {
                "embedding": None,
                "embedding_model": None,
                "embedding_dimensions": None,
                "embedding_generated_at": None,
            }
        },
    )


@pytest.mark.asyncio
async def test_the_context_migration_rolls_back_without_losing_authored_content() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await ProjectContextEmbeddingMigration().down(cast(AsyncDatabase[MongoDocument], database))

    command = database.command.await_args.args[0]
    assert command["validator"] == PREVIOUS_CONTEXT_VALIDATOR
    assert "embedding" not in command["validator"]["$jsonSchema"]["properties"]
    # Only the derived vector is removed; the authored fields stay.
    unset = collection.update_many.await_args.args[1]["$unset"]
    assert set(unset) == {
        "embedding",
        "embedding_model",
        "embedding_dimensions",
        "embedding_generated_at",
    }


@pytest.mark.asyncio
async def test_the_memory_migration_widens_the_validator_then_backfills() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await MemoryRecordMigration().up(cast(AsyncDatabase[MongoDocument], database))

    command = database.command.await_args.args[0]
    assert command["collMod"] == "memories"
    assert command["validator"] == COLLECTION_VALIDATORS["memories"]
    assert command["validationLevel"] == "strict"
    backfilled = collection.update_many.await_args.args[1]["$set"]
    assert set(backfilled) == {
        "embedding",
        "embedding_model",
        "embedding_dimensions",
        "embedding_generated_at",
        "summary",
        "supersedes",
        "superseded_by",
    }
    assert set(backfilled.values()) == {None}


@pytest.mark.asyncio
async def test_the_memory_migration_rolls_back_without_losing_a_memory() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await MemoryRecordMigration().down(cast(AsyncDatabase[MongoDocument], database))

    command = database.command.await_args.args[0]
    assert command["validator"] == PREVIOUS_MEMORY_VALIDATOR
    previous = command["validator"]["$and"][0]["$jsonSchema"]["properties"]
    assert "embedding" not in previous
    assert "superseded_by" not in previous
    # Content and status survive: a superseded memory stays superseded.
    unset = collection.update_many.await_args.args[1]["$unset"]
    assert "content" not in unset
    assert "status" not in unset


@pytest.mark.asyncio
async def test_v2_migration_creates_missing_collections_and_reconciles_existing_validators() -> (
    None
):
    database = MagicMock()
    database.list_collection_names = AsyncMock(return_value=["content_items"])
    database.create_collection = AsyncMock()
    database.command = AsyncMock()

    await CreatorContentSchemaMigration().up(cast(AsyncDatabase[MongoDocument], database))

    created = {entry.args[0] for entry in database.create_collection.await_args_list}
    assert created == set(V2_CONTENT_COLLECTIONS) - {"content_items"}
    for entry in database.create_collection.await_args_list:
        assert entry.kwargs["validator"] == COLLECTION_VALIDATORS[entry.args[0]]
        assert entry.kwargs["validationLevel"] == "strict"
        assert entry.kwargs["validationAction"] == "error"

    changed = {entry.args[0]["collMod"] for entry in database.command.await_args_list}
    assert changed == {"content_items"}


@pytest.mark.asyncio
async def test_creator_profile_access_migration_backfills_defaults_and_reconciles_validators() -> (
    None
):
    api_keys = MagicMock()
    profiles = MagicMock()
    api_keys.update_many = AsyncMock()
    profiles.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__.side_effect = {
        "api_keys": api_keys,
        "creator_profiles": profiles,
    }.__getitem__

    await CreatorProfileAccessMigration().up(cast(AsyncDatabase[MongoDocument], database))

    api_keys.update_many.assert_awaited_once_with(
        {"profiles": {"$exists": False}}, {"$set": {"profiles": []}}
    )
    profiles.update_many.assert_awaited_once_with(
        {"tone_preferences": {"$exists": False}}, {"$set": {"tone_preferences": []}}
    )
    changed = {entry.args[0]["collMod"] for entry in database.command.await_args_list}
    assert changed == {"api_keys", "audit_logs", "creator_profiles"}


@pytest.mark.asyncio
async def test_content_audit_migration_reconciles_the_existing_audit_collection() -> None:
    database = MagicMock()
    database.command = AsyncMock()

    await ContentAuditActionMigration().up(cast(AsyncDatabase[MongoDocument], database))

    database.command.assert_awaited_once_with(
        {
            "collMod": "audit_logs",
            "validator": COLLECTION_VALIDATORS["audit_logs"],
            "validationLevel": "strict",
            "validationAction": "error",
        }
    )


@pytest.mark.asyncio
async def test_content_retrieval_embedding_migration_backfills_current_documents_only() -> None:
    collections = {name: MagicMock() for name in EMBEDDABLE_CONTENT_COLLECTIONS}
    for collection in collections.values():
        collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__.side_effect = collections.__getitem__

    await ContentRetrievalEmbeddingMigration().up(cast(AsyncDatabase[MongoDocument], database))

    for name, collection in collections.items():
        collection.update_many.assert_awaited_once_with(
            {"embedding": {"$exists": False}},
            {
                "$set": {
                    "embedding": None,
                    "embedding_model": None,
                    "embedding_dimensions": None,
                    "embedding_generated_at": None,
                }
            },
        )
        assert any(
            call.args[0]["collMod"] == name
            and call.args[0]["validator"] == COLLECTION_VALIDATORS[name]
            for call in database.command.await_args_list
        )
    assert "content_versions" not in collections


@pytest.mark.asyncio
async def test_v3_schema_migration_creates_only_missing_research_collections() -> None:
    database = MagicMock()
    database.list_collection_names = AsyncMock(return_value=["research_sources"])
    database.create_collection = AsyncMock()
    database.command = AsyncMock()

    await ResearchSchemaMigration().up(cast(AsyncDatabase[MongoDocument], database))

    created = {entry.args[0] for entry in database.create_collection.await_args_list}
    assert created == set(V3_RESEARCH_COLLECTIONS) - {"research_sources"}
    for entry in database.create_collection.await_args_list:
        assert entry.kwargs["validator"] == COLLECTION_VALIDATORS[entry.args[0]]
        assert entry.kwargs["validationLevel"] == "strict"
        assert entry.kwargs["validationAction"] == "error"
    database.command.assert_awaited_once_with(
        {
            "collMod": "research_sources",
            "validator": COLLECTION_VALIDATORS["research_sources"],
            "validationLevel": "strict",
            "validationAction": "error",
        }
    )
