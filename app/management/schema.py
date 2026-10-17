"""Strict authority schemas and tenant-first indexes. Expiry is checked in code."""

from copy import deepcopy

from pymongo import IndexModel

from app.identity.schema import DATE, INTEGER, OPTIONAL_DATE, OPTIONAL_STRING, STRING, schema
from app.management.inventory import COLLECTION_OWNERSHIP
from app.security.policy.schema import NEW_VALIDATORS, SELECTOR, TUPLES

POLICY = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": [
        "role",
        "parent_id",
        "expected_parent_generation",
        "tuples",
        "ceiling",
        "targets",
        "allowed_roles",
        "remaining_depth",
        "expires_at",
        "team_id",
        "expected_team_generation",
    ],
    "properties": {
        "role": NEW_VALIDATORS["policy_assignments"]["$jsonSchema"]["properties"]["role"],
        "parent_id": STRING,
        "expected_parent_generation": INTEGER,
        "tuples": TUPLES,
        "ceiling": TUPLES,
        "targets": SELECTOR,
        "allowed_roles": NEW_VALIDATORS["policy_assignments"]["$jsonSchema"]["properties"][
            "allowed_roles"
        ],
        "remaining_depth": {"bsonType": ["int", "long"], "minimum": 0, "maximum": 2},
        "expires_at": DATE,
        "team_id": OPTIONAL_STRING,
        "expected_team_generation": {"bsonType": ["int", "long", "null"], "minimum": 1},
    },
}
VALIDATORS = {
    "teams": schema(
        org_id=STRING,
        team_id=STRING,
        name=STRING,
        status={"enum": ["active", "suspended"]},
        revision=INTEGER,
        generation=INTEGER,
        created_at=DATE,
    ),
    "team_memberships": schema(
        org_id=STRING,
        team_id=STRING,
        user_id=STRING,
        status={"enum": ["active", "suspended", "revoked"]},
        revision=INTEGER,
        created_at=DATE,
    ),
    "management_invitations": schema(
        org_id=STRING,
        user_id=STRING,
        membership_id=STRING,
        membership_revision=INTEGER,
        issuer_id=STRING,
        policy=POLICY,
        token_hash=STRING,
        status={"enum": ["pending", "accepted", "revoked"]},
        revision=INTEGER,
        created_at=DATE,
        expires_at=DATE,
        accepted_at=OPTIONAL_DATE,
        assignment_id=OPTIONAL_STRING,
    ),
    "access_requests": schema(
        org_id=STRING,
        user_id=STRING,
        membership_revision=INTEGER,
        policy=POLICY,
        policy_revision=INTEGER,
        status={"enum": ["pending", "approved", "denied", "cancelled"]},
        revision=INTEGER,
        created_at=DATE,
        expires_at=DATE,
    ),
    "access_decisions": schema(
        org_id=STRING,
        request_id=STRING,
        request_revision=INTEGER,
        policy_revision=INTEGER,
        actor_id=STRING,
        outcome={"enum": ["approved", "denied"]},
        assignment_id=OPTIONAL_STRING,
        created_at=DATE,
    ),
    "_human_management_control": schema(schema_version={"enum": [41]}, created_at=DATE),
}
CREDENTIAL_VALIDATOR = deepcopy(NEW_VALIDATORS["policy_credentials"])
CREDENTIAL_VALIDATOR["$jsonSchema"]["properties"]["mode"] = {"enum": ["legacy_adoption_v41"]}
ASSIGNMENT_VALIDATOR = deepcopy(NEW_VALIDATORS["policy_assignments"])
ASSIGNMENT_VALIDATOR["$jsonSchema"]["properties"].update(
    {
        "team_id": STRING,
        "team_generation": INTEGER,
        "team_link_revision": INTEGER,
        "team_delegation": {"bsonType": "bool"},
    }
)
if set(VALIDATORS) != set(COLLECTION_OWNERSHIP):
    raise RuntimeError("human management storage ownership is incomplete")
INDEXES = {
    "teams": [IndexModel([("org_id", 1), ("team_id", 1)], unique=True, name="org_team_uq")],
    "team_memberships": [
        IndexModel(
            [("org_id", 1), ("team_id", 1), ("user_id", 1)], unique=True, name="org_team_user_uq"
        ),
        IndexModel([("org_id", 1), ("user_id", 1), ("status", 1)], name="member_teams"),
    ],
    "management_invitations": [
        IndexModel(
            [("org_id", 1), ("membership_id", 1)],
            unique=True,
            partialFilterExpression={"status": "pending"},
            name="one_pending_member_invitation_uq",
        ),
        IndexModel([("org_id", 1), ("token_hash", 1)], unique=True, name="org_invite_challenge_uq"),
        IndexModel([("org_id", 1), ("user_id", 1), ("status", 1)], name="member_invitations"),
    ],
    "access_requests": [
        IndexModel([("org_id", 1), ("user_id", 1), ("_id", 1)], name="member_access_requests")
    ],
    "access_decisions": [
        IndexModel(
            [("org_id", 1), ("request_id", 1)], unique=True, name="immutable_request_decision_uq"
        )
    ],
}
