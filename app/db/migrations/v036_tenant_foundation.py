"""Explicitly opted-in tenant registry; adoption is a separate operator action."""

from pymongo import IndexModel
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import CollectionInvalid

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.tenant_schema import MAINTENANCE_VALIDATORS, ORGANIZATION_VALIDATOR
from app.repositories.tenant_adoption_repository import AdoptionConflict


class TenantFoundationMigration:
    version = 36
    description = "Sealed organization registry and explicit tenant adoption control"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        names = set(await database.list_collection_names())
        marker = await database["_tenant_control"].find_one({"_id": "installation"})
        if marker is None or marker.get("schema_version") != self.version:
            # A reserved spelling is not proof that old data was installation
            # metadata. Refuse before changing any such collection or its rows.
            for name in ("organizations", *MAINTENANCE_VALIDATORS):
                if name in names and await database[name].count_documents({}) != 0:
                    raise AdoptionConflict(
                        "reserved tenant metadata contains unclassified legacy data"
                    )
        if "organizations" not in names:
            try:
                await database.create_collection(
                    "organizations",
                    validator=ORGANIZATION_VALIDATOR,
                    validationLevel="strict",
                    validationAction="error",
                )
            except CollectionInvalid:
                pass
        await database.command(
            {
                "collMod": "organizations",
                "validator": ORGANIZATION_VALIDATOR,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        await database["organizations"].create_indexes(
            [
                IndexModel("org_id", unique=True, name="organization_id_uq"),
                IndexModel("slug", unique=True, name="organization_slug_uq"),
            ]
        )
        for name, validator in MAINTENANCE_VALIDATORS.items():
            if name not in names:
                await database.create_collection(
                    name, validator=validator, validationLevel="strict", validationAction="error"
                )
            else:
                await database.command(
                    {
                        "collMod": name,
                        "validator": validator,
                        "validationLevel": "strict",
                        "validationAction": "error",
                    }
                )
        await database["_tenant_control"].update_one(
            {"_id": "installation"},
            {
                "$setOnInsert": {
                    "schema_version": 36,
                    "stage": "not_adopted",
                    "domain_traffic_enabled": False,
                }
            },
            upsert=True,
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        raise IrreversibleMigrationError(
            "tenant adoption requires an isolated verified full restore"
        )
