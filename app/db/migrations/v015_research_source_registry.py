"""V3 Phase 2 migration for the constrained source registry validator."""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS


class ResearchSourceRegistryMigration:
    """Constrain persisted source types without changing source history."""

    @property
    def version(self) -> int:
        return 15

    @property
    def description(self) -> str:
        return "constrain V3 research source registry types"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Reconcile the additive source validator; indexes remain startup-idempotent."""
        await database.command(
            {
                "collMod": "research_sources",
                "validator": COLLECTION_VALIDATORS["research_sources"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not widen a validator after registered source history exists."""
        _ = database
        raise IrreversibleMigrationError(
            "research source type constraints cannot be safely removed"
        )
