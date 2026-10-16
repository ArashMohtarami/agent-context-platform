"""Verified sponsor provenance and explicit service-only review exception."""

import pytest

from app.managed_agents.schema import VALIDATORS
from app.models.actor import ActorProvenance
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.reviews import require_review
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_policy import ResourceGrant, TenantPolicy, policy_scope


def policy(**changes):
    tuples = tuple(
        ResourceGrant(a, "creator_profile", "include", ("profile",))
        for a in ("content:read", "content:write", "reviews:decide")
    )
    changes.setdefault("reviewer_grants", tuples)
    return TenantPolicy(
        "org-a",
        "custodian",
        tuple(
            ResourceGrant(a, "creator_profile", "include", ("profile",))
            for a in ("content:read", "content:write", "reviews:decide")
        ),
        client_id="review-bot",
        managed=True,
        subject_kind="machine",
        sponsor_ids=("custodian",),
        machine_key_id="key",
        **changes,
    )


def test_ordinary_machine_cannot_review_with_write_or_review_scope():
    row = {
        "org_id": "org-a",
        "profile_id": "profile",
        "actor": {"actor_type": "human", "user_id": "independent", "org_id": "org-a"},
    }
    with tenant_scope(TenantContext("org-a")), policy_scope(policy()):
        with pytest.raises(PolicyDenied):
            require_review(row, "content_items", "content:write")


@pytest.mark.parametrize("author", ["custodian", "different"])
def test_service_exception_checks_event_time_sponsor_not_machine_name(author):
    row = {
        "org_id": "org-a",
        "profile_id": "profile",
        "actor": {
            "actor_type": "agent",
            "client_id": "another-bot",
            "sponsor_id": author,
            "org_id": "org-a",
            "sponsorship_revision": 1,
        },
    }
    with (
        tenant_scope(TenantContext("org-a")),
        policy_scope(policy(service_account_id="service", reviewer=True)),
    ):
        if author == "custodian":
            with pytest.raises(PolicyDenied):
                require_review(row, "content_items", "content:write")
        else:
            require_review(row, "content_items", "content:write")


def test_actor_preserves_verified_machine_sponsor_without_fake_human_identity():
    actor = ActorProvenance(
        actor_type="agent",
        client_id="worker",
        client_type="codex",
        sponsor_id="person",
        sponsor_team_id="team",
        org_id="org-a",
        sponsorship_revision=2,
    )
    data = actor.model_dump()
    assert data["sponsor_id"] == "person" and "user_id" not in data
    assert ActorProvenance(actor_type="system").model_dump() == {
        "actor_type": "system",
        "client_id": None,
        "client_type": None,
    }


def test_service_approval_schema_is_bounded_and_has_no_secret():
    schema = VALIDATORS["service_accounts"]["$jsonSchema"]
    approval = schema["properties"]["reviewer"]["oneOf"][1]
    assert approval["additionalProperties"] is False
    assert {
        "user_id",
        "membership_revision",
        "parent_id",
        "parent_generation",
        "expires_at",
        "tuples",
    } == set(approval["required"])


def test_review_exception_cannot_borrow_operation_from_custodian_policy():
    row = {
        "org_id": "org-a",
        "profile_id": "profile",
        "actor": {"actor_type": "human", "user_id": "independent", "org_id": "org-a"},
    }
    approval = (ResourceGrant("reviews:decide", "creator_profile", "include", ("profile",)),)
    with (
        tenant_scope(TenantContext("org-a")),
        policy_scope(policy(service_account_id="service", reviewer=True, reviewer_grants=approval)),
    ):
        with pytest.raises(PolicyDenied):
            require_review(row, "content_items", "content:write")
