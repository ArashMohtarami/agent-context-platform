"""Whole-installation archives are a host operator capability, never an org grant."""

from app.provenance import current as current_actor
from app.security.tenant_policy import current_policy


def require_operator() -> None:
    policy = current_policy()
    if current_actor().user_id is not None or (policy is not None and not policy.operator):
        raise ValueError("whole-database backup is installation-operator-only")
