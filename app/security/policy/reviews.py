"""Operation-specific independent review, including explicit service custody.

Service reviewers need a current separately approved exception and verified
event-time sponsors. Descriptive ownership and content:write never suffice.
"""

from collections.abc import Mapping
from dataclasses import replace
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
    if policy.subject_kind == "machine":
        if not policy.service_account_id or not policy.reviewer or not policy.sponsor_ids:
            raise PolicyDenied("managed machine reviewer sponsorship is not verified")
        approval = replace(policy, grants=policy.reviewer_grants)
        read_action = "content:read" if collection.startswith("content_") else "research:read"
        if not resource_allows(approval, operation, row, collection) or not resource_allows(
            approval, read_action, row, collection
        ):
            raise PolicyDenied("independent approval does not cover the review operation/evidence")
    elif policy.subject_kind != "human" or policy.client_id is not None:
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
        # Immutable event-time sponsor plus the fenced author history are both required.
        if (
            not actor.get("sponsor_id")
            or not actor.get("sponsorship_revision")
            or actor.get("org_id") != policy.org_id
        ):
            raise PolicyDenied("authored machine sponsorship is not verified")
        if actor["sponsor_id"] in (policy.sponsor_ids or (policy.subject_id,)):
            raise PolicyDenied("same-sponsor self-review is refused")
        return
    if not actor.get("user_id") or actor.get("org_id") != policy.org_id:
        raise PolicyDenied("authored subject sponsorship is not verified")
    if actor["user_id"] in (policy.sponsor_ids or (policy.subject_id,)):
        raise PolicyDenied("self-review is refused")
