"""Reviewed invitation contracts and bounded, non-widening team issuance."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.identity.crypto import digest, random_token
from app.management.boundaries import team_scope
from app.management.models import InviteCreate, ReviewedPolicy
from app.models.common import utc_now
from app.security.policy.evaluation import PolicyDenied, validate_issue
from app.security.policy.models import Grant
from tests.unit.test_v6_memberships import team_snapshot


def policy(**changes):
    return ReviewedPolicy.model_validate(
        {
            "parent_id": "lead-grant",
            "expected_parent_generation": 1,
            "expires_at": utc_now() + timedelta(days=1),
            **changes,
        }
    )


def test_invitation_default_is_no_data_and_secrets_are_purpose_hashed():
    value = policy()
    assert value.tuples == value.ceiling == ()
    assert value.targets.mode == "none"
    token = random_token()
    assert digest("challenge", token) != token
    assert digest("challenge", token) != digest("session", token)
    assert "token" not in InviteCreate.model_fields


@pytest.mark.parametrize(
    "change",
    [
        {"role": "org_owner", "allowed_roles": ["org_owner"]},
        {"role": "made_up"},
        {"remaining_depth": 3},
        {"team_id": "alpha"},
        {"expected_team_generation": 1},
        {"expires_at": "2030-01-01T00:00:00"},
        {
            "tuples": [
                {
                    "action": "context:read",
                    "resource_kind": "project",
                    "selector": "include",
                    "ids": [],
                }
            ]
        },
        {"targets": {"mode": "none", "ids": ["member"]}},
    ],
)
def test_invitation_rejects_ambiguous_or_unbounded_policy_shapes(change):
    with pytest.raises(ValidationError):
        policy(**change)


def test_invite_expiry_cannot_outlive_its_exact_reviewed_assignment():
    with pytest.raises(ValidationError):
        InviteCreate(
            email="fiction@example.test",
            display_name="Fiction",
            policy=policy(),
            expires_at=utc_now() + timedelta(days=2),
        )


def team_parent():
    snapshot = team_snapshot()
    parent = snapshot["assignments"]["lead-grant"]
    parent.update(team_id="alpha", team_generation=1, team_link_revision=1, team_delegation=False)
    parent["tuples"] = [
        Grant(
            action="grants:delegate", resource_kind="team", selector="include", ids=["alpha"]
        ).model_dump()
    ]
    return snapshot, parent


def test_team_invitation_uses_that_parent_action_and_ceiling_without_new_delegation():
    snapshot, parent = team_parent()
    request = policy(
        team_id="alpha",
        expected_team_generation=1,
        tuples=[
            Grant(action="context:read", resource_kind="project", selector="include", ids=["a"])
        ],
    ).issue("new-person")
    _, _, alternate = team_scope(snapshot, request, pending_link=True)
    assert alternate
    validate_issue(parent, request, "lead", utc_now(), team_authorized=True)


@pytest.mark.parametrize(
    "change",
    [
        {"team_id": "other"},
        {"expected_team_generation": 2},
        {"remaining_depth": 1},
        {"targets": {"mode": "include", "ids": ["member"]}},
        {"allowed_roles": ["contributor"]},
        {"role": "org_admin"},
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
    ],
)
def test_team_invite_cannot_move_to_another_team_elevate_or_widen(change):
    snapshot, parent = team_parent()
    request = policy(
        team_id="alpha",
        expected_team_generation=1,
        **{k: v for k, v in change.items() if k not in {"team_id", "expected_team_generation"}},
    ).issue("new-person")
    request = request.model_copy(
        update={k: v for k, v in change.items() if k in {"team_id", "expected_team_generation"}}
    )
    with pytest.raises(PolicyDenied):
        _, _, alternate = team_scope(snapshot, request, pending_link=True)
        validate_issue(parent, request, "lead", utc_now(), team_authorized=alternate)


def test_unrelated_invite_capability_cannot_replace_the_reviewed_parent_generation():
    from app.management.invitations import require_parent_invite

    _, parent = team_parent()
    request = policy(team_id="alpha", expected_team_generation=1).issue("new-person")
    with pytest.raises(PolicyDenied):
        require_parent_invite(parent, request)
    parent["tuples"].append(
        Grant(
            action="members:invite", resource_kind="team", selector="include", ids=["alpha"]
        ).model_dump()
    )
    require_parent_invite(parent, request)
    with pytest.raises(PolicyDenied):
        require_parent_invite(parent, request.model_copy(update={"team_id": "other"}))
