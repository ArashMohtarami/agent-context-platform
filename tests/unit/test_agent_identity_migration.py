"""V4 Phase 0 additive identity migration and index contracts."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.agent_indexes import AGENT_STANDARD_INDEX_DEFINITIONS
from app.db.agent_schema import AGENT_COLLECTION_VALIDATORS
from app.db.client_types import MongoDocument
from app.db.migrations.v022_agent_identity_schema import (
    V4_AGENT_COLLECTIONS,
    AgentIdentitySchemaMigration,
)
from app.db.migrations.v023_agent_identity_contracts import (
    AgentIdentityContractMigration,
    _session_validator_without_end_time,
)
from app.db.migrations.v029_agent_session_lifecycle import AgentSessionLifecycleMigration
from app.db.migrations.v030_agent_scope_validator import AgentScopeValidatorMigration
from app.db.schema import COLLECTION_VALIDATORS
from app.models.agent import AgentClientType, ProjectBindingType


@pytest.mark.asyncio
async def test_agent_identity_migration_creates_only_new_v4_collections() -> None:
    database = MagicMock()
    database.list_collection_names = AsyncMock(return_value=[])
    database.create_collection = AsyncMock()

    await AgentIdentitySchemaMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert [entry.args[0] for entry in database.create_collection.await_args_list] == list(
        V4_AGENT_COLLECTIONS
    )
    for collection, call_ in zip(
        V4_AGENT_COLLECTIONS, database.create_collection.await_args_list, strict=True
    ):
        assert call_.kwargs == {
            "validator": COLLECTION_VALIDATORS[collection],
            "validationLevel": "strict",
            "validationAction": "error",
        }


@pytest.mark.asyncio
async def test_agent_identity_migration_reconciles_existing_validators_without_rewriting_data() -> (
    None
):
    database = MagicMock()
    database.list_collection_names = AsyncMock(return_value=list(V4_AGENT_COLLECTIONS))
    database.command = AsyncMock()

    await AgentIdentitySchemaMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert [call_.args[0] for call_ in database.command.await_args_list] == [
        {
            "collMod": collection,
            "validator": COLLECTION_VALIDATORS[collection],
            "validationLevel": "strict",
            "validationAction": "error",
        }
        for collection in V4_AGENT_COLLECTIONS
    ]


def test_agent_schema_pins_v4_identity_contracts_and_no_credentials() -> None:
    assert set(AGENT_COLLECTION_VALIDATORS) == set(V4_AGENT_COLLECTIONS)
    client_properties = AGENT_COLLECTION_VALIDATORS["agent_clients"]["$jsonSchema"]["properties"]
    binding_properties = AGENT_COLLECTION_VALIDATORS["project_bindings"]["$jsonSchema"][
        "properties"
    ]

    assert client_properties["client_type"]["enum"] == [item.value for item in AgentClientType]
    assert binding_properties["binding_type"]["enum"] == [item.value for item in ProjectBindingType]
    assert "key_hash" not in client_properties
    assert "token" not in client_properties


def test_agent_identity_indexes_cover_lookup_and_activity_history() -> None:
    assert set(AGENT_STANDARD_INDEX_DEFINITIONS) == set(V4_AGENT_COLLECTIONS)
    names = {
        collection: {definition.document["name"] for definition in definitions}
        for collection, definitions in AGENT_STANDARD_INDEX_DEFINITIONS.items()
    }

    assert {"agent_client_id_uq", "agent_client_status_last_seen"} <= names["agent_clients"]
    assert {"agent_session_id_uq", "agent_session_project_status_last_seen"} <= names[
        "agent_sessions"
    ]
    assert {
        "project_binding_id_uq",
        "project_binding_lookup",
        "project_binding_mapping_uq",
        "project_binding_repository_locator_uq",
    } <= names["project_bindings"]
    assert {"agent_activity_event_id_uq", "agent_activity_tool_status_created"} <= names[
        "agent_activity"
    ]


@pytest.mark.asyncio
async def test_agent_contract_migration_backfills_session_end_time() -> None:
    database = MagicMock()
    database.command = AsyncMock()
    sessions = MagicMock()
    sessions.update_many = AsyncMock()
    bindings = MagicMock()
    bindings.create_indexes = AsyncMock()

    def collection_for(name: str) -> MagicMock:
        return {"agent_sessions": sessions, "project_bindings": bindings}[name]

    database.__getitem__.side_effect = collection_for

    await AgentIdentityContractMigration().up(cast(AsyncDatabase[MongoDocument], database))

    intermediate = _session_validator_without_end_time()["$jsonSchema"]
    assert "ended_at" not in intermediate["required"]
    sessions.update_many.assert_awaited_once_with(
        {"ended_at": {"$exists": False}}, {"$set": {"ended_at": None}}
    )
    commands = [entry.args[0] for entry in database.command.await_args_list]
    assert commands[0]["validator"] == _session_validator_without_end_time()
    assert [command["collMod"] for command in commands[1:]] == list(V4_AGENT_COLLECTIONS)
    bindings.create_indexes.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_session_lifecycle_migration_reconciles_only_its_validator() -> None:
    database = MagicMock()
    database.command = AsyncMock()

    await AgentSessionLifecycleMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert database.command.await_args.args[0] == {
        "collMod": "agent_sessions",
        "validator": AGENT_COLLECTION_VALIDATORS["agent_sessions"],
        "validationLevel": "strict",
        "validationAction": "error",
    }


@pytest.mark.asyncio
async def test_agent_scope_migration_reconciles_the_existing_client_validator() -> None:
    database = MagicMock()
    database.command = AsyncMock()

    await AgentScopeValidatorMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert database.command.await_args.args[0] == {
        "collMod": "agent_clients",
        "validator": AGENT_COLLECTION_VALIDATORS["agent_clients"],
        "validationLevel": "strict",
        "validationAction": "error",
    }
