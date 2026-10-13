"""V3 Phase 14 migration and strict-validator tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.v021_revalidation_freshness import RevalidationFreshnessMigration
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_revalidation_migration_backfills_metadata_before_restoring_strict_validators() -> (
    None
):
    collections = {
        name: MagicMock(update_many=AsyncMock())
        for name in (
            "knowledge_candidates",
            "memories",
            "project_context",
        )
    }
    database = MagicMock()
    database.command = AsyncMock()
    database.__getitem__ = MagicMock(side_effect=collections.__getitem__)

    await RevalidationFreshnessMigration().up(cast(AsyncDatabase[MongoDocument], database))

    commands = [call.args[0] for call in database.command.await_args_list]
    assert [command["collMod"] for command in commands[:3]] == [
        "knowledge_candidates",
        "memories",
        "project_context",
    ]
    candidate_schema = commands[0]["validator"]["$jsonSchema"]
    assert {
        "freshness_class",
        "review_after",
        "revalidation_of_candidate_id",
        "revalidation_reasons",
    }.isdisjoint(candidate_schema["required"])
    assert [command["validator"] for command in commands[-3:]] == [
        COLLECTION_VALIDATORS["knowledge_candidates"],
        COLLECTION_VALIDATORS["memories"],
        COLLECTION_VALIDATORS["project_context"],
    ]
    assert collections["knowledge_candidates"].update_many.await_count == 4
    assert collections["memories"].update_many.await_count == 3
    assert collections["project_context"].update_many.await_count == 3


def test_phase_14_validators_require_bounded_freshness_metadata() -> None:
    candidate = cast(MongoDocument, COLLECTION_VALIDATORS["knowledge_candidates"]["$jsonSchema"])
    candidate_properties = cast(MongoDocument, candidate["properties"])
    assert {
        "freshness_class",
        "review_after",
        "revalidation_of_candidate_id",
        "revalidation_reasons",
    }.issubset(candidate["required"])
    assert candidate_properties["freshness_class"]["enum"] == [
        "stable",
        "version_dependent",
        "time_sensitive",
        "volatile",
    ]

    context = cast(MongoDocument, COLLECTION_VALIDATORS["project_context"]["$jsonSchema"])
    provenance = cast(MongoDocument, context["properties"]["promotion_provenance"])
    assert {"source_snapshot_ids", "review_after", "freshness_class"}.issubset(
        provenance["required"]
    )
