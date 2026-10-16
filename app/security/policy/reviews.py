"""Operation-specific review and independent-human policy.

Phase 7 may introduce separately approved service-account reviewers. Such an
exception must bind a verified current sponsor and generation, explicit reviewer
approval/operation/resources/expiry and independent sponsor checks. owner_id,
content:write, arbitrary IdP roles and a machine actor are never that proof.
Until then every managed machine review is denied.
"""

from collections.abc import Mapping
from typing import Any

from app.security.policy.evaluation import PolicyDenied
from app.security.policy.resources import permitted, resource_allows
from app.security.tenant_policy import current_policy


def require_review(row: Mapping[str, Any], collection: str, operation: str) -> None:
    policy = current_policy()
    if policy is None:
        return  # explicit trusted host maintenance; never an authenticated HTTP subject
    if not policy.managed:
        if policy.engine is not None:
            # Even a reviewed adopted admin key is not a verified human or a
            # sponsored service account. The adapter does not grant review.
            raise PolicyDenied("bearer reviewer sponsorship is not verified")
        return  # pre-Phase-5 compatibility when managed policy is disabled
    if policy.subject_kind != "human" or policy.client_id is not None:
        raise PolicyDenied("managed machine reviewer sponsorship is not verified")
    if not resource_allows(policy, operation, row, collection):
        raise PolicyDenied("operation-specific review authority is required")
    read_action = "content:read" if collection.startswith("content_") else "research:read"
    if not resource_allows(policy, read_action, row, collection):
        raise PolicyDenied("review requires readable evidence")
    kind, identifier = (
        ("creator_profile", row.get("profile_id"))
        if collection.startswith("content_")
        else ("research_project", row.get("research_project_id"))
    )
    if not permitted(policy, "reviews:decide", kind, identifier):
        raise PolicyDenied("review-operation authority is required")
    actor = row.get("actor")
    if actor is not None and hasattr(actor, "model_dump"):
        actor = actor.model_dump()
    if not isinstance(actor, Mapping):
        raise PolicyDenied("authored subject sponsorship is not verified")
    if actor.get("actor_type") in {"agent", "automation", "system"}:
        # Never infer a human sponsor from the machine ID or creator label.
        raise PolicyDenied("authored machine sponsorship is not verified")
    if not actor.get("user_id") or actor.get("org_id") != policy.org_id:
        raise PolicyDenied("authored subject sponsorship is not verified")
    if actor["user_id"] == policy.subject_id:
        raise PolicyDenied("self-review is refused")
