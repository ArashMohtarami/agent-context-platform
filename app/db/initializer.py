"""Database collection, migration, and index initialization."""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.indexes import ensure_indexes
from app.db.migrations.runner import run_migrations


async def initialize_database(database: AsyncDatabase[MongoDocument]) -> None:
    """Apply schema migrations and reconcile standard indexes."""
    await run_migrations(database)
    await ensure_indexes(database)
