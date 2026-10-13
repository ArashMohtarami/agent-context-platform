""" "Phase 9 migration for durable, explicit content publication receipts."""

from copy import deepcopy
from typing import cast

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

_PUBLICATION_FIELDS = (
    "publication_provider",
    "external_id",
    "publication_idempotency_key",
)


def _intermediate_content_item_validator() -> MongoDocument:
    """Allow new nullable properties while legacy items are backfilled."""
    validator = deepcopy(COLLECTION_VALIDATORS["content_items"])
    schema = cast(MongoDocument, validator["$jsonSchema"])
    required = cast(list[str], schema["required"])
    schema["required"] = [field for field in required if field not in _PUBLICATION_FIELDS]
    return validator


class ContentPublicationReceiptMigration:
    """Add provider receipts without rewriting content bodies or V1 collections."""

    @property
    def version(self) -> int:
        return 11

    @property
    def description(self) -> str:
        return "add content publication receipt fields and publish audit action"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Backfill nullable receipt fields, then reconcile strict validators."""
        await database.command(
            {
                "collMod": "content_items",
                "validator": _intermediate_content_item_validator(),
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        for field in _PUBLICATION_FIELDS:
            await database["content_items"].update_many(
                {field: {"$exists": False}},
                {"$set": {field: None}},
            )
        await database.command(
            {
                "collMod": "content_items",
                "validator": COLLECTION_VALIDATORS["content_items"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        await database.command(
            {
                "collMod": "audit_logs",
                "validator": COLLECTION_VALIDATORS["audit_logs"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not delete auditable provider receipts or publication decisions."""
        _ = database
        raise IrreversibleMigrationError(
            "content publication receipts and audit records cannot be safely removed"
        )
