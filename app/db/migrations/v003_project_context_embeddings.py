"""Phase 9 migration adding model-tagged embeddings to project context."""

from dataclasses import dataclass
from typing import Final

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.schema import (
    COLLECTION_VALIDATORS,
    IDENTIFIER,
    LIFECYCLE_STATUSES,
    TAGS,
    document_schema,
)

COLLECTION: Final = "project_context"
EMBEDDING_FIELDS: Final[tuple[str, ...]] = (
    "embedding",
    "embedding_model",
    "embedding_dimensions",
    "embedding_generated_at",
)
CONTEXT_TYPES: Final[list[str]] = [
    "architecture",
    "architecture_decision",
    "business_rule",
    "api",
    "database",
    "infrastructure",
    "deployment",
    "security",
    "integration",
    "convention",
    "troubleshooting",
    "known_issue",
    "implementation",
    "constraint",
]

# The validator this migration replaces, kept as a snapshot so the rollback does
# not depend on the current application schema.
PREVIOUS_VALIDATOR: Final[MongoDocument] = document_schema(
    [
        "project_id",
        "type",
        "title",
        "content",
        "importance",
        "tags",
        "source",
        "status",
        "external_id",
    ],
    {
        "project_id": IDENTIFIER,
        "type": {"enum": CONTEXT_TYPES},
        "title": {"bsonType": "string", "minLength": 1, "maxLength": 500},
        "content": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
        "importance": {"bsonType": "double", "minimum": 0.0, "maximum": 1.0},
        "tags": TAGS,
        "source": IDENTIFIER,
        "status": {"enum": LIFECYCLE_STATUSES},
        "external_id": {"bsonType": ["string", "null"]},
    },
)


@dataclass(frozen=True, slots=True)
class ProjectContextEmbeddingMigration:
    """Allow vectors and their model metadata to be stored with context."""

    version: int = 3
    description: str = "add embedding fields to project context"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Widen the validator, then backfill existing context as unembedded."""
        await _set_validator(database, COLLECTION_VALIDATORS[COLLECTION])
        await database[COLLECTION].update_many(
            {"embedding": {"$exists": False}},
            {"$set": dict.fromkeys(EMBEDDING_FIELDS, None)},
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Restore the previous validator and drop the derived vectors.

        Vectors are derived from the stored content, which this rollback leaves
        untouched, so no authored knowledge is lost.
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
