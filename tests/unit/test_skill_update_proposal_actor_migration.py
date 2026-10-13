"""Regression coverage for V4 actor provenance on V3 Phase 11 proposals."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.migrations.v032_skill_update_proposal_actor import (
    SkillUpdateProposalActorMigration,
)
from app.db.schema import COLLECTION_VALIDATORS
from app.models.actor import system_actor


@pytest.mark.asyncio
async def test_skill_proposal_actor_migration_reconciles_validator_and_backfills() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    migration = SkillUpdateProposalActorMigration()
    await migration.up(cast(AsyncDatabase[MongoDocument], database))

    assert migration.version == 32
    database.command.assert_awaited_once_with(
        {
            "collMod": "skill_update_proposals",
            "validator": COLLECTION_VALIDATORS["skill_update_proposals"],
            "validationLevel": "strict",
            "validationAction": "error",
        }
    )
    collection.update_many.assert_awaited_once_with(
        {"actor": {"$exists": False}},
        {"$set": {"actor": system_actor().model_dump(mode="python")}},
    )


@pytest.mark.asyncio
async def test_skill_proposal_actor_migration_does_not_erase_history_on_rollback() -> None:
    with pytest.raises(IrreversibleMigrationError):
        await SkillUpdateProposalActorMigration().down(MagicMock())
