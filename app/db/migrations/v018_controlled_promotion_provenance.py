"""V3 Phase 10 additive V1 promotion-provenance migration."""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS


class ControlledPromotionProvenanceMigration:
    """Allow the optional, strict evidence envelope on existing V1 targets.

    No historical V1 row is changed: missing provenance means that the record
    predates controlled research promotion.
    """

    @property
    def version(self) -> int:
        return 18

    @property
    def description(self) -> str:
        return "add controlled research promotion provenance to V1 targets"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Reapply strict additive validators idempotently without a backfill."""
        for collection in ("memories", "project_context"):
            await database.command(
                {
                    "collMod": collection,
                    "validator": COLLECTION_VALIDATORS[collection],
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Never remove retained research provenance from promoted V1 records."""
        _ = database
        raise IrreversibleMigrationError("controlled promotion provenance cannot be safely removed")
