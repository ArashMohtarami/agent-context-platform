"""V2 Phase 3 migration for auditable content mutations.

This is deliberately limited to the existing audit collection.  Phase 0
already created the V2 content collections and all of the fields Phase 3
uses, so no V1 collection or content document is rewritten here.
"""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS


class ContentAuditActionMigration:
    """Allow the additive ``content.write`` audit action idempotently."""

    @property
    def version(self) -> int:
        return 8

    @property
    def description(self) -> str:
        return "add content write audit action"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Reconcile the strict audit validator without touching audit records."""
        await database.command(
            {
                "collMod": "audit_logs",
                "validator": COLLECTION_VALIDATORS["audit_logs"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Refuse to remove an audit value that may already be in use."""
        _ = database
        raise IrreversibleMigrationError("content-write audit records cannot be safely removed")
