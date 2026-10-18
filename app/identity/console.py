"""Own-account console DTOs. Private identity rows never enter an org directory."""

from datetime import timedelta

from app.identity.repository import Row
from app.identity.service import LocalIdentity
from app.models.common import utc_now


async def security(local: LocalIdentity, row: Row) -> Row:
    await local.sessions.authority(row)
    user_id = row["user_id"]
    factor = await local.store.find("mfa_factors", {"_id": user_id, "status": "active"})
    credential = await local.store.find("local_credentials", {"_id": user_id})
    identities = await local.store.list(
        "user_identities", {"user_id": user_id, "verified_at": {"$ne": None}}
    )
    return {
        "auth_method": (row.get("authentication") or {}).get("method", "local"),
        "assurance": local.assurance(row),
        "mfa_enrolled": factor is not None,
        "local_password_available": credential is not None,
        "local_enrollment_allowed": row["password_at"] is not None
        and timedelta(0) <= utc_now() - row["password_at"] <= timedelta(minutes=10),
        "step_up_required": row["mfa_at"] is None
        or not timedelta(0) <= utc_now() - row["mfa_at"] <= timedelta(minutes=10),
        "recovery_codes_remaining": len(factor["recovery_hashes"]) if factor else 0,
        "identities": [
            {
                "kind": "local" if identity["provider"] == "local" else "oidc",
                "issuer": None if identity["provider"] == "local" else identity["provider"],
                "verified_at": identity["verified_at"],
            }
            for identity in identities[:100]
        ],
        "session": {
            key: row[key] for key in ("public_id", "expires_at", "idle_expires_at", "created_at")
        },
    }


async def bootstrap(local: LocalIdentity, row: Row) -> Row:
    me = await local.me(row)
    memberships = await local.store.list(
        "organization_memberships",
        {"user_id": row["user_id"], "status": "active", "verified_at": {"$ne": None}},
    )
    organizations = []
    for member in memberships[:100]:
        org = await local.store.find(
            "organizations", {"org_id": member["org_id"], "status": "active"}
        )
        if org:
            organizations.append(
                {
                    "org_id": org["org_id"],
                    "name": org["name"],
                    "role": member["role"],
                    "auth_policy": org["auth_policy"],
                }
            )
    engine = local.policy_engine
    snapshot = await engine.snapshot(row["org_id"], row["user_id"]) if engine else None
    return {
        "me": me,
        "organizations": organizations,
        "grants": [g.model_dump() for g in snapshot["tuples"]] if snapshot else [],
        "security": await security(local, row),
    }
