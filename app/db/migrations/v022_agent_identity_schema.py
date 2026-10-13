"""V4 Phase 0 additive client-identity schema migration."""

from typing import Final

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import CollectionInvalid

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

V4_AGENT_COLLECTIONS: Final[tuple[str, ...]] = (
    "agent_clients",
    "agent_sessions",
    "project_bindings",
    "agent_activity",
)


class AgentIdentitySchemaMigration:
    """Create the V4 identity collections without changing V1--V3 records."""

    @property
    def version(self) -> int:
        return 22

    @property
    def description(self) -> str:
        return "create V4 agent identity collections"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Create missing collections or restore their strict validators."""
        existing = set(await database.list_collection_names())
        for name in V4_AGENT_COLLECTIONS:
            validator = COLLECTION_VALIDATORS[name]
            if name in existing:
                await _set_validator(database, name, validator)
                continue
            try:
                await database.create_collection(
                    name,
                    validator=validator,
                    validationLevel="strict",
                    validationAction="error",
                )
            except CollectionInvalid:
                # Another startup may have won the create race.  Reconcile its
                # validator rather than treating idempotent startup as failure.
                await _set_validator(database, name, validator)

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Refuse to erase identities, bindings, or operational history."""
        _ = database
        raise IrreversibleMigrationError(
            "V4 agent identity rollback would discard durable client history"
        )


async def _set_validator(
    database: AsyncDatabase[MongoDocument], name: str, validator: MongoDocument
) -> None:
    """Reapply the strict validator without touching existing documents."""
    await database.command(
        {
            "collMod": name,
            "validator": validator,
            "validationLevel": "strict",
            "validationAction": "error",
        }
    )
