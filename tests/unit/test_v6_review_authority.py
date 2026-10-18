"""Operation/read/project coupling and immutable sponsor self-review rules."""

from dataclasses import replace

import pytest

from app.security.policy.evaluation import PolicyDenied
from app.security.policy.reviews import require_review
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_policy import ResourceGrant, TenantPolicy, policy_scope


def grants():
    return tuple(
        ResourceGrant(a, k, "include", ids)
        for a, k, ids in (
            ("content:read", "creator_profile", ("brand",)),
            ("content:write", "creator_profile", ("brand",)),
            ("content:read", "project", ("a",)),
            ("content:write", "project", ("a",)),
            ("reviews:decide", "creator_profile", ("brand",)),
        )
    )


def row(actor=None):
    return {
        "org_id": "org-a",
        "profile_id": "brand",
        "resource_project_ids": ["a"],
        "actor": actor or {"actor_type": "human", "user_id": "author", "org_id": "org-a"},
    }


@pytest.mark.parametrize(
    "subject,remove",
    [
        ("author", None),
        ("reviewer", "reviews:decide"),
        ("reviewer", "content:read"),
        ("reviewer", "content:write"),
    ],
)
def test_human_approval_requires_independence_and_each_current_operation(subject, remove):
    p = TenantPolicy(
        "org-a",
        subject,
        tuple(g for g in grants() if g.action != remove),
        managed=True,
        subject_kind="human",
    )
    with tenant_scope(TenantContext("org-a")), policy_scope(p), pytest.raises(PolicyDenied):
        require_review(row(), "content_items", "content:write")


def test_read_a_write_b_cannot_approve_a_or_foreign_profile_and_org():
    p = TenantPolicy("org-a", "reviewer", grants(), managed=True, subject_kind="human")
    with tenant_scope(TenantContext("org-a")), policy_scope(p):
        require_review(row(), "content_items", "content:write")
        for other in ({**row(), "org_id": "org-b"}, {**row(), "profile_id": "foreign"}):
            with pytest.raises(PolicyDenied):
                require_review(other, "content_items", "content:write")
    p = replace(
        p,
        grants=tuple(
            replace(g, ids=("b",))
            if g.action == "content:write" and g.resource_kind == "project"
            else g
            for g in grants()
        ),
    )
    with tenant_scope(TenantContext("org-a")), policy_scope(p), pytest.raises(PolicyDenied):
        require_review(row(), "content_items", "content:write")


@pytest.mark.parametrize("author_kind", ["human", "agent"])
@pytest.mark.parametrize(
    "approved,sponsor,allowed",
    [(False, "different", False), (True, "author", False), (True, "different", True)],
)
def test_normal_machine_and_second_client_same_sponsor_do_not_become_reviewers(
    approved, sponsor, allowed, author_kind
):
    p = TenantPolicy(
        "org-a",
        "second-client-key",
        grants(),
        client_id="second-client",
        managed=True,
        subject_kind="machine",
        sponsor_ids=(sponsor,),
        service_account_id="approved-service" if approved else None,
        reviewer=approved,
        reviewer_grants=grants(),
    )
    authored = row(
        {
            "actor_type": author_kind,
            "user_id": "author" if author_kind == "human" else None,
            "sponsor_id": "author" if author_kind == "agent" else None,
            "sponsorship_revision": 2,
            "org_id": "org-a",
            "client_id": "first-client",
        }
    )
    with tenant_scope(TenantContext("org-a")), policy_scope(p):
        if allowed:
            require_review(authored, "content_items", "content:write")
        else:
            with pytest.raises(PolicyDenied):
                require_review(authored, "content_items", "content:write")
