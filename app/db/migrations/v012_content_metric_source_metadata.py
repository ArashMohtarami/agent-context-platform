"""Phase 10 migration for generic, source-attributed content metrics."""

from copy import deepcopy
from typing import cast

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

_METRIC_FIELDS = ("source", "provider_metadata")


def _intermediate_content_metric_validator() -> MongoDocument:
    """Permit legacy observations until every row has Phase 10 fields."""
    validator = deepcopy(COLLECTION_VALIDATORS["content_metrics"])
    schema = cast(MongoDocument, validator["$jsonSchema"])
    required = cast(list[str], schema["required"])
    schema["required"] = [field for field in required if field not in _METRIC_FIELDS]
    return validator


class ContentMetricSourceMigration:
    """Backfill generic metric source metadata without touching V1 collections."""

    @property
    def version(self) -> int:
        return 12

    @property
    def description(self) -> str:
        return "add generic source and provider metadata to content metrics"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Backfill defaults before reapplying the strict Phase 10 validator."""
        await database.command(
            {
                "collMod": "content_metrics",
                "validator": _intermediate_content_metric_validator(),
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        await database["content_metrics"].update_many(
            {"source": {"$exists": False}},
            {"$set": {"source": "manual"}},
        )
        await database["content_metrics"].update_many(
            {"provider_metadata": {"$exists": False}},
            {"$set": {"provider_metadata": {}}},
        )
        await database.command(
            {
                "collMod": "content_metrics",
                "validator": COLLECTION_VALIDATORS["content_metrics"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not discard durable source provenance from metric observations."""
        _ = database
        raise IrreversibleMigrationError("content metric source metadata cannot be safely removed")
