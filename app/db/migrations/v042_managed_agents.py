"""Explicit stopped-writer schema42 cutover. Legacy keys never receive sponsorship."""

from copy import deepcopy
from typing import Any, cast

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.identity.schema import DATE, schema
from app.managed_agents.schema import INDEXES, VALIDATORS
from app.models.common import utc_now


class ManagedAgentsMigration:
    version = 42
    description = "Verified machine sponsorship, service custody and secret-free issuance receipts"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        marker = await database["_human_management_control"].find_one(
            {"_id": "installation", "schema_version": {"$in": [41, 42]}}
        )
        control = await database["_tenant_control"].find_one(
            {
                "_id": "installation",
                "stage": "managed",
                "worker_schema_versions": {"$in": [[41], [42]]},
            }
        )
        if not marker or not control:
            raise RuntimeError("verified Phase6 and stopped schema41 writers required")
        names = set(await database.list_collection_names())
        existing = await database["_managed_agents_control"].find_one({"_id": "installation"})
        if not existing:
            for name in VALIDATORS:
                if name in names and await database[name].count_documents({}):
                    raise RuntimeError("reserved machine authority storage is unclassified")
        info = await (await database.list_collections(filter={"name": "_tenant_control"})).to_list(
            1
        )
        validation = deepcopy(info[0]["options"]["validator"])
        clause = {"schema_version": 37, "stage": "managed", "worker_schema_versions": [42]}
        if clause not in validation["$and"][1]["$or"]:
            validation["$and"][1]["$or"].append(clause)
        await database.command(
            {
                "collMod": "_tenant_control",
                "validator": validation,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        await database["_tenant_control"].update_one(
            {"_id": "installation"}, {"$set": {"worker_schema_versions": [42]}}
        )
        for name, validator in VALIDATORS.items():
            if name not in names:
                await database.create_collection(name)
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
        # A former adapter must not continue through the old resolver/job path.
        await database["policy_credentials"].update_many(
            {"status": "active"}, {"$set": {"status": "revoked"}, "$inc": {"revision": 1}}
        )
        info = await (await database.list_collections(filter={"name": "agent_clients"})).to_list(1)
        validation = deepcopy(info[0]["options"]["validator"])

        def extend(node: Any) -> None:
            if isinstance(node, dict):
                if "$jsonSchema" in node:
                    node["$jsonSchema"]["properties"]["sponsorship_id"] = {
                        "bsonType": ["string", "null"],
                        "maxLength": 128,
                    }
                for value in node.values():
                    extend(value)
            elif isinstance(node, list):
                for value in node:
                    extend(value)

        extend(validation)
        await database.command(
            {
                "collMod": "agent_clients",
                "validator": validation,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        from app.db.provenance_schema import ACTOR_PROVENANCE
        from app.db.tenant_schema import TENANT_COLLECTIONS

        def actors(node: Any) -> None:
            if isinstance(node, dict):
                if "actor" in node.get("properties", {}):
                    node["properties"]["actor"] = deepcopy(ACTOR_PROVENANCE)
                for value in node.values():
                    actors(value)
            elif isinstance(node, list):
                for value in node:
                    actors(value)

        for name in TENANT_COLLECTIONS:
            info = await (await database.list_collections(filter={"name": name})).to_list(1)
            validation = deepcopy(info[0]["options"]["validator"])
            actors(validation)
            if name == "agent_activity":
                validation["$and"][0]["$jsonSchema"]["properties"].update(
                    {
                        key: deepcopy(cast(dict[str, Any], ACTOR_PROVENANCE["properties"])[key])
                        for key in (
                            "sponsor_id",
                            "sponsor_team_id",
                            "service_account_id",
                            "sponsorship_revision",
                        )
                    }
                )
            if name == "agent_sessions":
                validation["$and"][0]["$jsonSchema"]["properties"]["policy_hash"] = {
                    "bsonType": ["string", "null"],
                    "maxLength": 64,
                }
            await database.command(
                {
                    "collMod": name,
                    "validator": validation,
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
        for name in (
            "_local_identity_control",
            "_sso_identity_control",
            "_management_policy_control",
            "_human_management_control",
        ):
            await database.command(
                {
                    "collMod": name,
                    "validator": schema(schema_version={"enum": [42]}, created_at=DATE),
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
            await database[name].update_one(
                {"_id": "installation"}, {"$set": {"schema_version": 42}}
            )
        await database["_managed_agents_control"].update_one(
            {"_id": "installation"},
            {"$setOnInsert": {"schema_version": 42, "activated": False, "created_at": utc_now()}},
            upsert=True,
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        raise IrreversibleMigrationError(
            "machine authority recovery requires sealed verified operator restore"
        )
