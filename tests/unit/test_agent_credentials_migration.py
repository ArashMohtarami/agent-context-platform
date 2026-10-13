"""The V4 client-key linkage migration is backwards compatible and idempotent."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.migrations.v024_agent_credentials import AgentCredentialMigration
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_agent_credential_migration_backfills_legacy_keys_before_strict_validator() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await AgentCredentialMigration().up(cast(AsyncDatabase[MongoDocument], database))

    first, second = database.command.await_args_list
    assert first.args[0]["collMod"] == "api_keys"
    required = first.args[0]["validator"]["$jsonSchema"]["required"]
    assert "client_id" not in required
    assert "revoked_at" not in required
    assert second.args[0] == {
        "collMod": "api_keys",
        "validator": COLLECTION_VALIDATORS["api_keys"],
        "validationLevel": "strict",
        "validationAction": "error",
    }
    assert collection.update_many.await_args_list == [
        call({"client_id": {"$exists": False}}, {"$set": {"client_id": None}}),
        call({"revoked_at": {"$exists": False}}, {"$set": {"revoked_at": None}}),
    ]


@pytest.mark.asyncio
async def test_agent_credential_migration_cannot_be_reversed_without_losing_provenance() -> None:
    with pytest.raises(IrreversibleMigrationError):
        await AgentCredentialMigration().down(MagicMock())
