"""Migration contract and non-destructive rollback error."""

from typing import Protocol

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument


class IrreversibleMigrationError(RuntimeError):
    """Raised when a safe automatic down migration does not exist."""


class Migration(Protocol):
    """A versioned database schema change."""

    @property
    def version(self) -> int:
        """Return the monotonically increasing migration version."""
        ...

    @property
    def description(self) -> str:
        """Return a human-readable migration description."""
        ...

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Apply the migration."""
        ...

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Reverse the migration when doing so is safe."""
        ...
