"""Extend the existing V1 memories collection with safe agent ownership."""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

COLLECTION = "memories"
LEGACY_EXTERNAL_ID_INDEX = "memory_external_id_uq"


class AgentMemoryScopesMigration:
    """Backfill V1 rows as shared memory before enabling V4 scopes."""

    @property
    def version(self) -> int:
        return 27

    @property
    def description(self) -> str:
        return "add client ownership to shared and agent-scoped V1 memories"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Keep global/project rows valid and reject malformed historical ownership."""
        collection = database[COLLECTION]
        malformed = await collection.find_one(
            {
                "scope": {"$in": ["global", "project"]},
                "client_id": {"$exists": True, "$ne": None},
            },
            {"_id": 1},
        )
        if malformed is not None:
            raise IrreversibleMigrationError(
                "shared memory has a client_id; repair ownership before enabling agent scopes"
            )
        await database.command(
            {
                "collMod": COLLECTION,
                "validator": COLLECTION_VALIDATORS[COLLECTION],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        await collection.update_many(
            {"client_id": {"$exists": False}},
            {"$set": {"client_id": None}},
        )
        # This V1 constraint omits client_id and would make two isolated
        # agents conflict on an idempotency key. The replacement is declared
        # in standard indexes and reconciled immediately after migrations.
        indexes = await collection.index_information()
        if LEGACY_EXTERNAL_ID_INDEX in indexes:
            await collection.drop_index(LEGACY_EXTERNAL_ID_INDEX)

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not discard client-specific operational memory on rollback."""
        _ = database
        raise IrreversibleMigrationError(
            "agent-scoped memory cannot be removed without discarding ownership"
        )
