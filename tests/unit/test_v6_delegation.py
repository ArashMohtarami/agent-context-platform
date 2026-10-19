"""Ancestry, expiry, safe narrowing and role/target envelopes."""

from copy import deepcopy
from datetime import timedelta

import pytest

from app.models.common import utc_now
from app.security.policy.evaluation import PolicyDenied, active_chain, effective, validate_issue
from app.security.policy.governance import Governance
from app.security.policy.models import Grant, Issue, Selector


def lineage():
    now = utc_now()
    members = {
        name: {
            "_id": "member-" + name,
            "org_id": "org-a",
            "user_id": name,
            "role": role,
            "status": "active",
            "verified_at": now,
            "revision": 1,
        }
        for name, role in (("owner", "org_owner"), ("lead", "team_lead"), ("member", "contributor"))
    }
    users = {name: {"status": "active"} for name in members}
    ceiling = (
        Grant(action="context:read", resource_kind="project", selector="include", ids=["a"]),
        Grant(
            action="grants:delegate",
            resource_kind="member",
            selector="include",
            ids=["lead", "member"],
        ),
    )
    root = Governance.assignment(
        "root",
        "org-a",
        members["owner"],
        "installation_operator",
        ceiling,
        ceiling,
        Selector(mode="include", ids=("lead", "member")),
        ("team_lead", "contributor"),
        3,
        now + timedelta(days=10),
    )
    lead = Governance.assignment(
        "lead-grant",
        "org-a",
        members["lead"],
        "owner",
        ceiling,
        ceiling,
        Selector(mode="include", ids=("member",)),
        ("contributor",),
        1,
        now + timedelta(days=5),
        root,
    )
    return utc_now(), members, users, {"root": root, "lead-grant": lead}


def request(**changes):
    return Issue.model_validate(
        {
            "subject_id": "member",
            "parent_id": "lead-grant",
            "expected_parent_generation": 1,
            "tuples": [
                {
                    "action": "context:read",
                    "resource_kind": "project",
                    "selector": "include",
                    "ids": ["a"],
                }
            ],
            "expires_at": utc_now() + timedelta(days=1),
            **changes,
        }
    )


def test_bounded_lead_member_delegation_and_valid_ancestry():
    now, members, users, assignments = lineage()
    lead = assignments["lead-grant"]
    validate_issue(lead, request(), "lead", now)
    child = Governance.assignment(
        "child",
        "org-a",
        members["member"],
        "lead",
        request().tuples,
        (),
        Selector(),
        (),
        0,
        now + timedelta(days=1),
        lead,
    )
    assignments["child"] = child
    now = utc_now()
    assert [r["_id"] for r in active_chain(child, assignments, members, users, now)] == [
        "child",
        "lead-grant",
        "root",
    ]
    assert effective("org-a", "member", assignments, members, users, now)[0][0].ids == ["a"]
    assert effective("org-b", "member", assignments, members, users, now)[0] == ()


@pytest.mark.parametrize(
    "change",
    [
        {"subject_id": "lead"},
        {"subject_id": "foreign"},
        {"expected_parent_generation": 2},
        {"role": "org_owner"},
        {"role": "org_admin"},
        {"role": "team_lead"},
        {"remaining_depth": 1},
        {"expires_at": utc_now() + timedelta(days=20)},
        {"allowed_roles": ["team_lead"]},
        {"targets": {"mode": "all_in_org"}},
        {
            "tuples": [
                {
                    "action": "context:write",
                    "resource_kind": "project",
                    "selector": "include",
                    "ids": ["a"],
                }
            ]
        },
        {
            "tuples": [
                {"action": "context:read", "resource_kind": "project", "selector": "all_in_org"}
            ]
        },
    ],
)
def test_self_stale_expired_or_outside_envelope_issue_is_denied(change):
    now, _, _, assignments = lineage()
    with pytest.raises(PolicyDenied):
        validate_issue(assignments["lead-grant"], request(**change), "lead", now)


@pytest.mark.parametrize(
    "mutation",
    ["revoke", "narrow", "suspend", "expire", "member_generation", "user_disabled", "cross_org"],
)
def test_stale_suspended_expired_ancestry_and_reinstatement_never_resurrect(mutation):
    now, members, users, assignments = lineage()
    lead = assignments["lead-grant"]
    if mutation == "revoke":
        assignments["root"]["status"] = "revoked"
    elif mutation == "narrow":
        assignments["root"]["generation"] += 1
    elif mutation == "suspend":
        members["owner"]["status"] = "suspended"
    elif mutation == "expire":
        assignments["root"]["expires_at"] = now - timedelta(seconds=1)
    elif mutation == "member_generation":
        members["owner"]["revision"] += 1
    elif mutation == "user_disabled":
        users["owner"]["status"] = "disabled"
    else:
        assignments["root"]["org_id"] = "org-b"
    assert effective("org-a", "lead", assignments, members, users, now)[0] == ()
    with pytest.raises(PolicyDenied):
        active_chain(lead, assignments, members, users, now)
    # Reinstatement advances generations; no code may reset old lineage.
    members["owner"]["status"] = "active"
    members["owner"]["revision"] += 1
    assignments["root"]["status"] = "active"
    assignments["root"]["generation"] += 1
    assert effective("org-a", "lead", assignments, members, users, now)[0] == ()


def test_tampered_ceiling_or_cycle_does_not_become_authority():
    now, members, users, assignments = lineage()
    for field, value in (("ceiling_hash", "tampered"), ("parent_id", "lead-grant")):
        rows = deepcopy(assignments)
        rows["lead-grant"][field] = value
        assert effective("org-a", "lead", rows, members, users, now)[0] == ()


def test_a_different_assignment_cannot_supply_the_parent_issuance_authority():
    now, _, _, assignments = lineage()
    parent = assignments["lead-grant"]
    parent["tuples"] = [grant for grant in parent["tuples"] if grant["action"] != "grants:delegate"]
    with pytest.raises(PolicyDenied):
        validate_issue(parent, request(), "lead", now)


def test_issuance_ceiling_does_not_confer_personal_review_authority():
    now, _, _, assignments = lineage()
    root = assignments["root"]
    review = Grant(
        action="reviews:decide",
        resource_kind="creator_profile",
        selector="include",
        ids=["profile"],
    )
    root["ceiling"].append(review.model_dump())
    # This is an issuance-only ceiling for a future reviewer, not a team lead's
    # effective reviews:decide tuple.
    command = Issue(
        subject_id="lead",
        role="team_lead",
        parent_id="root",
        expected_parent_generation=1,
        ceiling=(review,),
        targets=Selector(mode="include", ids=("member",)),
        allowed_roles=("contributor",),
        remaining_depth=1,
        expires_at=now + timedelta(days=1),
    )
    validate_issue(root, command, "owner", now)
    assert not command.tuples


def test_shared_parent_decoding_never_caches_authority_across_evaluations():
    now, members, users, assignments = lineage()
    lead = assignments["lead-grant"]
    for number in range(100):
        child = Governance.assignment(
            str(number),
            "org-a",
            members["member"],
            "lead",
            request().tuples,
            (),
            Selector(),
            (),
            0,
            now + timedelta(hours=1),
            lead,
        )
        assignments[child["_id"]] = child
    now = utc_now()
    assert len(effective("org-a", "member", assignments, members, users, now)[0]) == 100
    # Every call must recheck both the decoded ceiling commitment and the live
    # lineage generation, even when the same Python dictionaries are reused.
    root = assignments["root"]
    original = deepcopy(root["ceiling"])
    root["ceiling"] = []
    assert effective("org-a", "member", assignments, members, users, now)[0] == ()
    root["ceiling"] = original
    assert len(effective("org-a", "member", assignments, members, users, now)[0]) == 100
    root["generation"] += 1
    assert effective("org-a", "member", assignments, members, users, now)[0] == ()
