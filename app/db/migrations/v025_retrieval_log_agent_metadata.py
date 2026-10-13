"""Add nullable MCP client provenance to opt-in retrieval logs."""

from copy import deepcopy
from typing import cast

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS

_FIELDS = ("client_id", "client_type", "session_id")


def _intermediate_validator() -> MongoDocument:
    """Allow legacy retrieval records until their nullable fields are backfilled."""
    validator = deepcopy(COLLECTION_VALIDATORS["retrieval_logs"])
    schema = cast(MongoDocument, validator["$jsonSchema"])
    required = cast(list[str], schema["required"])
    schema["required"] = [field for field in required if field not in _FIELDS]
    return validator


class RetrievalLogAgentMetadataMigration:
    """Backfill additive client metadata without rewriting historical logs."""

    @property
    def version(self) -> int:
        return 25

    @property
    def description(self) -> str:
        return "add nullable agent provenance to retrieval logs"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        await database.command(
            {
                "collMod": "retrieval_logs",
                "validator": _intermediate_validator(),
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        for field in _FIELDS:
            await database["retrieval_logs"].update_many(
                {field: {"$exists": False}}, {"$set": {field: None}}
            )
        await database.command(
            {
                "collMod": "retrieval_logs",
                "validator": COLLECTION_VALIDATORS["retrieval_logs"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        _ = database
        raise IrreversibleMigrationError("retrieval log client provenance cannot be safely removed")
