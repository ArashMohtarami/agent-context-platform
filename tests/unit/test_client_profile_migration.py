"""V5 Phase 0 migration and strict collection contract tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.agent_schema import (
    AGENT_COLLECTION_VALIDATORS,
    agent_client_validator_without_profile_id,
)
from app.db.client_profile_indexes import CLIENT_PROFILE_STANDARD_INDEX_DEFINITIONS
from app.db.client_profile_schema import (
    CLIENT_PROFILE_COLLECTION,
    CLIENT_PROFILE_COLLECTION_VALIDATORS,
    client_profile_validator_without_description_and_status,
)
from app.db.client_types import MongoDocument
from app.db.migrations.v033_client_profiles import ClientProfileMigration
from app.db.migrations.v034_client_profile_lifecycle import ClientProfileLifecycleMigration
from app.db.schema import COLLECTION_VALIDATORS
from app.models.agent import AgentClientType
from app.models.client_profile import DEFAULT_CLIENT_PROFILE_DESCRIPTION


def test_v5_client_profile_schema_is_generic_and_extends_existing_client_types() -> None:
    client_schema = AGENT_COLLECTION_VALIDATORS["agent_clients"]["$jsonSchema"]
    profile_schema = CLIENT_PROFILE_COLLECTION_VALIDATORS[CLIENT_PROFILE_COLLECTION]["$jsonSchema"]

    assert AgentClientType.GEMINI_CLI.value in client_schema["properties"]["client_type"]["enum"]
    assert client_schema["properties"]["profile_id"]["bsonType"] == ["string", "null"]
    assert "profile_id" in client_schema["required"]
    assert profile_schema["properties"]["scopes"]["items"]["enum"]
    assert {"description", "status"}.issubset(profile_schema["required"])
    assert profile_schema["properties"]["rate_limit_profile"]["bsonType"] == ["string", "null"]
    assert "token" not in profile_schema["properties"]
    assert {
        index.document["name"]
        for index in CLIENT_PROFILE_STANDARD_INDEX_DEFINITIONS[CLIENT_PROFILE_COLLECTION]
    } == {"client_profile_id_uq", "client_profile_updated"}


@pytest.mark.asyncio
async def test_client_profile_migration_backfills_v4_clients_and_creates_collection() -> None:
    clients = MagicMock()
    clients.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.list_collection_names = AsyncMock(return_value=[])
    database.create_collection = AsyncMock()
    database.__getitem__.return_value = clients

    await ClientProfileMigration().up(cast(AsyncDatabase[MongoDocument], database))

    commands = [call.args[0] for call in database.command.await_args_list]
    assert commands == [
        {
            "collMod": "agent_clients",
            "validator": agent_client_validator_without_profile_id(),
            "validationLevel": "strict",
            "validationAction": "error",
        },
        {
            "collMod": "agent_clients",
            "validator": AGENT_COLLECTION_VALIDATORS["agent_clients"],
            "validationLevel": "strict",
            "validationAction": "error",
        },
    ]
    clients.update_many.assert_awaited_once_with(
        {"profile_id": {"$exists": False}}, {"$set": {"profile_id": None}}
    )
    database.create_collection.assert_awaited_once_with(
        CLIENT_PROFILE_COLLECTION,
        validator=COLLECTION_VALIDATORS[CLIENT_PROFILE_COLLECTION],
        validationLevel="strict",
        validationAction="error",
    )


@pytest.mark.asyncio
async def test_client_profile_migration_reconciles_collection_created_by_another_instance() -> None:
    clients = MagicMock()
    clients.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.list_collection_names = AsyncMock(return_value=[CLIENT_PROFILE_COLLECTION])
    database.__getitem__.return_value = clients

    await ClientProfileMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert database.command.await_args_list[-1].args[0] == {
        "collMod": CLIENT_PROFILE_COLLECTION,
        "validator": COLLECTION_VALIDATORS[CLIENT_PROFILE_COLLECTION],
        "validationLevel": "strict",
        "validationAction": "error",
    }


@pytest.mark.asyncio
async def test_client_profile_lifecycle_migration_backfills_preexisting_v5_profiles() -> None:
    profiles = MagicMock()
    profiles.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__.return_value = profiles

    await ClientProfileLifecycleMigration().up(cast(AsyncDatabase[MongoDocument], database))

    commands = [call.args[0] for call in database.command.await_args_list]
    assert commands == [
        {
            "collMod": CLIENT_PROFILE_COLLECTION,
            "validator": client_profile_validator_without_description_and_status(),
            "validationLevel": "strict",
            "validationAction": "error",
        },
        {
            "collMod": CLIENT_PROFILE_COLLECTION,
            "validator": CLIENT_PROFILE_COLLECTION_VALIDATORS[CLIENT_PROFILE_COLLECTION],
            "validationLevel": "strict",
            "validationAction": "error",
        },
    ]
    assert profiles.update_many.await_args_list == [
        (
            (
                {"description": {"$exists": False}},
                {"$set": {"description": DEFAULT_CLIENT_PROFILE_DESCRIPTION}},
            ),
        ),
        (({"status": {"$exists": False}}, {"$set": {"status": "active"}}),),
    ]
