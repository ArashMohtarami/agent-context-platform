"""Backfill V5 client-profile descriptions and lifecycle status safely."""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_profile_schema import (
    CLIENT_PROFILE_COLLECTION,
    CLIENT_PROFILE_COLLECTION_VALIDATORS,
    client_profile_validator_without_description_and_status,
)
from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.models.client_profile import DEFAULT_CLIENT_PROFILE_DESCRIPTION


class ClientProfileLifecycleMigration:
    """Add lifecycle fields without changing any assigned V4 client.

    Migration 33 may already have populated profile records.  Strict MongoDB
    validation must first permit the fields, then legacy records receive safe
    defaults, then the final validator requires both fields.
    """

    @property
    def version(self) -> int:
        return 34

    @property
    def description(self) -> str:
        return "add V5 client profile descriptions and lifecycle status"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Apply the backwards-compatible profile-only schema evolution."""
        await database.command(
            {
                "collMod": CLIENT_PROFILE_COLLECTION,
                "validator": client_profile_validator_without_description_and_status(),
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        profiles = database[CLIENT_PROFILE_COLLECTION]
        await profiles.update_many(
            {"description": {"$exists": False}},
            {"$set": {"description": DEFAULT_CLIENT_PROFILE_DESCRIPTION}},
        )
        await profiles.update_many({"status": {"$exists": False}}, {"$set": {"status": "active"}})
        await database.command(
            {
                "collMod": CLIENT_PROFILE_COLLECTION,
                "validator": CLIENT_PROFILE_COLLECTION_VALIDATORS[CLIENT_PROFILE_COLLECTION],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not discard administrative profile lifecycle history."""
        _ = database
        raise IrreversibleMigrationError(
            "client profile descriptions and lifecycle status cannot be removed safely"
        )
