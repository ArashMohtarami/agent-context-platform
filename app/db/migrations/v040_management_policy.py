"""Opt-in managed authority and a resumable old-worker fence; no automatic grants."""

from copy import deepcopy

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.identity.schema import DATE, INTEGER, schema
from app.identity.sso_schema import SESSION_VALIDATOR
from app.models.common import utc_now
from app.security.policy.schema import INDEXES, MEMBERSHIP_VALIDATOR, NEW_VALIDATORS


class ManagementPolicyMigration:
    version = 40
    description = "Action-resource assignments, bounded delegation and shared policy fencing"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        if not await database["_sso_identity_control"].find_one(
            {"_id": "installation", "schema_version": {"$in": [39, 40]}}
        ):
            raise RuntimeError("verified Phase 4 identity schema is required")
        control = await database["_tenant_control"].find_one({"_id": "installation"})
        if not control or control.get("stage") not in {"isolated", "managed"}:
            raise RuntimeError("verified tenant isolation must precede managed policy")
        names = set(await database.list_collection_names())
        marker = await database["_management_policy_control"].find_one({"_id": "installation"})
        if marker is None:
            for name in NEW_VALIDATORS:
                if name in names and await database[name].count_documents({}):
                    raise RuntimeError("reserved governance storage contains unclassified data")
        # Preserve the earlier validator and add a new admitted runtime stage.
        # Old schema-37 bearer/job workers require stage=isolated and now refuse.
        info = await (await database.list_collections(filter={"name": "_tenant_control"})).to_list(
            1
        )
        validator = deepcopy(info[0]["options"]["validator"])
        base = validator["$and"][0]["$jsonSchema"]
        base["properties"]["stage"]["enum"] = [
            "prepared",
            "isolating",
            "isolated",
            "managed",
            "blocked",
        ]
        validator["$and"][1]["$or"].append(
            {"schema_version": 37, "stage": "managed", "worker_schema_versions": [40]}
        )
        await database.command(
            {
                "collMod": "_tenant_control",
                "validator": validator,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        await database["_tenant_control"].update_one(
            {"_id": "installation"}, {"$set": {"stage": "managed", "worker_schema_versions": [40]}}
        )
        for name in ("_local_identity_control", "_sso_identity_control"):
            await database.command(
                {
                    "collMod": name,
                    "validator": schema(schema_version={"enum": [40]}, created_at=DATE),
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
            await database[name].update_one(
                {"_id": "installation"}, {"$set": {"schema_version": 40}}
            )
        session_validator = deepcopy(SESSION_VALIDATOR)
        session_validator["$jsonSchema"]["properties"]["policy_fence"] = INTEGER
        for name, validation in {
            **NEW_VALIDATORS,
            "organization_memberships": MEMBERSHIP_VALIDATOR,
            "browser_sessions": session_validator,
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
        await database["_management_policy_control"].update_one(
            {"_id": "installation"},
            {"$setOnInsert": {"schema_version": 40, "created_at": utc_now()}},
            upsert=True,
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        raise IrreversibleMigrationError(
            "policy recovery requires a verified sealed operator restore"
        )
