"""Explicit, sealed V6 tenant runtime contracts after compatible adoption."""

from copy import deepcopy
from secrets import token_bytes
from typing import Any, cast

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.migrations.base import IrreversibleMigrationError
from app.db.tenant_schema import (
    MAINTENANCE_VALIDATORS,
    ORGANIZATION_VALIDATOR,
    TENANT_COLLECTIONS,
    tenant_validator,
)
from app.repositories.tenant_adoption_repository import preservation_id

RUNTIME_VERSION = 37


def runtime_validator(collection: str, org_id: str, adoption_id: str) -> MongoDocument:
    validator = cast(
        dict[str, Any], tenant_validator(collection, org_id=org_id, adoption_id=adoption_id)
    )

    def extend(node: dict[str, Any]) -> None:
        if "$jsonSchema" in node:
            schema = node["$jsonSchema"]
            fields = (
                ("project_mode", "profile_mode")
                if collection == "api_keys"
                else ("project_mode",)
                if collection == "agent_clients"
                else ()
            )
            for field in fields:
                schema["required"].append(field)
                schema["properties"][field] = {"enum": ["none", "include", "all_in_org"]}
            if collection.startswith("content_"):
                schema["required"].append("resource_project_ids")
                schema["properties"]["resource_project_ids"] = {
                    "bsonType": "array",
                    "items": {"bsonType": "string"},
                }
            if collection == "gateway_idempotency":
                schema["properties"]["policy_hash"] = {"bsonType": ["string", "null"]}
            if collection == "ingestion_jobs":
                schema["properties"]["initiating_authority"] = {"bsonType": ["object", "null"]}
            return
        for child in node.get("$and", []):
            extend(child)

    extend(validator)
    modes = (
        (("project_mode", "projects"), ("profile_mode", "profiles"))
        if collection == "api_keys"
        else (("project_mode", "project_ids"),)
        if collection == "agent_clients"
        else ()
    )
    for mode, ids in modes:
        validator["$and"].append(
            {
                "$or": [
                    {mode: {"$in": ["none", "all_in_org"]}, ids: {"$size": 0}},
                    {mode: "include", f"{ids}.0": {"$exists": True}},
                ]
            }
        )
    return cast(MongoDocument, validator)


class TenantIsolationMigration:
    version = RUNTIME_VERSION
    description = "Sealed tenant runtime validators, explicit resource modes and cursor signing"

    async def up(self, database: AsyncDatabase[MongoDocument]) -> None:
        control = await database["_tenant_control"].find_one({"_id": "installation"})
        if not control or control.get("stage") not in {"prepared", "isolating"}:
            raise RuntimeError(
                "compatible Phase 1 adoption must finish before tenant runtime migration"
            )
        org_id, adoption_id = str(control["adoption_org_id"]), str(control["adoption_id"])
        metadata_validator = deepcopy(
            cast(dict[str, Any], MAINTENANCE_VALIDATORS["_tenant_control"])
        )
        properties = metadata_validator["$jsonSchema"]["properties"]
        properties["schema_version"] = {"enum": [36, 37]}
        properties["stage"] = {"enum": ["prepared", "isolating", "isolated", "blocked"]}
        properties["domain_traffic_enabled"] = {"bsonType": "bool"}
        metadata_validator = {
            "$and": [
                metadata_validator,
                {
                    "$or": [
                        {"domain_traffic_enabled": False},
                        {"schema_version": 37, "stage": "isolated", "worker_schema_versions": [37]},
                    ]
                },
            ]
        }
        await database.command(
            {
                "collMod": "_tenant_control",
                "validator": metadata_validator,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        await database["_tenant_control"].update_one(
            {"_id": "installation"},
            {"$set": {"stage": "isolating", "domain_traffic_enabled": False}},
        )
        organizations = deepcopy(cast(dict[str, Any], ORGANIZATION_VALIDATOR))
        organizations["$jsonSchema"]["properties"]["status"] = {
            "enum": ["sealed", "active", "suspended"]
        }
        await database.command(
            {
                "collMod": "organizations",
                "validator": organizations,
                "validationLevel": "strict",
                "validationAction": "error",
            }
        )
        for name in TENANT_COLLECTIONS:
            # Admit both shapes while explicitly sealed. Resume cannot open traffic.
            new = runtime_validator(name, org_id, adoption_id)
            await database.command(
                {
                    "collMod": name,
                    "validator": {
                        "$or": [tenant_validator(name, org_id=org_id, adoption_id=adoption_id), new]
                    },
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
            if name.startswith("content_"):
                from app.db.tenant_resources import content_projects

                async for content in database[name].find({}):
                    derived = await content_projects(database, name, content)
                    await database[name].update_one(
                        {"_id": content["_id"], "org_id": content["org_id"]},
                        {"$set": {"resource_project_ids": derived}},
                    )
            if name in {"api_keys", "agent_clients"}:
                async for row in database[name].find({}):
                    organization = await database["organizations"].find_one(
                        {"org_id": row.get("org_id")}
                    )
                    if organization is None:
                        raise RuntimeError("an authority record has no registered organization")
                    modes = ("projects", "profiles") if name == "api_keys" else ("projects",)
                    preserved = await database["_tenant_preservation"].find_one(
                        {"_id": preservation_id(name, row["_id"])}
                    )
                    changes: dict[str, object] = {}
                    for kind in modes:
                        field = "project_mode" if kind == "projects" else "profile_mode"
                        ids = "project_ids" if name == "agent_clients" else kind
                        if field in row:
                            continue
                        legacy_modes = (
                            preserved.get("legacy_resource_modes", {}) if preserved else {}
                        )
                        if row.get("identity_version") == 1 and (
                            not isinstance(legacy_modes, dict) or kind not in legacy_modes
                        ):
                            raise RuntimeError(
                                "legacy ALL requires its Phase 1 preservation manifest"
                            )
                        changes[field] = "include" if row.get(ids) else "all_in_org"
                    if changes:
                        await database[name].update_one(
                            {"_id": row["_id"], "org_id": row["org_id"]}, {"$set": changes}
                        )
            await database.command(
                {
                    "collMod": name,
                    "validator": new,
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
        await database["_tenant_control"].update_one(
            {"_id": "installation"},
            {
                "$set": {
                    "schema_version": 37,
                    "cursor_secret": control.get("cursor_secret", token_bytes(32)),
                    "domain_traffic_enabled": False,
                }
            },
        )

    async def down(self, database: AsyncDatabase[MongoDocument]) -> None:
        raise IrreversibleMigrationError(
            "tenant runtime rollback requires an isolated verified restore"
        )
