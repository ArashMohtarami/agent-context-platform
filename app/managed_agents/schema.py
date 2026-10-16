"""Strict managed authority storage, with tenant-first unique receipts."""

from pymongo import IndexModel

from app.identity.schema import DATE, INTEGER, OPTIONAL_STRING, STRING, schema
from app.managed_agents.inventory import COLLECTION_OWNERSHIP
from app.security.policy.schema import TUPLES

VALIDATORS = {
    "service_accounts": schema(
        org_id=STRING,
        service_account_id=STRING,
        name=STRING,
        custodian_id=STRING,
        custodian_revision=INTEGER,
        team_id=STRING,
        team_generation=INTEGER,
        team_link_revision=INTEGER,
        status={"enum": ["active", "revoked"]},
        revision=INTEGER,
        created_at=DATE,
        expires_at=DATE,
        created_by=STRING,
        reviewer={
            "oneOf": [
                {"bsonType": "null"},
                schema(
                    user_id=STRING,
                    membership_revision=INTEGER,
                    parent_id=STRING,
                    parent_generation=INTEGER,
                    expires_at=DATE,
                    tuples=TUPLES,
                )["$jsonSchema"],
            ]
        },
    ),
    "managed_clients": schema(
        org_id=STRING,
        client_id=STRING,
        subject_kind={"enum": ["human", "service_account"]},
        subject_id=STRING,
        sponsor_id=STRING,
        sponsor_revision=INTEGER,
        service_revision={"bsonType": ["int", "long", "null"]},
        team_id=OPTIONAL_STRING,
        team_generation={"bsonType": ["int", "long", "null"]},
        team_link_revision={"bsonType": ["int", "long", "null"]},
        parent_id=STRING,
        parent_generation=INTEGER,
        issuer_id=STRING,
        tuples=TUPLES,
        scopes={"bsonType": "array", "maxItems": 32, "items": STRING},
        profile_id=OPTIONAL_STRING,
        profile_revision={"bsonType": ["int", "long", "null"]},
        status={"enum": ["active", "revoked"]},
        revision=INTEGER,
        created_at=DATE,
        expires_at=DATE,
    ),
    "managed_credentials": schema(
        org_id=STRING,
        client_id=STRING,
        key_id=STRING,
        recipient_id=STRING,
        issuance_id=STRING,
        command_hash=STRING,
        client_revision=INTEGER,
        tuples=TUPLES,
        status={"enum": ["active", "revoked"]},
        revision=INTEGER,
        created_at=DATE,
        expires_at=DATE,
        replaces_key_id=OPTIONAL_STRING,
        claimed={"bsonType": "bool"},
    ),
    "_managed_agents_control": schema(
        schema_version={"enum": [42]}, activated={"bsonType": "bool"}, created_at=DATE
    ),
}
reviewer_schema = VALIDATORS["service_accounts"]["$jsonSchema"]["properties"]["reviewer"]["oneOf"][
    1
]
reviewer_schema["required"].remove("_id")
reviewer_schema["properties"].pop("_id")
if set(VALIDATORS) != set(COLLECTION_OWNERSHIP):
    raise RuntimeError("machine authority ownership inventory is incomplete")
INDEXES = {
    "service_accounts": [
        IndexModel(
            [("org_id", 1), ("service_account_id", 1)], unique=True, name="org_service_account_uq"
        )
    ],
    "managed_clients": [
        IndexModel([("org_id", 1), ("client_id", 1)], unique=True, name="org_managed_client_uq")
    ],
    "managed_credentials": [
        IndexModel([("org_id", 1), ("key_id", 1)], unique=True, name="org_managed_key_uq"),
        IndexModel(
            [("org_id", 1), ("client_id", 1), ("recipient_id", 1), ("issuance_id", 1)],
            unique=True,
            name="org_client_recipient_issuance_uq",
        ),
    ],
}
