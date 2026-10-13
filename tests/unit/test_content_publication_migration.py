"""Phase 9 publication-receipt migration tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.v011_content_publication_receipt import (
    ContentPublicationReceiptMigration,
)
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_publication_receipt_migration_backfills_before_restoring_strict_schema() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await ContentPublicationReceiptMigration().up(cast(AsyncDatabase[MongoDocument], database))

    commands = [call.args[0] for call in database.command.await_args_list]
    intermediate = commands[0]["validator"]["$jsonSchema"]
    assert commands[0]["collMod"] == "content_items"
    assert {"publication_provider", "external_id", "publication_idempotency_key"}.isdisjoint(
        intermediate["required"]
    )
    assert commands[1] == {
        "collMod": "content_items",
        "validator": COLLECTION_VALIDATORS["content_items"],
        "validationLevel": "strict",
        "validationAction": "error",
    }
    assert commands[2] == {
        "collMod": "audit_logs",
        "validator": COLLECTION_VALIDATORS["audit_logs"],
        "validationLevel": "strict",
        "validationAction": "error",
    }
    assert collection.update_many.await_args_list == [
        (({"publication_provider": {"$exists": False}}, {"$set": {"publication_provider": None}}),),
        (({"external_id": {"$exists": False}}, {"$set": {"external_id": None}}),),
        (
            (
                {"publication_idempotency_key": {"$exists": False}},
                {"$set": {"publication_idempotency_key": None}},
            ),
        ),
    ]
