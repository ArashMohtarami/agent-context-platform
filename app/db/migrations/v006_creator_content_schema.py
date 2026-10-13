"""V2 Phase 0 migration for creator and publishing persistence.

The migration is additive.  It creates only V2 collections and never changes
the shape or names of V1 knowledge collections.
"""

from typing import Final

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

V2_CONTENT_COLLECTIONS: Final[tuple[str, ...]] = (
    "creator_profiles",
    "content_pillars",
    "content_ideas",
    "content_briefs",
    "content_items",
    "content_versions",
    "content_links",
    "content_metrics",
    "content_campaigns",
)


class CreatorContentSchemaMigration:
    """Create V2 Phase 0 collections and reconcile their validators."""

    @property
    def version(self) -> int:
        return 6

    @property
    def description(self) -> str:
        return "create V2 creator and content collections"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Create each V2 collection or restore its strict validator.

        Reapplying a validator to an existing collection keeps startup
        idempotent and catches a manually weakened validation setting without
        touching its documents.
        """
        existing = set(await database.list_collection_names())
        for name in V2_CONTENT_COLLECTIONS:
            validator = COLLECTION_VALIDATORS[name]
            if name in existing:
                await _set_validator(database, name, validator)
                continue
            await database.create_collection(
                name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Refuse a rollback that could discard creator data or provenance."""
        _ = database
        raise IrreversibleMigrationError(
            "V2 creator/content rollback would delete content history and provenance"
        )


async def _set_validator(
    database: AsyncDatabase[MongoDocument],
    collection: str,
    validator: MongoDocument,
) -> None:
    """Set one strict validator without downgrading enforcement."""
    await database.command(
        {
            "collMod": collection,
            "validator": validator,
            "validationLevel": "strict",
            "validationAction": "error",
        }
    )
