"""V3 Phase 1 migration for research scopes and audit records.

Only the strict validators on existing security/audit collections change.  No
V1/V2 record is rewritten and no research data is acquired or transformed.
"""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS


class ResearchProjectAuthorizationMigration:
    """Admit additive V3 research scopes and the research-write audit action."""

    @property
    def version(self) -> int:
        return 14

    @property
    def description(self) -> str:
        return "add V3 research project scopes and audit action"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Reconcile validators without modifying any existing document."""
        for collection in ("api_keys", "audit_logs"):
            await database.command(
                {
                    "collMod": collection,
                    "validator": COLLECTION_VALIDATORS[collection],
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not remove scope/action values that may already be in use."""
        _ = database
        raise IrreversibleMigrationError(
            "research project scopes and audit records cannot be safely removed"
        )
