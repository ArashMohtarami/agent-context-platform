"""V3 Phase 9 additive candidate review migration tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.v017_candidate_review_workflow import CandidateReviewWorkflowMigration
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_candidate_review_migration_renames_legacy_reviewer_before_strict_validation() -> (
    None
):
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)
    database.command = AsyncMock()

    await CandidateReviewWorkflowMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert database.command.await_args_list[0].args[0] == {
        "collMod": "knowledge_candidates",
        "validator": COLLECTION_VALIDATORS["knowledge_candidates"],
        "validationLevel": "moderate",
        "validationAction": "error",
    }
    assert collection.update_many.await_args_list[0].args == (
        {"reviewer": {"$exists": False}, "reviewed_by": {"$type": "string"}},
        [{"$set": {"reviewer": "$reviewed_by"}}, {"$unset": "reviewed_by"}],
    )
    assert collection.update_many.await_count == 3
    assert database.command.await_args_list[-1].args[0]["validationLevel"] == "strict"
