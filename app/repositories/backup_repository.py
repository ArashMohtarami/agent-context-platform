"""MongoDB reads and cleanup used by backup and restore workflows."""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument


class MongoBackupRepository:
    """Keep backup-specific MongoDB access out of the application service."""

    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        self._database = database

    async def collection_counts(self) -> dict[str, int]:
        """Return every collection count, including all additive V1--V4 collections.

        Backups use a database-level archive, so this inventory intentionally
        enumerates rather than whitelists collections: newly migrated persistent
        collections are captured and verified without a second backup registry.
        """
        counts: dict[str, int] = {}
        for name in sorted(await self._database.list_collection_names()):
            counts[name] = await self._database[name].count_documents({})
        return counts

    async def restored_document_count(self, prefix: str) -> int:
        """Count documents restored under one isolated collection prefix."""
        total = 0
        for name in await self._database.list_collection_names():
            if name.startswith(prefix):
                total += await self._database[name].count_documents({})
        return total

    async def discard_restored(self, prefix: str) -> None:
        """Drop only collections created under one restore-check prefix."""
        for name in await self._database.list_collection_names():
            if name.startswith(prefix):
                await self._database.drop_collection(name)
