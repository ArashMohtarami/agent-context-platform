"""Initial Phase 3 validated collection migration."""

from dataclasses import dataclass

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.collections import ensure_collections
from app.db.migrations.base import IrreversibleMigrationError

V1_COLLECTIONS = (
    "skills",
    "skill_chunks",
    "projects",
    "project_context",
    "memories",
    "conversations",
    "retrieval_logs",
)


@dataclass(frozen=True, slots=True)
class InitialSchemaMigration:
    """Create the seven validated application collections."""

    version: int = 1
    description: str = "create Phase 3 application collections"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Create absent collections using the current strict validators."""
        await ensure_collections(database, collection_names=V1_COLLECTIONS)

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Refuse an automatic destructive rollback."""
        _ = database
        raise IrreversibleMigrationError("initial schema rollback would delete application data")
