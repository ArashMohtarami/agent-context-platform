"""V3 Phase 8 research-brief validator migration tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.v016_research_brief_structure import ResearchBriefStructureMigration
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_brief_structure_migration_backfills_additive_fields_and_reconciles_validator() -> (
    None
):
    collection = MagicMock()
    collection.update_many = AsyncMock()
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)
    database.command = AsyncMock()

    await ResearchBriefStructureMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert collection.update_many.await_count == 4
    assert [call.args[0] for call in collection.update_many.await_args_list] == [
        {"claim_refs": {"$exists": False}},
        {"citation_refs": {"$exists": False}},
        {"contradictions": {"$exists": False}},
        {"source_refs": {"$exists": False}},
    ]
    assert database.command.await_args.args[0] == {
        "collMod": "research_briefs",
        "validator": COLLECTION_VALIDATORS["research_briefs"],
        "validationLevel": "strict",
        "validationAction": "error",
    }


def test_brief_validator_requires_structured_claim_citation_and_source_provenance() -> None:
    schema = COLLECTION_VALIDATORS["research_briefs"]["$jsonSchema"]
    properties = schema["properties"]

    assert {"claim_refs", "citation_refs", "contradictions", "source_refs"}.issubset(
        schema["required"]
    )
    assert properties["citation_refs"]["items"]["required"] == [
        "citation_id",
        "claim_id",
        "source_id",
        "snapshot_id",
        "chunk_id",
        "source_class",
        "locator",
        "evidence_excerpt",
        "relation",
    ]
