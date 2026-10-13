"""Backfill explicit system provenance before enforcing V4 actor contracts."""

from typing import Final

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.migrations.v006_creator_content_schema import V2_CONTENT_COLLECTIONS
from app.db.migrations.v013_research_schema import V3_RESEARCH_COLLECTIONS
from app.db.schema import COLLECTION_VALIDATORS
from app.models.actor import system_actor

_COLLECTIONS: Final[tuple[str, ...]] = (
    "project_context",
    "memories",
    "audit_logs",
    *V2_CONTENT_COLLECTIONS,
    *V3_RESEARCH_COLLECTIONS,
)
_LEGACY_ACTOR: Final[MongoDocument] = system_actor().model_dump(mode="python")


class ActorProvenanceMigration:
    """Preserve pre-V4 records while making provenance mandatory going forward."""

    @property
    def version(self) -> int:
        return 28

    @property
    def description(self) -> str:
        return "add system-backed actor provenance to V1, V2, V3, and audit writes"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Apply validators first, then backfill only records without an actor."""
        for name in _COLLECTIONS:
            await database.command(
                {
                    "collMod": name,
                    "validator": COLLECTION_VALIDATORS[name],
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
            await database[name].update_many(
                {"actor": {"$exists": False}},
                {"$set": {"actor": _LEGACY_ACTOR}},
            )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not erase durable actor history on rollback."""
        _ = database
        raise IrreversibleMigrationError(
            "actor provenance cannot be removed without discarding audit history"
        )
