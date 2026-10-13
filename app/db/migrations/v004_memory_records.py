"""Phase 10 migration adding embeddings and supersession links to memories."""

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

COLLECTION: Final = "memories"
# A memory gains a vector, a short summary, and both ends of the supersession
# link. All four are derived or optional, so existing rows backfill as null.
ADDED_FIELDS: Final[tuple[str, ...]] = (
    "embedding",
    "embedding_model",
    "embedding_dimensions",
    "embedding_generated_at",
    "summary",
    "supersedes",
    "superseded_by",
)

# The validator this migration replaces, kept as a snapshot so the rollback does
# not depend on the current application schema.
PREVIOUS_VALIDATOR: Final[MongoDocument] = {
    "$and": [
        document_schema(
            [
                "scope",
                "project_id",
                "type",
                "content",
                "importance",
                "confidence",
                "tags",
                "source",
                "status",
                "external_id",
                "last_used_at",
            ],
            {
                "scope": {"enum": ["global", "project"]},
                "project_id": {"bsonType": ["string", "null"]},
                "type": IDENTIFIER,
                "content": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
                "importance": {"bsonType": "double", "minimum": 0.0, "maximum": 1.0},
                "confidence": {"bsonType": "double", "minimum": 0.0, "maximum": 1.0},
                "tags": TAGS,
                "source": IDENTIFIER,
                "status": {"enum": LIFECYCLE_STATUSES},
                "external_id": {"bsonType": ["string", "null"]},
                "last_used_at": {"bsonType": ["date", "null"]},
            },
        ),
        {
            "$or": [
                {"scope": "global", "project_id": None},
                {"scope": "project", "project_id": {"$type": "string"}},
            ]
        },
    ]
}


@dataclass(frozen=True, slots=True)
class MemoryRecordMigration:
    """Make memories retrievable and let one memory replace another."""

    version: int = 4
    description: str = "add embeddings, summary, and supersession links to memories"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Widen the validator, then backfill existing memories with nulls."""
        await _set_validator(database, COLLECTION_VALIDATORS[COLLECTION])
        await database[COLLECTION].update_many(
            {"embedding": {"$exists": False}},
            {"$set": dict.fromkeys(ADDED_FIELDS, None)},
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Restore the previous validator and drop the added fields.

        The rollback removes derived vectors and the supersession links, but
        never a memory: a superseded record keeps its content and its status,
        so no decision's history is destroyed.
        """
        await _set_validator(database, PREVIOUS_VALIDATOR)
        await database[COLLECTION].update_many(
            {},
            {"$unset": dict.fromkeys(ADDED_FIELDS, "")},
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
