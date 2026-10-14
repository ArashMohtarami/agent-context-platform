"""Opt-in Phase 1 storage contracts, separate from historical V1-V5 validators."""

from copy import deepcopy
from typing import Any, Final, cast

from pymongo import ASCENDING, IndexModel

from app.db.client_types import MongoDocument
from app.db.indexes import INDEX_DEFINITIONS
from app.db.schema import COLLECTION_VALIDATORS, IDENTIFIER, document_schema

TENANT_SCHEMA_VERSION: Final = 36
TENANT_COLLECTIONS: Final = tuple(sorted(COLLECTION_VALIDATORS))
INSTALLATION_COLLECTIONS: Final = (
    "_schema_migrations",
    "_mongot_health_probe",
    "organizations",
    "_tenant_control",
    "_tenant_preservation",
    "_tenant_quarantine",
)

ORGANIZATION_VALIDATOR: Final = document_schema(
    ["org_id", "identity_version", "name", "slug", "status", "auth_policy", "policy_version"],
    {
        "org_id": IDENTIFIER,
        "identity_version": {"enum": [2]},
        "legacy_adoption_id": {"bsonType": "null"},
        "name": {"bsonType": "string", "minLength": 1, "maxLength": 500},
        "slug": IDENTIFIER,
        "status": {"enum": ["sealed", "suspended"]},
        "auth_policy": {"enum": ["local_only", "sso_preferred", "sso_required"]},
        "policy_version": {"bsonType": ["int", "long"], "minimum": 1},
    },
)

# These are operator installation records, deliberately outside all domain APIs.
# Original BSON is stored as binary so arbitrary unknown source shapes survive.
MAINTENANCE_VALIDATORS: Final = {
    "_tenant_control": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "schema_version", "stage", "domain_traffic_enabled"],
            "properties": {
                "_id": {"enum": ["installation"]},
                "schema_version": {"enum": [TENANT_SCHEMA_VERSION]},
                "stage": {"enum": ["not_adopted", "adopting", "blocked", "prepared"]},
                "domain_traffic_enabled": {"enum": [False]},
            },
        }
    },
    **{
        name: {
            "$jsonSchema": {
                "bsonType": "object",
                "required": [
                    "_id",
                    "collection",
                    "document_id",
                    "adoption_id",
                    "original",
                    "sha256",
                ],
                "properties": {
                    "_id": {"bsonType": "string"},
                    "collection": {"bsonType": "string"},
                    "adoption_id": IDENTIFIER,
                    "original": {"bsonType": "binData"},
                    "sha256": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
                },
            }
        }
        for name in ("_tenant_preservation", "_tenant_quarantine")
    },
}


def tenant_validator(collection: str, *, org_id: str, adoption_id: str) -> MongoDocument:
    """Require ownership; pin legacy records to the chosen adoption org/manifest.

    Shape validators cannot compare old/new documents. Ownership immutability is
    also enforced by frozen models and repository write filters. DB operators
    remain trusted and can bypass validation; no physical isolation is claimed.
    """
    validator = cast(dict[str, Any], deepcopy(COLLECTION_VALIDATORS[collection]))
    schema = validator.get("$jsonSchema")
    if schema is None:
        schema = validator["$and"][0]["$jsonSchema"]
    schema["required"] += ["org_id", "identity_version"]
    schema["properties"].update(
        {
            "org_id": deepcopy(IDENTIFIER),
            "identity_version": {"bsonType": ["int", "long"], "enum": [1, 2]},
            "legacy_adoption_id": {"bsonType": ["string", "null"], "maxLength": 128},
        }
    )
    return {
        "$and": [
            validator,
            {
                "$or": [
                    {"identity_version": 1, "org_id": org_id, "legacy_adoption_id": adoption_id},
                    {"identity_version": 2, "legacy_adoption_id": None},
                ]
            },
        ]
    }


def tenant_index_definitions() -> dict[str, tuple[IndexModel, ...]]:
    definitions: dict[str, tuple[IndexModel, ...]] = {}
    for collection, indexes in INDEX_DEFINITIONS.items():
        converted = []
        for index in indexes:
            options = dict(index.document)
            keys = list(options.pop("key").items())
            # TTL indexes are intentionally single-field cleanup indexes. A key
            # ID remains globally unique for initial bearer credential resolution.
            if "expireAfterSeconds" not in options and not (
                collection == "api_keys" and keys == [("key_id", ASCENDING)]
            ):
                keys.insert(0, ("org_id", ASCENDING))
                options["name"] = "tenant_" + options["name"]
            converted.append(IndexModel(keys, **options))
        definitions[collection] = tuple(converted)
    return definitions


TENANT_INDEX_DEFINITIONS: Final = tenant_index_definitions()
