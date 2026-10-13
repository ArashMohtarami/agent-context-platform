"""V3 Phase 2 source-registry validator and standard-index contracts."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.v015_research_source_registry import ResearchSourceRegistryMigration
from app.db.research_indexes import RESEARCH_STANDARD_INDEX_DEFINITIONS
from app.db.schema import COLLECTION_VALIDATORS
from app.models.research import ResearchSourceType


def _names(collection: str) -> set[str]:
    return {str(item.document["name"]) for item in RESEARCH_STANDARD_INDEX_DEFINITIONS[collection]}


@pytest.mark.asyncio
async def test_source_registry_migration_reconciles_only_the_additive_source_validator() -> None:
    database = MagicMock()
    database.command = AsyncMock()

    await ResearchSourceRegistryMigration().up(cast(AsyncDatabase[MongoDocument], database))

    assert database.command.await_args.args[0] == {
        "collMod": "research_sources",
        "validator": COLLECTION_VALIDATORS["research_sources"],
        "validationLevel": "strict",
        "validationAction": "error",
    }


def test_source_validator_and_standard_indexes_pin_phase_two_source_contracts() -> None:
    schema = COLLECTION_VALIDATORS["research_sources"]["$jsonSchema"]
    properties = schema["properties"]

    assert properties["source_type"]["enum"] == [item.value for item in ResearchSourceType]
    assert {
        "research_source_project_canonical_uri_uq",
        "research_source_project_metadata",
    }.issubset(_names("research_sources"))
    assert {
        "source_snapshot_source_id_uq",
        "source_snapshot_project_hash",
    }.issubset(_names("source_snapshots"))
