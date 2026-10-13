"""V3 Phase 14 additive freshness metadata migration.

The migration creates no collection. It backfills inert defaults only, then
restores strict validators for promoted V1 knowledge and V3 candidates.
"""

from copy import deepcopy
from typing import Final, cast

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

_CANDIDATE_FIELDS: Final = (
    "freshness_class",
    "review_after",
    "revalidation_of_candidate_id",
    "revalidation_reasons",
)
_PROVENANCE_FIELDS: Final = (
    "source_snapshot_ids",
    "review_after",
    "freshness_class",
)


def _json_schema(validator: MongoDocument) -> MongoDocument:
    """Return the document schema for direct and conditional V1 validators."""
    if "$jsonSchema" in validator:
        return cast(MongoDocument, validator["$jsonSchema"])
    branches = cast(list[MongoDocument], validator["$and"])
    return cast(MongoDocument, branches[0]["$jsonSchema"])


def _intermediate_validator(collection: str) -> MongoDocument:
    """Admit legacy rows while only the additive defaults are being written."""
    validator = deepcopy(COLLECTION_VALIDATORS[collection])
    schema = _json_schema(validator)
    required = cast(list[str], schema["required"])
    if collection == "knowledge_candidates":
        schema["required"] = [field for field in required if field not in _CANDIDATE_FIELDS]
        return validator
    properties = cast(MongoDocument, schema["properties"])
    provenance = cast(MongoDocument, properties["promotion_provenance"])
    nested_required = cast(list[str], provenance["required"])
    provenance["required"] = [field for field in nested_required if field not in _PROVENANCE_FIELDS]
    return validator


class RevalidationFreshnessMigration:
    """Backfill Phase 14 metadata without changing knowledge content or lifecycle."""

    @property
    def version(self) -> int:
        return 21

    @property
    def description(self) -> str:
        return "add freshness and revalidation metadata to research-derived knowledge"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Permit additive fields, backfill them, and reapply strict validation."""
        collections = ("knowledge_candidates", "memories", "project_context")
        for collection in collections:
            await database.command(
                {
                    "collMod": collection,
                    "validator": _intermediate_validator(collection),
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )

        candidate_defaults: Final[dict[str, object]] = {
            "freshness_class": "stable",
            "review_after": None,
            "revalidation_of_candidate_id": None,
            "revalidation_reasons": [],
        }
        for field, value in candidate_defaults.items():
            await database["knowledge_candidates"].update_many(
                {field: {"$exists": False}},
                {"$set": {field: value}},
            )

        provenance_defaults: Final[dict[str, object]] = {
            "source_snapshot_ids": [],
            "review_after": None,
            "freshness_class": "stable",
        }
        for collection in ("memories", "project_context"):
            for field, value in provenance_defaults.items():
                await database[collection].update_many(
                    {
                        "promotion_provenance": {"$type": "object"},
                        f"promotion_provenance.{field}": {"$exists": False},
                    },
                    {"$set": {f"promotion_provenance.{field}": value}},
                )

        for collection in collections:
            await database.command(
                {
                    "collMod": collection,
                    "validator": COLLECTION_VALIDATORS[collection],
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Never discard retained snapshot provenance or revalidation history."""
        _ = database
        raise IrreversibleMigrationError(
            "research-derived freshness metadata cannot be safely removed"
        )
