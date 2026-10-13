"""Migration coverage for additive MCP client provenance on retrieval logs."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.migrations.v025_retrieval_log_agent_metadata import RetrievalLogAgentMetadataMigration
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_retrieval_log_metadata_is_backfilled_before_strict_validation() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await RetrievalLogAgentMetadataMigration().up(cast(AsyncDatabase[MongoDocument], database))

    first, second = database.command.await_args_list
    assert first.args[0]["collMod"] == "retrieval_logs"
    required = first.args[0]["validator"]["$jsonSchema"]["required"]
    assert all(field not in required for field in ("client_id", "client_type", "session_id"))
    assert second.args[0] == {
        "collMod": "retrieval_logs",
        "validator": COLLECTION_VALIDATORS["retrieval_logs"],
        "validationLevel": "strict",
        "validationAction": "error",
    }
    assert collection.update_many.await_args_list == [
        call({"client_id": {"$exists": False}}, {"$set": {"client_id": None}}),
        call({"client_type": {"$exists": False}}, {"$set": {"client_type": None}}),
        call({"session_id": {"$exists": False}}, {"$set": {"session_id": None}}),
    ]


@pytest.mark.asyncio
async def test_retrieval_log_metadata_migration_cannot_be_reversed() -> None:
    with pytest.raises(IrreversibleMigrationError):
        await RetrievalLogAgentMetadataMigration().down(MagicMock())
