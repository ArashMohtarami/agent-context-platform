"""Resumable stopped-writer cutover; old policy workers cannot ignore team lineage."""

from copy import deepcopy

from pymongo import IndexModel
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.identity.schema import DATE, schema
from app.management.schema import ASSIGNMENT_VALIDATOR, CREDENTIAL_VALIDATOR, INDEXES, VALIDATORS
from app.models.common import utc_now


class HumanManagementMigration:
    version = 41
    description = "Tenant human management, reviewed invitations and immutable access decisions"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        marker = await database["_management_policy_control"].find_one(
            {"_id": "installation", "schema_version": {"$in": [40, 41]}}
        )
        control = await database["_tenant_control"].find_one(
            {"_id": "installation", "stage": "managed"}
        )
        if not marker or not control or control.get("worker_schema_versions") not in ([40], [41]):
            raise RuntimeError("Phase 5 policy and stopped schema-40 writers are required")
        names = set(await database.list_collection_names())
        if not await database["_human_management_control"].find_one({"_id": "installation"}):
            for name in VALIDATORS:
                if name in names and await database[name].count_documents({}):
                    raise RuntimeError(
                        "reserved human management storage contains unclassified data"
                    )
        info = await (await database.list_collections(filter={"name": "_tenant_control"})).to_list(
            1
        )
        validator = deepcopy(info[0]["options"]["validator"])
        clause = {"schema_version": 37, "stage": "managed", "worker_schema_versions": [41]}
        if clause not in validator["$and"][1]["$or"]:
            validator["$and"][1]["$or"].append(clause)
        await database.command(
            {
                "collMod": "_tenant_control",
                "validator": validator,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        await database["_tenant_control"].update_one(
            {"_id": "installation"}, {"$set": {"worker_schema_versions": [41]}}
        )
        for name, validation in {
            **VALIDATORS,
            "policy_assignments": ASSIGNMENT_VALIDATOR,
            "policy_credentials": CREDENTIAL_VALIDATOR,
        }.items():
            if name not in names:
                await database.create_collection(name)
            await database.command(
                {
                    "collMod": name,
                    "validator": validation,
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
            if name in INDEXES:
                await database[name].create_indexes(INDEXES[name])
        # The Phase 5 legacy job resolver does not call enabled(). Changing
        # this discriminator makes its exact old mode check fail, even for a
        # queued/replayed adopted job. No key ID, scope or review is broadened.
        await database["policy_credentials"].update_many(
            {"mode": "legacy_adoption"},
            {"$set": {"mode": "legacy_adoption_v41"}, "$inc": {"revision": 1}},
        )
        await database["organization_memberships"].create_indexes(
            [
                IndexModel(
                    [("org_id", 1), ("display_name", 1), ("user_id", 1)],
                    name="org_member_directory",
                )
            ]
        )
        for name in (
            "_local_identity_control",
            "_sso_identity_control",
            "_management_policy_control",
        ):
            await database.command(
                {
                    "collMod": name,
                    "validator": schema(schema_version={"enum": [41]}, created_at=DATE),
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
            await database[name].update_one(
                {"_id": "installation"}, {"$set": {"schema_version": 41}}
            )
        await database["_human_management_control"].update_one(
            {"_id": "installation"},
            {"$setOnInsert": {"schema_version": 41, "created_at": utc_now()}},
            upsert=True,
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        raise IrreversibleMigrationError(
            "human governance recovery requires a verified sealed operator restore"
        )
