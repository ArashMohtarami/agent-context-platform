"""Tenant DTOs, named-team boundaries and generation invalidation."""

from datetime import timedelta

import pytest

from app.api.routes.human_management import MemberDTO, TeamDTO
from app.management.boundaries import permits_member, team_scope
from app.management.directory import member_query
from app.models.common import utc_now
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyDenied, active_chain
from app.security.policy.models import Grant, Issue
from tests.unit.test_v6_delegation import lineage


def team_snapshot():
    now, members, users, assignments = lineage()
    team = {"org_id": "org-a", "team_id": "alpha", "generation": 1, "status": "active"}
    links = {
        ("alpha", user): {"org_id": "org-a", "status": "active", "revision": 1}
        for user in ("lead", "member")
    }
    snapshot = {
        "anchor": {"org_id": "org-a", "revision": 1},
        "member": members["lead"],
        "members": members,
        "users": users,
        "assignments": assignments,
        "teams": {"alpha": team, "other": {**team, "team_id": "other"}},
        "team_links": links,
        "tuples": (
            Grant(action="members:read", resource_kind="team", selector="include", ids=["alpha"]),
        ),
    }
    return snapshot


def test_directory_is_org_qualified_and_does_not_expose_private_identity_fields():
    snapshot = team_snapshot()
    assert member_query(snapshot) == {"org_id": "org-a", "user_id": {"$in": ["lead", "member"]}}
    dto = MemberDTO.model_validate(
        {
            **snapshot["member"],
            "display_name": "Fictional Person",
            "email": "private@example.test",
            "password_hash": "private",
            "other_memberships": ["org-b"],
        }
    )
    assert set(dto.model_dump()) == {
        "org_id",
        "user_id",
        "display_name",
        "role",
        "status",
        "revision",
    }
    assert "email" not in MemberDTO.model_fields
    assert "user_id" not in TeamDTO.model_fields


@pytest.mark.parametrize(
    "change",
    [
        "team_suspended",
        "foreign_team",
        "link_suspended",
        "missing_link",
        "no_action",
        "wrong_action",
    ],
)
def test_current_team_and_action_are_required_before_directory_selection(change):
    snapshot = team_snapshot()
    if change == "team_suspended":
        snapshot["teams"]["alpha"]["status"] = "suspended"
    elif change == "foreign_team":
        snapshot["teams"]["alpha"]["org_id"] = "org-b"
    elif change == "link_suspended":
        snapshot["team_links"][("alpha", "member")]["status"] = "suspended"
    elif change == "missing_link":
        del snapshot["team_links"][("alpha", "member")]
    else:
        snapshot["tuples"] = (
            ()
            if change == "no_action"
            else (
                Grant(
                    action="members:suspend",
                    resource_kind="team",
                    selector="include",
                    ids=["alpha"],
                ),
            )
        )
    assert not permits_member(snapshot, "members:read", "member")
    with pytest.raises(PolicyDenied):
        PolicyEngine.require(snapshot, "members:read", "member", "member")


def test_direct_member_grants_do_not_escape_a_leads_named_team_boundary():
    snapshot = team_snapshot()
    snapshot["tuples"] = (
        Grant(action="members:read", resource_kind="member", selector="all_in_org"),
    )
    assert member_query(snapshot)["user_id"] == {"$in": []}
    with pytest.raises(PolicyDenied):
        PolicyEngine.require(snapshot, "members:read", "member", "member")


@pytest.mark.parametrize(
    "change",
    [
        "team_suspend",
        "team_generation",
        "link_suspend",
        "link_revision",
        "missing_team",
        "missing_link",
    ],
)
def test_old_team_assignments_never_survive_suspend_or_generation_change(change):
    snapshot = team_snapshot()
    row = snapshot["assignments"]["lead-grant"]
    row.update(team_id="alpha", team_generation=1, team_link_revision=1, team_delegation=False)
    assert active_chain(
        row,
        snapshot["assignments"],
        snapshot["members"],
        snapshot["users"],
        utc_now(),
        snapshot["teams"],
        snapshot["team_links"],
    )
    if change == "team_suspend":
        snapshot["teams"]["alpha"]["status"] = "suspended"
    elif change == "team_generation":
        snapshot["teams"]["alpha"]["generation"] += 1
    elif change == "link_suspend":
        snapshot["team_links"][("alpha", "lead")]["status"] = "suspended"
    elif change == "link_revision":
        snapshot["team_links"][("alpha", "lead")]["revision"] += 1
    elif change == "missing_team":
        snapshot["teams"] = {}
    else:
        snapshot["team_links"] = {}
    with pytest.raises(PolicyDenied):
        active_chain(
            row,
            snapshot["assignments"],
            snapshot["members"],
            snapshot["users"],
            utc_now(),
            snapshot["teams"],
            snapshot["team_links"],
        )
    snapshot["teams"].setdefault("alpha", {"org_id": "org-a", "generation": 3, "status": "active"})
    snapshot["teams"]["alpha"].update(status="active", generation=3)
    with pytest.raises(PolicyDenied):
        active_chain(
            row,
            snapshot["assignments"],
            snapshot["members"],
            snapshot["users"],
            utc_now(),
            snapshot["teams"],
            snapshot["team_links"],
        )


def test_lead_cannot_issue_a_policy_without_a_named_team():
    snapshot = team_snapshot()
    request = Issue(
        subject_id="member",
        parent_id="lead-grant",
        expected_parent_generation=1,
        expires_at=utc_now() + timedelta(days=1),
    )
    with pytest.raises(PolicyDenied):
        team_scope(snapshot, request)


def test_human_management_configuration_cannot_start_without_policy(settings):
    from app.core.config import Settings

    values = settings.model_dump()
    values.update(v6_human_management=True, v6_management_policy=False)
    with pytest.raises(ValueError, match="requires Phase 5"):
        Settings(_env_file=None, **values)


def test_old_unbound_lead_grants_remain_legacy_only_until_explicit_team_review():
    now, members, users, assignments = lineage()
    old = assignments["lead-grant"]
    assert active_chain(old, assignments, members, users, now)
    with pytest.raises(PolicyDenied, match="reviewed named team"):
        active_chain(old, assignments, members, users, now, {}, {})
