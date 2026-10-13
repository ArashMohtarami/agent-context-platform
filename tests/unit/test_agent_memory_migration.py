"""Migration coverage for V4 client-owned operational memory."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.migrations.v027_agent_memory_scopes import (
    LEGACY_EXTERNAL_ID_INDEX,
    AgentMemoryScopesMigration,
)
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_agent_memory_migration_backfills_shared_rows_and_replaces_the_key_index() -> None:
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=None)
    collection.update_many = AsyncMock()
    collection.index_information = AsyncMock(return_value={LEGACY_EXTERNAL_ID_INDEX: {}})
    collection.drop_index = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await AgentMemoryScopesMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert collection.find_one.await_args.args[0] == {
        "scope": {"$in": ["global", "project"]},
        "client_id": {"$exists": True, "$ne": None},
    }
    assert database.command.await_args.args[0] == {
        "collMod": "memories",
        "validator": COLLECTION_VALIDATORS["memories"],
        "validationLevel": "strict",
        "validationAction": "error",
    }
    assert collection.update_many.await_args_list == [
        call({"client_id": {"$exists": False}}, {"$set": {"client_id": None}})
    ]
    collection.drop_index.assert_awaited_once_with(LEGACY_EXTERNAL_ID_INDEX)


@pytest.mark.asyncio
async def test_agent_memory_migration_refuses_a_misowned_legacy_shared_memory() -> None:
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value={"_id": "misowned"})
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)

    with pytest.raises(IrreversibleMigrationError, match="shared memory has a client_id"):
        await AgentMemoryScopesMigration().up(cast(AsyncDatabase[MongoDocument], database))


@pytest.mark.asyncio
async def test_agent_memory_migration_is_irreversible_without_losing_operational_ownership() -> (
    None
):
    with pytest.raises(IrreversibleMigrationError):
        await AgentMemoryScopesMigration().down(MagicMock())
