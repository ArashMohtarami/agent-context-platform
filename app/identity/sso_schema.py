"""Schema 39 additions; schema 38 remains a separate, reproducible migration."""

from copy import deepcopy

from pymongo import IndexModel

from app.identity.repository import SSO_COLLECTION_OWNERSHIP
from app.identity.schema import (
    DATE,
    INTEGER,
    OPTIONAL_DATE,
    OPTIONAL_STRING,
    STRING,
    STRINGS,
    VALIDATORS,
    schema,
)

NEW_VALIDATORS = {
    "organization_oidc": schema(
        org_id=STRING,
        registration_id=STRING,
        registration_version=STRING,
        approval_reference=STRING,
        created_at=DATE,
    ),
    "oidc_transactions": schema(
        purpose={"enum": ["login", "link", "switch", "reauth", "logout"]},
        org_id=STRING,
        registration_id=STRING,
        registration_version=STRING,
        policy_version=INTEGER,
        browser_id=STRING,
        session_id=OPTIONAL_STRING,
        user_id=OPTIONAL_STRING,
        auth_epoch=INTEGER,
        nonce=STRING,
        encrypted_verifier={"bsonType": "object"},
        used_at=OPTIONAL_DATE,
        created_at=DATE,
        expires_at=DATE,
    ),
    "organization_break_glass": schema(
        org_id=STRING,
        user_id=STRING,
        membership_revision=INTEGER,
        policy_version=INTEGER,
        approval_reference=STRING,
        expires_at=DATE,
        created_at=DATE,
    ),
    "_sso_identity_control": schema(schema_version={"enum": [39]}, created_at=DATE),
}
if set(NEW_VALIDATORS) != set(SSO_COLLECTION_OWNERSHIP):
    raise RuntimeError("SSO ownership inventory is incomplete")

AUTHENTICATION = schema(
    method={"enum": ["local", "oidc"]},
    identity_id=STRING,
    registration_id=OPTIONAL_STRING,
    registration_version=OPTIONAL_STRING,
    issuer=OPTIONAL_STRING,
    client_id=OPTIONAL_STRING,
    auth_time=DATE,
    valid_until=DATE,
    assurance_version=INTEGER,
    acr=OPTIONAL_STRING,
    verified_amr=STRINGS,
)["$jsonSchema"]
AUTHENTICATION["properties"].pop("_id")
AUTHENTICATION["required"].remove("_id")
SESSION_VALIDATOR = deepcopy(VALIDATORS["browser_sessions"])
SESSION_VALIDATOR["$jsonSchema"]["properties"]["authentication"] = {
    "anyOf": [{"bsonType": "null"}, AUTHENTICATION]
}
IDENTITY_VALIDATOR = deepcopy(VALIDATORS["user_identities"])
IDENTITY_VALIDATOR["$jsonSchema"]["properties"]["provider"] = STRING
INDEXES = {
    "organization_oidc": [IndexModel("org_id", unique=True, name="org_oidc_uq")],
    "organization_break_glass": [
        IndexModel([("org_id", 1), ("user_id", 1)], unique=True, name="org_emergency_uq")
    ],
    "oidc_transactions": [IndexModel("expires_at", expireAfterSeconds=0, name="oidc_cleanup")],
}
