"""V3 Phase 9 additive candidate-review metadata migration.

Candidate content and evidence links remain inert.  The migration only renames
legacy reviewer metadata before enforcing the Phase 9 review validator; it
never approves, rejects, promotes, or writes V1 knowledge.
"""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS


class CandidateReviewWorkflowMigration:
    """Preserve old reviewer identities while activating strict Phase 9 fields."""

    @property
    def version(self) -> int:
        return 17

    @property
    def description(self) -> str:
        return "add V3 candidate reviewer workflow metadata"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Make legacy rows valid before returning to strict validation."""
        collection = database["knowledge_candidates"]
        validator = COLLECTION_VALIDATORS["knowledge_candidates"]
        await database.command(
            {
                "collMod": "knowledge_candidates",
                "validator": validator,
                "validationLevel": "moderate",
                "validationAction": "error",
            }
        )
        await collection.update_many(
            {"reviewer": {"$exists": False}, "reviewed_by": {"$type": "string"}},
            [{"$set": {"reviewer": "$reviewed_by"}}, {"$unset": "reviewed_by"}],
        )
        await collection.update_many(
            {"reviewer": {"$exists": False}},
            {"$set": {"reviewer": None}},
        )
        await collection.update_many(
            {"reviewed_by": {"$exists": True}},
            {"$unset": {"reviewed_by": ""}},
        )
        await database.command(
            {
                "collMod": "knowledge_candidates",
                "validator": validator,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Never discard review provenance merely to restore a legacy name."""
        _ = database
        raise IrreversibleMigrationError("candidate reviewer metadata cannot be safely removed")
