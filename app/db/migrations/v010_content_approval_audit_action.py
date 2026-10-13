"""Phase 8 migration for durable, explicit content approval decisions."""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS


class ContentApprovalAuditActionMigration:
    """Add the additive ``content.approve`` action to the existing audit validator."""

    @property
    def version(self) -> int:
        return 10

    @property
    def description(self) -> str:
        return "add content approval audit action"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Reconcile only the strict audit validator; existing records remain unchanged."""
        await database.command(
            {
                "collMod": "audit_logs",
                "validator": COLLECTION_VALIDATORS["audit_logs"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not remove an approval event that may already be relied upon."""
        _ = database
        raise IrreversibleMigrationError("content-approval audit records cannot be safely removed")
