"""Phase 15 migration creating the API key and audit collections.

Both are new collections rather than changes to existing ones, so this adds and
the rollback removes. The rollback is safe in one direction only: dropping
`api_keys` destroys every issued grant, which cannot be reconstructed because
the secrets were never stored. It therefore refuses to run while any key
exists, rather than quietly locking a deployment out of its own platform.
"""

from typing import Final

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

COLLECTIONS: Final[tuple[str, ...]] = ("api_keys", "audit_logs")


class ApiKeyAndAuditMigration:
    """Create the collections authentication and its audit trail need."""

    @property
    def version(self) -> int:
        """Return the migration version."""
        return 5

    @property
    def description(self) -> str:
        """Return what this migration does."""
        return "create the api_keys and audit_logs collections"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Create both collections with strict validators."""
        existing = set(await database.list_collection_names())
        for name in COLLECTIONS:
            if name in existing:
                await _set_validator(database, name, COLLECTION_VALIDATORS[name])
                continue
            await database.create_collection(
                name,
                validator=COLLECTION_VALIDATORS[name],
                validationLevel="strict",
                validationAction="error",
            )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Drop both collections, refusing while any key would be destroyed.

        An issued key cannot be recreated: the secret was shown once and only
        its hash was kept. Rolling back past this point is therefore a
        deliberate act that has to start with revoking what exists.
        """
        existing = set(await database.list_collection_names())
        if "api_keys" in existing and await database["api_keys"].count_documents({}) > 0:
            raise IrreversibleMigrationError(
                "api_keys still holds issued keys; a rollback would destroy grants "
                "whose secrets cannot be reissued"
            )
        for name in COLLECTIONS:
            if name in existing:
                await database.drop_collection(name)


async def _set_validator(
    database: AsyncDatabase[MongoDocument],
    collection: str,
    validator: MongoDocument,
) -> None:
    """Apply one collection validator without weakening enforcement."""
    await database.command(
        {
            "collMod": collection,
            "validator": validator,
            "validationLevel": "strict",
            "validationAction": "error",
        }
    )
