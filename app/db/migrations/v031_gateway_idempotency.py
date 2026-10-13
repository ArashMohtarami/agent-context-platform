"""Create the bounded V4 Phase 10 MCP idempotency ledger."""

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import CollectionInvalid

from app.db.client_types import MongoDocument
from app.db.idempotency_schema import IDEMPOTENCY_COLLECTION, IDEMPOTENCY_COLLECTION_VALIDATORS
from app.db.migrations.base import IrreversibleMigrationError


class GatewayIdempotencyMigration:
    """Add one short-lived retry ledger without touching existing domain records."""

    @property
    def version(self) -> int:
        return 31

    @property
    def description(self) -> str:
        return "create bounded MCP gateway idempotency records"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Create or reconcile the strict validator idempotently."""
        validator = IDEMPOTENCY_COLLECTION_VALIDATORS[IDEMPOTENCY_COLLECTION]
        existing = set(await database.list_collection_names())
        if IDEMPOTENCY_COLLECTION not in existing:
            try:
                await database.create_collection(
                    IDEMPOTENCY_COLLECTION,
                    validator=validator,
                    validationLevel="strict",
                    validationAction="error",
                )
                return
            except CollectionInvalid:
                pass
        await database.command(
            {
                "collMod": IDEMPOTENCY_COLLECTION,
                "validator": validator,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """A rollback must not erase retry evidence that may still be needed."""
        _ = database
        raise IrreversibleMigrationError(
            "gateway idempotency records cannot be safely discarded by rollback"
        )
