"""Phase 10 content-metric source migration tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.v012_content_metric_source_metadata import ContentMetricSourceMigration
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_metric_source_migration_backfills_before_restoring_strict_schema() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await ContentMetricSourceMigration().up(cast(AsyncDatabase[MongoDocument], database))

    commands = [call.args[0] for call in database.command.await_args_list]
    intermediate = commands[0]["validator"]["$jsonSchema"]
    assert commands[0]["collMod"] == "content_metrics"
    assert {"source", "provider_metadata"}.isdisjoint(intermediate["required"])
    assert commands[1] == {
        "collMod": "content_metrics",
        "validator": COLLECTION_VALIDATORS["content_metrics"],
        "validationLevel": "strict",
        "validationAction": "error",
    }
    assert collection.update_many.await_args_list == [
        (({"source": {"$exists": False}}, {"$set": {"source": "manual"}}),),
        (({"provider_metadata": {"$exists": False}}, {"$set": {"provider_metadata": {}}}),),
    ]
