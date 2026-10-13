"""Phase 5 migration adding model-tagged embeddings to skill chunks."""

from dataclasses import dataclass
from typing import Final

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.schema import COLLECTION_VALIDATORS, IDENTIFIER, METADATA, document_schema

COLLECTION: Final = "skill_chunks"
EMBEDDING_FIELDS: Final[tuple[str, ...]] = (
    "embedding",
    "embedding_model",
    "embedding_dimensions",
    "embedding_generated_at",
)

# The validator this migration replaces, kept as a snapshot so the rollback does
# not depend on the current application schema.
PREVIOUS_VALIDATOR: Final[MongoDocument] = document_schema(
    [
        "skill_id",
        "skill_version",
        "chunk_id",
        "content",
        "section",
        "chunk_index",
        "tokens",
        "metadata",
    ],
    {
        "skill_id": IDENTIFIER,
        "skill_version": {"bsonType": "string", "minLength": 5, "maxLength": 64},
        "chunk_id": IDENTIFIER,
        "content": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
        "section": {"bsonType": ["string", "null"]},
        "chunk_index": {"bsonType": ["int", "long"], "minimum": 0},
        "tokens": {"bsonType": ["int", "long"], "minimum": 0, "maximum": 100_000},
        "metadata": METADATA,
    },
)


@dataclass(frozen=True, slots=True)
class SkillChunkEmbeddingMigration:
    """Allow vectors and their model metadata to be stored with skill chunks."""

    version: int = 2
    description: str = "add embedding fields to skill chunks"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Widen the validator, then backfill existing chunks as unembedded."""
        await _set_validator(database, COLLECTION_VALIDATORS[COLLECTION])
        await database[COLLECTION].update_many(
            {"embedding": {"$exists": False}},
            {"$set": dict.fromkeys(EMBEDDING_FIELDS, None)},
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Restore the previous validator and drop the derived vectors.

        Vectors are derived data that a skill re-import regenerates, so this
        rollback loses no canonical knowledge.
        """
        await _set_validator(database, PREVIOUS_VALIDATOR)
        await database[COLLECTION].update_many(
            {},
            {"$unset": dict.fromkeys(EMBEDDING_FIELDS, "")},
        )


async def _set_validator(
    database: AsyncDatabase[MongoDocument],
    validator: MongoDocument,
) -> None:
    """Apply one collection validator without weakening enforcement."""
    await database.command(
        {
            "collMod": COLLECTION,
            "validator": validator,
            "validationLevel": "strict",
            "validationAction": "error",
        }
    )
