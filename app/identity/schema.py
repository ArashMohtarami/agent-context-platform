"""Strict private identity schemas; TTL indexes are cleanup, never authorization."""

from typing import Any

from pymongo import IndexModel

from app.identity.repository import COLLECTION_OWNERSHIP

STRING = {"bsonType": "string", "minLength": 1, "maxLength": 4096}
INTEGER = {"bsonType": ["int", "long"], "minimum": 0}
DATE = {"bsonType": "date"}
OPTIONAL_DATE = {"bsonType": ["date", "null"]}
OPTIONAL_STRING = {"bsonType": ["string", "null"], "maxLength": 4096}
STRINGS = {"bsonType": "array", "maxItems": 100, "uniqueItems": True, "items": STRING}
BOOLEAN = {"bsonType": "bool"}


def schema(**fields: Any) -> dict[str, Any]:
    properties = {"_id": STRING, **fields}
    return {
        "$jsonSchema": {
            "bsonType": "object",
            "additionalProperties": False,
            "required": list(properties),
            "properties": properties,
        }
    }


VALIDATORS = {
    "users": schema(
        display_name=STRING,
        status={"enum": ["pending", "verification_pending", "active", "disabled"]},
        auth_epoch=INTEGER,
        fence=INTEGER,
        created_at=DATE,
    ),
    "user_identities": schema(
        user_id=STRING,
        provider={"enum": ["local"]},
        subject=STRING,
        verified_at=OPTIONAL_DATE,
        created_at=DATE,
    ),
    "local_credentials": schema(user_id=STRING, password_hash=STRING, updated_at=DATE),
    "mfa_factors": schema(
        user_id=STRING,
        factor_id=STRING,
        status={"enum": ["pending", "active", "disabled"]},
        encrypted_seed={
            "bsonType": "object",
            "additionalProperties": False,
            "required": ["key_id", "ciphertext"],
            "properties": {"key_id": STRING, "ciphertext": STRING},
        },
        recovery_hashes=STRINGS,
        last_step={"bsonType": ["int", "long"], "minimum": -1},
        revision=INTEGER,
        expires_at=OPTIONAL_DATE,
        created_at=DATE,
    ),
    "auth_challenges": schema(
        provider={"enum": ["local"]},
        purpose={"enum": ["invite", "verify", "reset", "login"]},
        user_id=STRING,
        org_id=OPTIONAL_STRING,
        browser_id=OPTIONAL_STRING,
        auth_epoch=INTEGER,
        membership_id=OPTIONAL_STRING,
        used_at=OPTIONAL_DATE,
        membership_revision=INTEGER,
        policy_version=INTEGER,
        created_at=DATE,
        expires_at=DATE,
    ),
    "browser_sessions": schema(
        public_id=STRING,
        kind={"enum": ["preauth", "human"]},
        user_id=OPTIONAL_STRING,
        org_id=OPTIONAL_STRING,
        membership_id=OPTIONAL_STRING,
        membership_revision=INTEGER,
        auth_epoch=INTEGER,
        policy_version=INTEGER,
        revoked=BOOLEAN,
        created_at=DATE,
        last_seen=DATE,
        idle_expires_at=DATE,
        expires_at=DATE,
        password_at=OPTIONAL_DATE,
        mfa_at=OPTIONAL_DATE,
        amr=STRINGS,
    ),
    "organization_memberships": schema(
        org_id=STRING,
        user_id=STRING,
        display_name=STRING,
        role={"enum": ["org_owner", "contributor"]},
        status={"enum": ["invited", "verification_pending", "active", "suspended", "revoked"]},
        verified_at=OPTIONAL_DATE,
        revision=INTEGER,
        fence=INTEGER,
        bootstrap_owner=BOOLEAN,
        bootstrap_intent={"bsonType": ["object", "null"]},
        created_at=DATE,
    ),
    "auth_throttles": schema(count=INTEGER, expires_at=DATE),
    "identity_audit_events": schema(
        action=STRING,
        user_id=OPTIONAL_STRING,
        org_id=OPTIONAL_STRING,
        target_id=OPTIONAL_STRING,
        approval_reference=OPTIONAL_STRING,
        actor={"bsonType": "object"},
        created_at=DATE,
    ),
    "_local_identity_control": schema(schema_version={"enum": [38]}, created_at=DATE),
}
if set(VALIDATORS) != set(COLLECTION_OWNERSHIP):
    raise RuntimeError("identity collection ownership inventory is incomplete")

INDEXES = {
    "user_identities": [
        IndexModel([("provider", 1), ("subject", 1)], unique=True, name="login_identity_uq")
    ],
    "local_credentials": [IndexModel("user_id", unique=True, name="local_user_uq")],
    "mfa_factors": [IndexModel("user_id", unique=True, name="mfa_user_uq")],
    "organization_memberships": [
        IndexModel([("org_id", 1), ("user_id", 1)], unique=True, name="org_user_uq"),
        IndexModel(
            "org_id",
            unique=True,
            partialFilterExpression={"bootstrap_owner": True},
            name="initial_owner_uq",
        ),
        IndexModel([("user_id", 1), ("status", 1)], name="own_memberships"),
    ],
    "browser_sessions": [
        IndexModel("public_id", unique=True, name="public_session_uq"),
        IndexModel([("user_id", 1), ("kind", 1)], name="own_sessions"),
        IndexModel("expires_at", expireAfterSeconds=0, name="session_cleanup"),
    ],
    "auth_challenges": [IndexModel("expires_at", expireAfterSeconds=0, name="challenge_cleanup")],
    "auth_throttles": [IndexModel("expires_at", expireAfterSeconds=0, name="throttle_cleanup")],
    "identity_audit_events": [
        IndexModel([("user_id", 1), ("created_at", -1)], name="identity_audit")
    ],
}
