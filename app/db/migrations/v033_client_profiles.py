"""Add V5 client profiles without changing V1--V4 behaviour."""

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import CollectionInvalid

from app.db.agent_schema import (
    AGENT_COLLECTION_VALIDATORS,
    agent_client_validator_without_profile_id,
)
from app.db.client_profile_schema import CLIENT_PROFILE_COLLECTION
from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.schema import COLLECTION_VALIDATORS


class ClientProfileMigration:
    """Create generic profile storage and backfill nullable client references.

    The profile reference is nullable so all existing Codex and Claude clients
    retain their exact V4 authorization behaviour.  Profiles do not replace or
    dynamically reinterpret the already-authoritative client scope snapshot.
    """

    @property
    def version(self) -> int:
        return 33

    @property
    def description(self) -> str:
        return "add V5 generic client profiles and nullable client profile references"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Create the additive collection and migrate strict V4 clients safely."""
        await database.command(
            {
                "collMod": "agent_clients",
                "validator": agent_client_validator_without_profile_id(),
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        await database["agent_clients"].update_many(
            {"profile_id": {"$exists": False}}, {"$set": {"profile_id": None}}
        )
        await database.command(
            {
                "collMod": "agent_clients",
                "validator": AGENT_COLLECTION_VALIDATORS["agent_clients"],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )

        existing = set(await database.list_collection_names())
        if CLIENT_PROFILE_COLLECTION in existing:
            await self._set_profile_validator(database)
            return
        try:
            await database.create_collection(
                CLIENT_PROFILE_COLLECTION,
                validator=COLLECTION_VALIDATORS[CLIENT_PROFILE_COLLECTION],
                validationLevel="strict",
                validationAction="error",
            )
        except CollectionInvalid:
            await self._set_profile_validator(database)

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Refuse to destroy durable profile history or assignment provenance."""
        _ = database
        raise IrreversibleMigrationError(
            "V5 client profiles and profile assignments cannot be removed safely"
        )

    @staticmethod
    async def _set_profile_validator(database: AsyncDatabase[MongoDocument]) -> None:
        await database.command(
            {
                "collMod": CLIENT_PROFILE_COLLECTION,
                "validator": COLLECTION_VALIDATORS[CLIENT_PROFILE_COLLECTION],
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
