"""Guarded SSO schema, no implicit enrollment or grant activation."""

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import CollectionInvalid

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.identity.schema import DATE, schema
from app.identity.sso_schema import (
    IDENTITY_VALIDATOR,
    INDEXES,
    NEW_VALIDATORS,
    SESSION_VALIDATOR,
)
from app.models.common import utc_now


class SSOIdentityMigration:
    version = 39
    description = "Approved OIDC providers, exact identity linking and organization switch"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        if not await database["_local_identity_control"].find_one(
            {"_id": "installation", "schema_version": {"$in": [38, 39]}}
        ):
            raise RuntimeError("local identity schema 38 is required before SSO")
        names = set(await database.list_collection_names())
        marker = await database["_sso_identity_control"].find_one({"_id": "installation"})
        if marker is None:
            for name in NEW_VALIDATORS:
                if name in names and await database[name].count_documents({}):
                    raise RuntimeError("reserved SSO storage contains unclassified data")
        # Fence schema-38 browser workers at their existing per-request marker
        # check before enabling OIDC evidence. This is also a resumable barrier.
        await database.command(
            {
                "collMod": "_local_identity_control",
                "validator": schema(schema_version={"enum": [39]}, created_at=DATE),
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        await database["_local_identity_control"].update_one(
            {"_id": "installation"}, {"$set": {"schema_version": 39}}
        )
        for name, validator in {
            **NEW_VALIDATORS,
            "browser_sessions": SESSION_VALIDATOR,
            "user_identities": IDENTITY_VALIDATOR,
        }.items():
            if name not in names:
                try:
                    await database.create_collection(name)
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
        if marker is None:
            # Cutover deliberately logs out schema-38 sessions, preserving their history.
            await database["browser_sessions"].update_many({}, {"$set": {"revoked": True}})
        await database["_sso_identity_control"].update_one(
            {"_id": "installation"},
            {"$setOnInsert": {"schema_version": 39, "created_at": utc_now()}},
            upsert=True,
        )
        from app.db.migrations.v037_tenant_isolation import runtime_validator
        from app.db.tenant_schema import TENANT_COLLECTIONS

        control = await database["_tenant_control"].find_one({"_id": "installation"})
        if not control:
            raise RuntimeError("tenant control is required")
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
        raise IrreversibleMigrationError("SSO recovery requires a verified operator restore")
