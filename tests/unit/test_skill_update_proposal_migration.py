"""V3 Phase 11 migration tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.migrations.v019_skill_update_proposals import (
    SKILL_UPDATE_PROPOSAL_COLLECTION,
    SkillUpdateProposalMigration,
)
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_migration_creates_strict_generated_proposal_collection_when_absent() -> None:
    database = MagicMock()
    database.list_collection_names = AsyncMock(return_value=[])
    database.create_collection = AsyncMock()

    migration = SkillUpdateProposalMigration()
    await migration.up(cast(AsyncDatabase[MongoDocument], database))

    database.create_collection.assert_awaited_once_with(
        SKILL_UPDATE_PROPOSAL_COLLECTION,
        validator=COLLECTION_VALIDATORS[SKILL_UPDATE_PROPOSAL_COLLECTION],
        validationLevel="strict",
        validationAction="error",
    )
    assert migration.version == 19


@pytest.mark.asyncio
async def test_migration_reconciles_existing_collection_without_touching_skills() -> None:
    database = MagicMock()
    database.list_collection_names = AsyncMock(return_value=[SKILL_UPDATE_PROPOSAL_COLLECTION])
    database.command = AsyncMock()
    database.create_collection = AsyncMock()

    await SkillUpdateProposalMigration().up(cast(AsyncDatabase[MongoDocument], database))

    database.create_collection.assert_not_awaited()
    database.command.assert_awaited_once_with(
        {
            "collMod": SKILL_UPDATE_PROPOSAL_COLLECTION,
            "validator": COLLECTION_VALIDATORS[SKILL_UPDATE_PROPOSAL_COLLECTION],
            "validationLevel": "strict",
            "validationAction": "error",
        }
    )


@pytest.mark.asyncio
async def test_migration_refuses_destructive_rollback() -> None:
    with pytest.raises(IrreversibleMigrationError):
        await SkillUpdateProposalMigration().down(MagicMock())
