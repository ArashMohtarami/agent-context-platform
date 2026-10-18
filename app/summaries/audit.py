"""Auditable authority changes without arbitrary before/after record serialization."""

from datetime import datetime
from typing import Any

from app.identity.repository import Row
from app.security.policy.catalog import HUMAN_ROLES
from app.security.policy.models import Grant

REDACTION = (
    "Only declared authority fields. Resource IDs, target identities, names, free text, "
    "approval references and payloads are withheld. Missing snapshots are not inferred."
)
FIELDS = (
    "status",
    "role",
    "revision",
    "generation",
    "expires_at",
    "remaining_depth",
    "tuples",
    "ceiling",
    "allowed_roles",
    "targets",
)
STATES = {"active", "revoked", "suspended", "disabled", "pending", "expired", "approved", "denied"}


def safe(value: Any, field: str) -> str | None:
    if field == "status":
        return value if isinstance(value, str) and value in STATES else None
    if field == "role":
        return value if isinstance(value, str) and value in HUMAN_ROLES else None
    if field == "expires_at":
        return value.isoformat() if isinstance(value, datetime) else None
    if field in {"revision", "generation", "remaining_depth"}:
        return str(value) if type(value) is int and 0 <= value <= 1_000_000_000 else None
    if field == "allowed_roles":
        return (
            ", ".join(sorted(set(value)))
            if isinstance(value, list)
            and len(value) <= 20
            and all(isinstance(x, str) and x in HUMAN_ROLES for x in value)
            else None
        )
    if field == "targets":
        if (
            isinstance(value, dict)
            and value.get("mode") in {"none", "include", "all_in_org"}
            and isinstance(value.get("ids"), list)
        ):
            return str(value["mode"]) + " (" + str(len(value["ids"])) + " identifiers withheld)"
        return None
    if field in {"tuples", "ceiling"} and isinstance(value, list) and len(value) <= 100:
        try:
            grants = [Grant.model_validate(x) for x in value]
        except ValueError:
            return None
        return (
            "\n".join(
                f"{g.action} / {g.resource_kind}: {g.selector} ({len(g.ids)} identifiers withheld)"
                for g in grants
            )
            or "NONE"
        )
    return None


def diff(change: Row) -> list[Row]:
    before = change.get("before", change.get("previous_root"))
    after = change.get("after", change.get("new_root"))
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    rows = []
    for field in FIELDS:
        a, b = safe(before.get(field), field), safe(after.get(field), field)
        if (field not in before or field not in after or a != b) and (
            a is not None or b is not None
        ):
            rows.append({"field": field, "before": a, "after": b})
    return rows
