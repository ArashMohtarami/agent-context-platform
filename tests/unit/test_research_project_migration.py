"""V3 Phase 1 additive research authorization migration tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.v014_research_project_auth import ResearchProjectAuthorizationMigration
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_research_project_authorization_migration_only_reconciles_security_validators() -> (
    None
):
    database = MagicMock()
    database.command = AsyncMock()

    await ResearchProjectAuthorizationMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert [call.args[0] for call in database.command.await_args_list] == [
        {
            "collMod": "api_keys",
            "validator": COLLECTION_VALIDATORS["api_keys"],
            "validationLevel": "strict",
            "validationAction": "error",
        },
        {
            "collMod": "audit_logs",
            "validator": COLLECTION_VALIDATORS["audit_logs"],
            "validationLevel": "strict",
            "validationAction": "error",
        },
    ]
