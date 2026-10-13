"""V4 Phase 6 additive actor-provenance migration tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.migrations.v028_actor_provenance import _COLLECTIONS, ActorProvenanceMigration
from app.db.schema import COLLECTION_VALIDATORS
from app.models.actor import system_actor


@pytest.mark.asyncio
async def test_actor_provenance_migration_backfills_before_enforcing_current_validators() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await ActorProvenanceMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert [entry.args[0]["collMod"] for entry in database.command.await_args_list] == list(
        _COLLECTIONS
    )
    for entry in database.command.await_args_list:
        command = entry.args[0]
        assert command["validator"] == COLLECTION_VALIDATORS[command["collMod"]]
        assert command["validationLevel"] == "strict"
        assert command["validationAction"] == "error"
    assert collection.update_many.await_args_list == [
        call(
            {"actor": {"$exists": False}},
            {"$set": {"actor": system_actor().model_dump(mode="python")}},
        )
        for _ in _COLLECTIONS
    ]


def test_actor_provenance_is_required_for_v1_v2_v3_and_audit_collections() -> None:
    assert "actor" in COLLECTION_VALIDATORS["project_context"]["$jsonSchema"]["required"]
    assert "actor" in COLLECTION_VALIDATORS["memories"]["$and"][0]["$jsonSchema"]["required"]
    assert "actor" in COLLECTION_VALIDATORS["content_items"]["$jsonSchema"]["required"]
    assert "actor" in COLLECTION_VALIDATORS["research_notes"]["$jsonSchema"]["required"]
    assert "actor" in COLLECTION_VALIDATORS["skill_update_proposals"]["$jsonSchema"]["required"]
    assert "actor" in COLLECTION_VALIDATORS["audit_logs"]["$jsonSchema"]["required"]


@pytest.mark.asyncio
async def test_actor_provenance_migration_does_not_erase_history_on_rollback() -> None:
    with pytest.raises(IrreversibleMigrationError):
        await ActorProvenanceMigration().down(MagicMock())
