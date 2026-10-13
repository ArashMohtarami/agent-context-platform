"""V3 Phase 8 migration for additive structured research brief references.

Existing Phase 0 brief identifiers remain intact.  This migration backfills only
empty structured-reference lists before restoring strict validation; it never
interprets evidence, changes claims, or promotes research material.
"""

from typing import Final

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

_BRIEF_REFERENCE_DEFAULTS: Final[dict[str, list[object]]] = {
    "claim_refs": [],
    "citation_refs": [],
    "contradictions": [],
    "source_refs": [],
}


class ResearchBriefStructureMigration:
    """Backfill additive structured brief fields, then tighten its validator."""

    @property
    def version(self) -> int:
        return 16

    @property
    def description(self) -> str:
        return "add structured V3 research brief references"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Keep historical brief identifier lists and add empty structured counterparts."""
        collection = database["research_briefs"]
        for field, default in _BRIEF_REFERENCE_DEFAULTS.items():
            await collection.update_many(
                {field: {"$exists": False}},
                {"$set": {field: default}},
            )
        await database.command(
            {
                "collMod": "research_briefs",
                "validator": COLLECTION_VALIDATORS["research_briefs"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not discard persisted research provenance or contradiction records."""
        _ = database
        raise IrreversibleMigrationError(
            "structured research brief references cannot be safely removed"
        )
