"""Permit Gemini CLI provenance in existing strict MongoDB validators."""

from typing import Final

from pymongo.asynchronous.database import AsyncDatabase

from app.db.agent_schema import AGENT_COLLECTION_VALIDATORS
from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.migrations.v006_creator_content_schema import V2_CONTENT_COLLECTIONS
from app.db.migrations.v013_research_schema import V3_RESEARCH_COLLECTIONS
from app.db.schema import COLLECTION_VALIDATORS

# These are the existing collections whose validators persist an agent client
# type directly or through actor provenance.  Reapplying only their validators
# makes adding ``gemini_cli`` additive and leaves all stored V1--V4 documents
# untouched.
AGENT_CLIENT_TYPE_COLLECTIONS: Final[tuple[str, ...]] = (
    "agent_clients",
    "agent_sessions",
    "agent_activity",
)
ACTOR_PROVENANCE_COLLECTIONS: Final[tuple[str, ...]] = (
    "project_context",
    "memories",
    "audit_logs",
    *V2_CONTENT_COLLECTIONS,
    *V3_RESEARCH_COLLECTIONS,
    "skill_update_proposals",
)
RECONCILED_COLLECTIONS: Final[tuple[str, ...]] = (
    *AGENT_CLIENT_TYPE_COLLECTIONS,
    "retrieval_logs",
    *ACTOR_PROVENANCE_COLLECTIONS,
)


class GeminiClientTypeMigration:
    """Refresh strict validators after adding the generic ``gemini_cli`` type."""

    @property
    def version(self) -> int:
        return 35

    @property
    def description(self) -> str:
        return "allow Gemini CLI client provenance in existing strict validators"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Reconcile validators without changing client records or content."""
        for name in AGENT_CLIENT_TYPE_COLLECTIONS:
            await self._set_validator(database, name, AGENT_COLLECTION_VALIDATORS[name])
        await self._set_validator(
            database,
            "retrieval_logs",
            COLLECTION_VALIDATORS["retrieval_logs"],
        )
        for name in ACTOR_PROVENANCE_COLLECTIONS:
            await self._set_validator(database, name, COLLECTION_VALIDATORS[name])

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not invalidate existing Gemini-attributed durable records."""
        _ = database
        raise IrreversibleMigrationError(
            "Gemini client provenance cannot be removed without invalidating existing records"
        )

    @staticmethod
    async def _set_validator(
        database: AsyncDatabase[MongoDocument], name: str, validator: MongoDocument
    ) -> None:
        await database.command(
            {
                "collMod": name,
                "validator": validator,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
