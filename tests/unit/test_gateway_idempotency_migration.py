"""V4 Phase 10 migration and index contracts for bounded retry storage."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.idempotency_indexes import IDEMPOTENCY_STANDARD_INDEX_DEFINITIONS
from app.db.idempotency_schema import IDEMPOTENCY_COLLECTION, IDEMPOTENCY_COLLECTION_VALIDATORS
from app.db.migrations.v031_gateway_idempotency import GatewayIdempotencyMigration
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_gateway_idempotency_migration_creates_the_strict_ledger_idempotently() -> None:
    database = MagicMock()
    database.list_collection_names = AsyncMock(return_value=[])
    database.create_collection = AsyncMock()

    await GatewayIdempotencyMigration().up(cast(AsyncDatabase[MongoDocument], database))

    database.create_collection.assert_awaited_once_with(
        IDEMPOTENCY_COLLECTION,
        validator=IDEMPOTENCY_COLLECTION_VALIDATORS[IDEMPOTENCY_COLLECTION],
        validationLevel="strict",
        validationAction="error",
    )


@pytest.mark.asyncio
async def test_gateway_idempotency_migration_reconciles_an_existing_validator() -> None:
    database = MagicMock()
    database.list_collection_names = AsyncMock(return_value=[IDEMPOTENCY_COLLECTION])
    database.command = AsyncMock()

    await GatewayIdempotencyMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert database.command.await_args.args[0] == {
        "collMod": IDEMPOTENCY_COLLECTION,
        "validator": COLLECTION_VALIDATORS[IDEMPOTENCY_COLLECTION],
        "validationLevel": "strict",
        "validationAction": "error",
    }


def test_idempotency_indexes_are_unique_and_ttl_bounded() -> None:
    definitions = IDEMPOTENCY_STANDARD_INDEX_DEFINITIONS[IDEMPOTENCY_COLLECTION]
    names = {definition.document["name"]: definition.document for definition in definitions}

    assert names["gateway_idempotency_client_operation_key_uq"]["unique"] is True
    assert names["gateway_idempotency_expiry_ttl"]["expireAfterSeconds"] == 0
