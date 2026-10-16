"""Strict tenant-owned policy storage, deliberately outside corpus repositories."""

from copy import deepcopy

from pymongo import IndexModel

from app.identity.models import MANAGEMENT_ACTIONS
from app.identity.schema import DATE, INTEGER, OPTIONAL_STRING, STRING, STRINGS, VALIDATORS, schema
from app.security.policy.catalog import HUMAN_ROLES
from app.security.policy.inventory import COLLECTION_OWNERSHIP
from app.security.scopes import ALL_SCOPES

SELECTOR = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["mode", "ids"],
    "properties": {
        "mode": {"enum": ["none", "include", "all_in_org"]},
        "ids": {**STRINGS, "maxItems": 50},
    },
}
TUPLE = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["action", "resource_kind", "selector", "ids", "conditions"],
    "properties": {
        "action": {"enum": [*ALL_SCOPES, *sorted(MANAGEMENT_ACTIONS)]},
        "resource_kind": {
            "enum": [
                "organization",
                "team",
                "member",
                "project_directory",
                "project",
                "organization_knowledge",
                "creator_profile",
                "client",
                "client_memory",
                "skill",
                "research_project",
            ]
        },
        "selector": {"enum": ["none", "include", "all_in_org"]},
        "ids": {**STRINGS, "maxItems": 50},
        "conditions": {"bsonType": "object", "maxProperties": 0},
    },
}
for shape, field in ((SELECTOR, "mode"), (TUPLE, "selector")):
    shape["oneOf"] = [
        {"properties": {field: {"enum": ["none", "all_in_org"]}, "ids": {"maxItems": 0}}},
        {"properties": {field: {"enum": ["include"]}, "ids": {"minItems": 1}}},
    ]
TUPLES = {"bsonType": "array", "maxItems": 100, "items": TUPLE}
NEW_VALIDATORS = {
    "policy_anchors": schema(
        org_id=STRING,
        revision=INTEGER,
        fence=INTEGER,
        status={"enum": ["active", "suspended"]},
        created_at=DATE,
    ),
    "policy_assignments": schema(
        org_id=STRING,
        subject_kind={"enum": ["human"]},
        subject_id=STRING,
        subject_revision=INTEGER,
        role={"enum": list(HUMAN_ROLES)},
        tuples=TUPLES,
        ceiling=TUPLES,
        ceiling_hash=STRING,
        targets=SELECTOR,
        allowed_roles={**STRINGS, "items": {"enum": list(HUMAN_ROLES)}, "maxItems": 8},
        remaining_depth={"bsonType": ["int", "long"], "minimum": 0, "maximum": 3},
        parent_id=OPTIONAL_STRING,
        parent_generation=INTEGER,
        issuer_id=STRING,
        generation=INTEGER,
        revision=INTEGER,
        status={"enum": ["active", "revoked"]},
        starts_at=DATE,
        expires_at=DATE,
        created_at=DATE,
    ),
    "policy_credentials": schema(
        org_id=STRING,
        key_id=STRING,
        mode={"enum": ["legacy_adoption"]},
        revision=INTEGER,
        status={"enum": ["active", "revoked"]},
        envelope_hash=STRING,
        expires_at=DATE,
        approval_reference=STRING,
    ),
    "governance_events": schema(
        org_id=STRING,
        actor_id=STRING,
        action=STRING,
        target_id=STRING,
        approval_reference=OPTIONAL_STRING,
        change={"bsonType": "object"},
        created_at=DATE,
    ),
    "governance_outbox": schema(
        org_id=STRING, event_id=STRING, status={"enum": ["pending", "delivered"]}, created_at=DATE
    ),
    "ownership_transfers": schema(
        org_id=STRING,
        owner_id=STRING,
        target_id=STRING,
        owner_revision=INTEGER,
        target_revision=INTEGER,
        policy_revision=INTEGER,
        status={"enum": ["pending", "accepted"]},
        expires_at=DATE,
    ),
    "review_authorship": schema(
        org_id=STRING, collection=STRING, document_id=STRING, sponsor_ids=STRINGS, created_at=DATE
    ),
    "_management_policy_control": schema(schema_version={"enum": [40]}, created_at=DATE),
}
if set(NEW_VALIDATORS) != set(COLLECTION_OWNERSHIP):
    raise RuntimeError("governance storage ownership is incomplete")
MEMBERSHIP_VALIDATOR = deepcopy(VALIDATORS["organization_memberships"])
MEMBERSHIP_VALIDATOR["$jsonSchema"]["properties"]["role"] = {"enum": list(HUMAN_ROLES)}
INDEXES = {
    "policy_anchors": [IndexModel("org_id", unique=True, name="org_policy_anchor_uq")],
    "policy_assignments": [
        IndexModel([("org_id", 1), ("subject_id", 1)], name="subject_policy"),
        IndexModel([("org_id", 1), ("parent_id", 1)], name="policy_lineage"),
    ],
    "policy_credentials": [
        IndexModel([("org_id", 1), ("key_id", 1)], unique=True, name="reviewed_credential_uq")
    ],
    "governance_events": [
        IndexModel([("org_id", 1), ("created_at", -1)], name="org_governance_history")
    ],
    "governance_outbox": [
        IndexModel([("org_id", 1), ("status", 1)], name="org_governance_delivery")
    ],
    "ownership_transfers": [IndexModel([("org_id", 1), ("target_id", 1)], name="transfer_target")],
    "review_authorship": [
        IndexModel(
            [("org_id", 1), ("collection", 1), ("document_id", 1)],
            unique=True,
            name="review_authorship_uq",
        )
    ],
}
