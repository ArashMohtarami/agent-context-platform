"""V2 Phase 1 creator-profile preferences and access migration.

The Phase 0 collections are preserved.  This migration only backfills safe
empty defaults before tightening their validators and adds an optional profile
allow-list to existing API keys.  An empty allow-list deliberately retains the
V1 meaning of an unrestricted grant.
"""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

_COLLECTIONS = ("api_keys", "audit_logs", "creator_profiles")


class CreatorProfileAccessMigration:
    """Safely add V2 Phase 1 profile grants and tone-preference data."""

    @property
    def version(self) -> int:
        return 7

    @property
    def description(self) -> str:
        return "add creator profile access grants and tone preferences"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Backfill defaults and reconcile strict validators idempotently."""
        # Widen the accepted shape before writing a newly introduced field:
        # the Phase 0 validators deliberately forbid unknown properties, so a
        # backfill first would be rejected on every existing deployment.
        for collection in _COLLECTIONS:
            await database.command(
                {
                    "collMod": collection,
                    "validator": COLLECTION_VALIDATORS[collection],
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
        await database["api_keys"].update_many(
            {"profiles": {"$exists": False}}, {"$set": {"profiles": []}}
        )
        await database["creator_profiles"].update_many(
            {"tone_preferences": {"$exists": False}}, {"$set": {"tone_preferences": []}}
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Refuse to weaken the authorization or audit schema automatically."""
        _ = database
        raise IrreversibleMigrationError(
            "creator profile access rollback would weaken active authorization records"
        )
