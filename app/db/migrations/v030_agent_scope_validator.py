"""Reconcile the V4 client validator with the established credential scopes."""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.agent_schema import AGENT_COLLECTION_VALIDATORS
from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError


class AgentScopeValidatorMigration:
    """Allow all already-supported V4 client scopes in persisted identities.

    Earlier V4 credential phases added gateway scopes to the shared scope
    registry but did not refresh the Phase 0 ``agent_clients`` validator. This
    is a backwards-compatible validator reconciliation: it changes no stored
    client and only admits scope names the application already validates.
    """

    @property
    def version(self) -> int:
        return 30

    @property
    def description(self) -> str:
        return "reconcile V4 agent client scope validation"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Apply the current bounded shared-scope enum to agent clients."""
        await database.command(
            {
                "collMod": "agent_clients",
                "validator": AGENT_COLLECTION_VALIDATORS["agent_clients"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not invalidate existing clients on a rollback."""
        _ = database
        raise IrreversibleMigrationError(
            "the current agent scope enum cannot be narrowed without invalidating clients"
        )
