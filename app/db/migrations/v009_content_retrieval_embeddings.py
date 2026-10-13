"""V2 Phase 4 migration for vectors on current content representations."""

from typing import Final

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

EMBEDDABLE_CONTENT_COLLECTIONS: Final[tuple[str, ...]] = (
    "content_ideas",
    "content_briefs",
    "content_items",
)


class ContentRetrievalEmbeddingMigration:
    """Add nullable embedding metadata without rewriting content or history.

    Only the three current, searchable representations gain fields.  Immutable
    ``content_versions`` deliberately remains untouched: versions are history,
    not a vector corpus.
    """

    @property
    def version(self) -> int:
        return 9

    @property
    def description(self) -> str:
        return "add retrieval embeddings to current content representations"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Backfill null metadata then enable the additive strict validator."""
        for collection in EMBEDDABLE_CONTENT_COLLECTIONS:
            await database[collection].update_many(
                {"embedding": {"$exists": False}},
                {
                    "$set": {
                        "embedding": None,
                        "embedding_model": None,
                        "embedding_dimensions": None,
                        "embedding_generated_at": None,
                    }
                },
            )
            await database.command(
                {
                    "collMod": collection,
                    "validator": COLLECTION_VALIDATORS[collection],
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not discard vectors that may be expensive to recreate."""
        _ = database
        raise IrreversibleMigrationError("content retrieval vectors cannot be safely removed")
