"""V3 Phase 0 additive persistence migration.

This migration creates V3 collections only.  It does not alter V1/V2 data,
run jobs, register sources, make claims, or promote any research result.
"""

from typing import Final

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

V3_RESEARCH_COLLECTIONS: Final[tuple[str, ...]] = (
    "research_projects",
    "research_sources",
    "research_documents",
    "research_chunks",
    "research_notes",
    "research_claims",
    "research_citations",
    "research_briefs",
    "knowledge_candidates",
    "promotion_events",
    "ingestion_jobs",
    "source_snapshots",
)


class ResearchSchemaMigration:
    """Create V3 Phase 0 collections and restore strict validators idempotently."""

    @property
    def version(self) -> int:
        return 13

    @property
    def description(self) -> str:
        return "create V3 research and evidence collections"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Create absent collections without modifying V1/V2 collections or data."""
        existing = set(await database.list_collection_names())
        for name in V3_RESEARCH_COLLECTIONS:
            validator = COLLECTION_VALIDATORS[name]
            if name in existing:
                await database.command(
                    {
                        "collMod": name,
                        "validator": validator,
                        "validationLevel": "strict",
                        "validationAction": "error",
                    }
                )
                continue
            await database.create_collection(
                name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Refuse to erase evidence, citations, snapshots, or review history."""
        _ = database
        raise IrreversibleMigrationError(
            "V3 research schema rollback would discard durable evidence and provenance"
        )
