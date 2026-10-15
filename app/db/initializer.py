"""Database collection, migration, and index initialization."""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.indexes import ensure_indexes
from app.db.migrations.runner import run_migrations
from app.db.tenant_schema import TENANT_INDEX_DEFINITIONS


async def initialize_database(
    database: AsyncDatabase[MongoDocument],
    *,
    tenant_foundation: bool = False,
    tenant_isolation: bool = False,
) -> None:
    """Apply schema migrations and reconcile standard indexes."""
    if not tenant_foundation:
        await run_migrations(database)
        await ensure_indexes(database)
        return
    await run_migrations(database, tenant_foundation=True, tenant_isolation=tenant_isolation)
    control = await database["_tenant_control"].find_one({"_id": "installation"})
    if control is not None and control.get("stage") != "not_adopted":
        # Never resurrect global unique indexes over a migrated database.
        if control.get("stage") in {"prepared", "isolating", "isolated"}:
            for name, definitions in TENANT_INDEX_DEFINITIONS.items():
                await database[name].create_indexes(list(definitions))
        return
    await ensure_indexes(database)
