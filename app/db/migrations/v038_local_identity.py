"""Private local identity, after the verified Phase 2 installation gate."""

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import CollectionInvalid

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.identity.schema import INDEXES, VALIDATORS
from app.models.common import utc_now


class LocalIdentityMigration:
    version = 38
    description = "Private human identity, verified memberships and opaque browser sessions"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        control = await database["_tenant_control"].find_one({"_id": "installation"})
        if not control or not (
            control.get("stage") == "isolated"
            and control.get("schema_version") == 37
            and control.get("domain_traffic_enabled") is True
            and control.get("worker_schema_versions") == [37]
        ):
            raise RuntimeError("verified Phase 2 isolation must be activated before local identity")
        names = set(await database.list_collection_names())
        marker = await database["_local_identity_control"].find_one({"_id": "installation"})
        if marker is None:
            for name in VALIDATORS:
                if name in names and await database[name].count_documents({}):
                    raise RuntimeError("reserved identity storage contains unclassified data")
        for name, validator in VALIDATORS.items():
            if name not in names:
                try:
                    await database.create_collection(
                        name,
                        validator=validator,
                        validationLevel="strict",
                        validationAction="error",
                    )
                except CollectionInvalid:
                    pass
            await database.command(
                {
                    "collMod": name,
                    "validator": validator,
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
            if name in INDEXES:
                await database[name].create_indexes(INDEXES[name])
        await database["_local_identity_control"].update_one(
            {"_id": "installation"},
            {"$setOnInsert": {"schema_version": 38, "created_at": utc_now()}},
            upsert=True,
        )
        # Extend actor shapes without rewriting historical actors or ObjectIds.
        from app.db.migrations.v037_tenant_isolation import runtime_validator
        from app.db.tenant_schema import TENANT_COLLECTIONS

        for name in TENANT_COLLECTIONS:
            await database.command(
                {
                    "collMod": name,
                    "validator": runtime_validator(
                        name, str(control["adoption_org_id"]), str(control["adoption_id"])
                    ),
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        raise IrreversibleMigrationError("identity recovery requires a verified operator restore")
