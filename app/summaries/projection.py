"""Server allowlists keep free text, IDs from payloads and governance changes private."""

from typing import Any

from app.identity.repository import Row
from app.mcp.tools import TOOLS_BY_NAME
from app.summaries.models import Source

# Governance action names are fixed server declarations. No arbitrary action text
# or change/approval/target/email is serialized into summaries or CSV output.
GOVERNANCE_ACTIONS = frozenset(
    {
        "service_custody_approved",
        "access_request_cancelled",
        "managed_machine_access_activated",
        "legacy_adapter_approved",
        "legacy_key_verified_claim",
        "assignment_narrowed",
        "assignment_issued",
        "team_membership_changed",
        "service_reviewer_exception_approved",
        "membership_revoked",
        "bootstrap_activated",
        "managed_credential_issued",
        "management_invitation_accepted",
        "ownership_transfer_accepted",
        "managed_client_revoked",
        "team_changed",
        "service_account_revoked",
        "management_invitation_revoked",
        "ownership_transfer_offered",
        "access_requested",
        "membership_suspended",
        "assignment_revoked",
        "membership_active",
        "organization_renamed",
        "human_management_schema_activated",
        "managed_credential_revoked",
        "organization_auth_policy_changed",
        "management_invitation_created",
        "managed_machine_schema_prepared",
        "access_request_decided",
        "unmanaged_key_revoked",
        "owner_corpus_recovery",
        "client_template_updated",
        "human_management_root_approved",
        "managed_client_policy_approved",
        "team_created",
    }
)


def projection(source: Source) -> Row:
    if source == "gateway_activity":
        verified: Any = {
            "$and": [
                {"$ne": [{"$ifNull": ["$sponsor_id", None]}, None]},
                {"$gte": [{"$ifNull": ["$sponsorship_revision", 0]}, 1]},
            ]
        }
        fields: Row = {
            "action": {
                "$cond": [
                    {"$in": ["$operation", sorted(TOOLS_BY_NAME)]},
                    "$operation",
                    "unrecognized_operation",
                ]
            },
            "outcome": "$status",
            "actor_kind": {"$cond": [verified, "machine", "unknown"]},
            "actor_id": {"$cond": [verified, "$client_id", None]},
            "sponsor_id": {"$cond": [verified, "$sponsor_id", None]},
            "service_account_id": {
                "$cond": [verified, {"$ifNull": ["$service_account_id", None]}, None]
            },
            "team_id": {"$cond": [verified, {"$ifNull": ["$sponsor_team_id", None]}, None]},
            "project_id": {"$ifNull": ["$project_id", None]},
        }
    else:
        verified = {"$eq": ["$change._event_v1.verified", True]}
        fields = {
            "action": {
                "$cond": [
                    {"$in": ["$action", sorted(GOVERNANCE_ACTIONS)]},
                    "$action",
                    "other_governance",
                ]
            },
            "outcome": {"$literal": "committed"},
            "actor_kind": {"$cond": [verified, "$change._event_v1.actor_kind", "unknown"]},
            "actor_id": {"$cond": [verified, "$actor_id", None]},
            "sponsor_id": {"$literal": None},
            "service_account_id": {"$literal": None},
            "team_id": {"$cond": [verified, "$change._event_v1.team_id", None]},
            "project_id": {"$literal": None},
        }
    return {
        "_id": 0,
        "event_id": {"$toString": "$_id"},
        "occurred_at": "$created_at",
        "attribution": {"$cond": [verified, "verified_event_time", "legacy_unattributed"]},
        **fields,
    }
