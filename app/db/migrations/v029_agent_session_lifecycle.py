"""Expand the Phase 0 session contract with the explicit expired state."""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.agent_schema import AGENT_COLLECTION_VALIDATORS
from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError


class AgentSessionLifecycleMigration:
    """Allow lazy session expiry without weakening the collection validator."""

    @property
    def version(self) -> int:
        return 29

    @property
    def description(self) -> str:
        return "add expired state to V4 agent sessions"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Update the idempotent validator; existing session documents remain valid."""
        await database.command(
            {
                "collMod": "agent_sessions",
                "validator": AGENT_COLLECTION_VALIDATORS["agent_sessions"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not make already-expired durable records invalid on rollback."""
        _ = database
        raise IrreversibleMigrationError(
            "expired agent sessions cannot be safely removed from the schema"
        )
