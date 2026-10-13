"""V4 Phase 0 follow-up migration for the finalized identity contract."""

from copy import deepcopy
from typing import cast

from pymongo.asynchronous.database import AsyncDatabase

from app.db.agent_indexes import PROJECT_BINDING_MAPPING_UNIQUE_INDEX
from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.migrations.v022_agent_identity_schema import V4_AGENT_COLLECTIONS
from app.db.schema import COLLECTION_VALIDATORS


def _session_validator_without_end_time() -> MongoDocument:
    """Allow Phase 0 session rows while their new nullable field is backfilled."""
    validator = deepcopy(COLLECTION_VALIDATORS["agent_sessions"])
    schema = cast(MongoDocument, validator["$jsonSchema"])
    required = cast(list[str], schema["required"])
    schema["required"] = [field for field in required if field != "ended_at"]
    return validator


class AgentIdentityContractMigration:
    """Finalize V4 Phase 0 identity fields without weakening V1--V3 schemas."""

    @property
    def version(self) -> int:
        return 23

    @property
    def description(self) -> str:
        return "finalize V4 agent identity lifecycle and binding constraints"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Backfill nullable session end times and enforce one binding mapping."""
        await _set_validator(
            database,
            "agent_sessions",
            _session_validator_without_end_time(),
        )
        await database["agent_sessions"].update_many(
            {"ended_at": {"$exists": False}},
            {"$set": {"ended_at": None}},
        )
        for name in V4_AGENT_COLLECTIONS:
            await _set_validator(database, name, COLLECTION_VALIDATORS[name])
        # Fail closed if a legacy installation already holds an ambiguous
        # mapping.  Repairing it needs an explicit administrator decision;
        # this migration must never pick a project silently.
        await database["project_bindings"].create_indexes([PROJECT_BINDING_MAPPING_UNIQUE_INDEX])

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Do not discard lifecycle history or reintroduce ambiguous bindings."""
        _ = database
        raise IrreversibleMigrationError(
            "V4 agent identity lifecycle and binding constraints cannot be safely removed"
        )


async def _set_validator(
    database: AsyncDatabase[MongoDocument], name: str, validator: MongoDocument
) -> None:
    """Apply one strict validator without modifying durable content."""
    await database.command(
        {
            "collMod": name,
            "validator": validator,
            "validationLevel": "strict",
            "validationAction": "error",
        }
    )
