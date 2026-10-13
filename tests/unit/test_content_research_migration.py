"""Phase 13 migration tests for additive V2 brief research references."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.v020_content_brief_research_evidence import (
    ContentBriefResearchEvidenceMigration,
)
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_content_brief_research_migration_backfills_before_strict_validation() -> None:
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(return_value=collection)

    await ContentBriefResearchEvidenceMigration().up(cast(AsyncDatabase[MongoDocument], database))

    commands = [call.args[0] for call in database.command.await_args_list]
    intermediate = commands[0]["validator"]["$jsonSchema"]
    expected: dict[str, object] = {
        "research_project_id": None,
        "claim_refs": [],
        "citation_refs": [],
        "factual_review_status": "not_reviewed",
    }
    assert commands[0]["collMod"] == "content_briefs"
    assert set(expected).isdisjoint(intermediate["required"])
    assert commands[1] == {
        "collMod": "content_briefs",
        "validator": COLLECTION_VALIDATORS["content_briefs"],
        "validationLevel": "strict",
        "validationAction": "error",
    }
    assert collection.update_many.await_args_list == [
        (({field: {"$exists": False}}, {"$set": {field: value}}),)
        for field, value in expected.items()
    ]


def test_content_brief_validator_requires_additive_phase_13_fields() -> None:
    validator = COLLECTION_VALIDATORS["content_briefs"]
    schema = cast(MongoDocument, validator["$jsonSchema"])
    properties = cast(MongoDocument, schema["properties"])
    required = cast(list[object], schema["required"])

    assert {
        "research_project_id",
        "claim_refs",
        "citation_refs",
        "factual_review_status",
    }.issubset(required)
    assert cast(MongoDocument, properties["factual_review_status"])["enum"] == [
        "not_reviewed",
        "verified",
        "needs_review",
    ]
