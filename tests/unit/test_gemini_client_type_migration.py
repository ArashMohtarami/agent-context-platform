"""Regression coverage for the additive Gemini client-type migration."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.agent_schema import AGENT_COLLECTION_VALIDATORS
from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.migrations.v035_gemini_client_type import (
    ACTOR_PROVENANCE_COLLECTIONS,
    AGENT_CLIENT_TYPE_COLLECTIONS,
    RECONCILED_COLLECTIONS,
    GeminiClientTypeMigration,
)
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_gemini_client_type_migration_only_reconciles_affected_validators() -> None:
    database = MagicMock()
    database.command = AsyncMock()

    await GeminiClientTypeMigration().up(cast(AsyncDatabase[MongoDocument], database))

    commands = [call.args[0] for call in database.command.await_args_list]
    assert [command["collMod"] for command in commands] == list(RECONCILED_COLLECTIONS)
    assert commands[0]["validator"] == AGENT_COLLECTION_VALIDATORS["agent_clients"]
    assert (
        commands[len(AGENT_CLIENT_TYPE_COLLECTIONS)]["validator"]
        == COLLECTION_VALIDATORS["retrieval_logs"]
    )
    assert commands[-1]["validator"] == COLLECTION_VALIDATORS[ACTOR_PROVENANCE_COLLECTIONS[-1]]
    assert all(command["validationLevel"] == "strict" for command in commands)
    assert all(command["validationAction"] == "error" for command in commands)


@pytest.mark.asyncio
async def test_gemini_client_type_migration_does_not_remove_durable_provenance() -> None:
    with pytest.raises(IrreversibleMigrationError):
        await GeminiClientTypeMigration().down(MagicMock())
