"""Fixed management templates. Every capability still needs a target envelope."""

from app.identity.models import MANAGEMENT_ACTIONS

ROLES = (
    "installation_operator",
    "org_owner",
    "org_admin",
    "executive",
    "team_lead",
    "reviewer",
    "contributor",
    "auditor",
    "viewer",
)
HUMAN_ROLES = ROLES[1:]
_EVERYONE = set(HUMAN_ROLES)
_ADMIN = {"org_owner", "org_admin"}
_LEAD = _ADMIN | {"team_lead"}
_ALLOWED = {
    "org:read": _EVERYONE,
    "org:manage": {"org_owner"},
    "org:transfer-owner": {"org_owner"},
    **dict.fromkeys(
        ("members:read", "members:invite", "members:suspend", "members:reinstate"), _LEAD
    ),
    "teams:read": _LEAD | {"executive"},
    "teams:manage": _ADMIN,
    "grants:read": _EVERYONE,
    "grants:delegate": _LEAD,
    "grants:all-in-org": {"org_owner"},
    "clients:read:own": _EVERYONE,
    "clients:read:team": {"team_lead"},
    "clients:read:org": _ADMIN,
    "clients:manage:own": set(),
    "clients:manage:team": {"team_lead"},
    "clients:manage:org": _ADMIN,
    "credentials:issue:own": set(),
    "credentials:revoke:own": set(),
    "credentials:revoke:team": {"team_lead"},
    "credentials:revoke:org": _ADMIN,
    "activity:read:self": _EVERYONE,
    "activity:read:team": {"team_lead"},
    "activity:read:org": _ADMIN | {"executive", "auditor"},
    "audit:read:org": _ADMIN | {"auditor"},
    "reports:export": _ADMIN | {"auditor"},
    "reviews:decide": {"reviewer"},
}
ROLE_PERMISSIONS = {
    role: frozenset(action for action, roles in _ALLOWED.items() if role in roles) for role in ROLES
}
if set(_ALLOWED) != MANAGEMENT_ACTIONS:
    raise RuntimeError("management catalog is incomplete")

# These are separately delegated capabilities, never automatic template powers.
ADDITIONAL_CAPABILITIES = frozenset(
    {
        "clients:manage:own",
        "credentials:issue:own",
        "credentials:revoke:own",
    }
)


def role_allows(role: str, action: str, *, explicit: bool = False) -> bool:
    return action in ROLE_PERMISSIONS.get(role, ()) or (
        explicit
        and role in HUMAN_ROLES
        and (
            action in ADDITIONAL_CAPABILITIES
            or (role == "executive" and action == "grants:delegate")
        )
    )


def can_appoint(issuer_role: str, target_role: str) -> bool:
    if target_role not in HUMAN_ROLES or target_role == "org_owner":
        return False  # ownership uses the distinct accepted-transfer transaction
    if issuer_role == "org_owner":
        return True
    if issuer_role == "org_admin":
        return target_role not in {"org_owner", "org_admin"}
    if issuer_role in {"executive", "team_lead"}:
        return target_role in {"reviewer", "contributor", "auditor", "viewer"}
    return False
