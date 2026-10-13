"""V4 Phase 1 extension of the existing API-key credential records."""

from copy import deepcopy
from typing import cast

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS


def _intermediate_validator() -> MongoDocument:
    validator = deepcopy(COLLECTION_VALIDATORS["api_keys"])
    schema = cast(MongoDocument, validator["$jsonSchema"])
    required = cast(list[str], schema["required"])
    schema["required"] = [field for field in required if field not in {"client_id", "revoked_at"}]
    return validator


class AgentCredentialMigration:
    """Attach optional client identity to legacy-compatible API keys."""

    @property
    def version(self) -> int:
        return 24

    @property
    def description(self) -> str:
        return "link API keys to V4 agent clients with revocation metadata"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        await database.command(
            {
                "collMod": "api_keys",
                "validator": _intermediate_validator(),
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        for field in ("client_id", "revoked_at"):
            await database["api_keys"].update_many(
                {field: {"$exists": False}}, {"$set": {field: None}}
            )
        await database.command(
            {
                "collMod": "api_keys",
                "validator": COLLECTION_VALIDATORS["api_keys"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        _ = database
        raise IrreversibleMigrationError("agent credential linkage cannot be safely removed")
