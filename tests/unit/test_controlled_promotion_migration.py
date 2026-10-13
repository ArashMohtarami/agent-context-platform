"""V3 Phase 10 migration and strict provenance validator tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.v018_controlled_promotion_provenance import (
    ControlledPromotionProvenanceMigration,
)
from app.db.schema import COLLECTION_VALIDATORS


@pytest.mark.asyncio
async def test_controlled_promotion_migration_reapplies_only_additive_v1_validators() -> None:
    database = MagicMock()
    database.command = AsyncMock()

    await ControlledPromotionProvenanceMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert database.command.await_args_list[0].args[0] == {
        "collMod": "memories",
        "validator": COLLECTION_VALIDATORS["memories"],
        "validationLevel": "strict",
        "validationAction": "error",
    }
    assert database.command.await_args_list[1].args[0] == {
        "collMod": "project_context",
        "validator": COLLECTION_VALIDATORS["project_context"],
        "validationLevel": "strict",
        "validationAction": "error",
    }


def test_promotion_provenance_validator_is_bounded_and_optional_on_v1_targets() -> None:
    provenance = COLLECTION_VALIDATORS["memories"]["$and"][0]["$jsonSchema"]["properties"][
        "promotion_provenance"
    ]
    context_provenance = COLLECTION_VALIDATORS["project_context"]["$jsonSchema"]["properties"][
        "promotion_provenance"
    ]

    assert provenance["required"] == [
        "candidate_id",
        "research_project_id",
        "claim_ids",
        "citation_ids",
        "source_ids",
        "source_snapshot_ids",
        "verified_at",
        "review_after",
        "freshness_class",
    ]
    assert context_provenance == provenance
    memory_required = COLLECTION_VALIDATORS["memories"]["$and"][0]["$jsonSchema"]["required"]
    assert "promotion_provenance" not in memory_required
