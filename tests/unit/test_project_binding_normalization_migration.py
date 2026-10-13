"""Migration coverage for enabling deterministic V4 project resolution."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.migrations.v026_project_binding_normalization import (
    ProjectBindingNormalizationMigration,
)


@pytest.mark.asyncio
async def test_binding_normalization_migration_canonicalizes_existing_ssh_values() -> None:
    collection = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(
        return_value=[
            {
                "_id": ObjectId(),
                "binding_id": "payment-origin",
                "project_id": "payment-service",
                "binding_type": "git_remote",
                "value": "git@github.com:example/payment-service.git",
            }
        ]
    )
    collection.find.return_value = cursor
    collection.update_one = AsyncMock()
    database = MagicMock()
    database.__getitem__.return_value = collection
    database.command = AsyncMock()

    await ProjectBindingNormalizationMigration().up(cast(AsyncDatabase[MongoDocument], database))

    update = collection.update_one.await_args
    assert update.args[0]["value"] == "git@github.com:example/payment-service.git"
    assert update.args[1]["$set"]["value"] == "github.com/example/payment-service"
    assert update.args[1]["$set"]["repository_locator"] == "github.com/example/payment-service"
    assert update.args[1]["$inc"] == {"revision": 1}


@pytest.mark.asyncio
async def test_binding_normalization_migration_fails_closed_for_cross_project_aliases() -> None:
    collection = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(
        return_value=[
            {
                "_id": ObjectId(),
                "binding_id": "payment-origin",
                "project_id": "payment-service",
                "binding_type": "git_remote",
                "value": "git@github.com:example/shared.git",
            },
            {
                "_id": ObjectId(),
                "binding_id": "wallet-origin",
                "project_id": "wallet-service",
                "binding_type": "repository_url",
                "value": "https://github.com/example/shared.git",
            },
        ]
    )
    collection.find.return_value = cursor
    collection.update_one = AsyncMock()
    database = MagicMock()
    database.__getitem__.return_value = collection
    database.command = AsyncMock()

    with pytest.raises(IrreversibleMigrationError, match="SSH and HTTPS"):
        await ProjectBindingNormalizationMigration().up(
            cast(AsyncDatabase[MongoDocument], database)
        )

    collection.update_one.assert_not_awaited()
