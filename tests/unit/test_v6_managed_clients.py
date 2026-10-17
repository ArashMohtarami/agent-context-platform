"""Action/resource ceilings and current named sponsorship boundaries."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.managed_agents.models import ClientApproval
from app.managed_agents.policy import intersect
from app.managed_agents.service import authority, team
from app.models.common import utc_now
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.models import Grant
from tests.unit.test_v6_memberships import team_snapshot


def grant(action="context:read", ids=("a",), mode="include", kind="project"):
    return Grant(action=action, resource_kind=kind, selector=mode, ids=list(ids))


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("none", "none", "none"),
        ("none", "include", "none"),
        ("include", "none", "none"),
        ("include", "include", "include"),
        ("include", "all_in_org", "include"),
        ("all_in_org", "include", "include"),
        ("all_in_org", "all_in_org", "all_in_org"),
    ],
)
def test_none_include_and_all_intersections(left, right, expected):
    result = intersect(
        (grant(mode=left, ids=("a",) if left == "include" else ()),),
        (grant(mode=right, ids=("a",) if right == "include" else ()),),
    )
    assert result == () if expected == "none" else result[0].selector == expected


def test_read_a_write_b_never_forms_cartesian_product_in_four_ceilings():
    approved = (grant(), grant("context:write", ("b",)))
    result = intersect(
        approved,
        approved,
        approved,
        (grant(mode="all_in_org", ids=()), grant("context:write", mode="all_in_org", ids=())),
    )
    assert [(g.action, g.ids) for g in result] == [
        ("context:read", ["a"]),
        ("context:write", ["b"]),
    ]
    assert intersect(approved, (grant("context:write", ("a",)),)) == ()
    assert intersect(approved, (grant(kind="creator_profile"),)) == ()


@pytest.mark.parametrize("change", ["missing", "suspended", "generation", "link", "link_revision"])
def test_team_sponsorship_requires_current_generation_and_edge(change):
    snapshot = team_snapshot()
    if change == "missing":
        snapshot["teams"].pop("alpha")
    elif change == "suspended":
        snapshot["teams"]["alpha"]["status"] = "suspended"
    elif change == "generation":
        snapshot["teams"]["alpha"]["generation"] = 2
    elif change == "link":
        snapshot["team_links"][("alpha", "member")]["status"] = "suspended"
    else:
        snapshot["team_links"][("alpha", "member")]["revision"] = 2
    with pytest.raises(PolicyDenied):
        team(snapshot, "alpha", "member", 1, 1)


def test_lead_metadata_cannot_manage_another_team_or_self_by_descriptive_owner():
    snapshot = team_snapshot()
    snapshot["tuples"] = (grant("clients:manage:team", ("alpha",), kind="team"),)
    row = {
        "client_id": "worker",
        "sponsor_id": "member",
        "owner_id": "lead",
        "team_id": "alpha",
        "team_generation": 1,
        "team_link_revision": 1,
    }
    authority(snapshot, "clients:manage", row)
    with pytest.raises(PolicyDenied):
        authority(snapshot, "clients:manage", {**row, "team_id": "other"})
    with pytest.raises(PolicyDenied):
        authority(snapshot, "credentials:issue", row)


@pytest.mark.parametrize(
    "action", ["agent:manage", "system:read", "members:invite", "credentials:issue:own"]
)
def test_machine_approval_cannot_administer_humans_or_authority(action):
    with pytest.raises(ValidationError):
        ClientApproval(
            client_id="worker",
            name="Worker",
            subject_id="person",
            parent_id="parent",
            expected_parent_generation=1,
            tuples=(grant(action),),
            expires_at=utc_now() + timedelta(days=1),
        )


def test_mcp_preflight_uses_action_resources_and_summary_does_not_flatten_them():
    from app.mcp.auth import Authorization
    from app.mcp.gateway_errors import GatewayError
    from app.mcp.middleware.authorization import GatewayAuthorization
    from app.mcp.middleware.client_context import ClientRequestContext

    context = ClientRequestContext(
        org_id="org-a",
        key_id="1234567890abcdef",
        allowed_profile_ids=None,
        request_id="req",
        client_id="worker",
        client_type="codex",
        scopes=("context:read", "context:write"),
        allowed_project_ids=None,
        action_resources=(grant(), grant("context:write", ("b",))),
        subject_id="member",
        policy_context="a" * 64,
    )
    auth = GatewayAuthorization(Authorization(), context)
    auth.require_scope("context:read")
    auth.require_project("a")
    with pytest.raises(GatewayError):
        auth.require_project("b")
    auth.require_scope("context:write")
    auth.require_project("b")
    with pytest.raises(GatewayError):
        auth.require_project("a")
    summary = auth.describe()
    assert summary["projects"] == [] and len(summary["action_resources"]) == 2


def test_every_new_collection_and_route_has_an_explicit_boundary():
    from app.api.routes.managed_agents import router
    from app.identity.repository import COLLECTION_OWNERSHIP
    from app.managed_agents.inventory import COLLECTION_OWNERSHIP as OWNERSHIP
    from app.managed_agents.schema import INDEXES, VALIDATORS

    assert set(OWNERSHIP) == set(VALIDATORS) <= set(COLLECTION_OWNERSHIP)
    for name, indexes in INDEXES.items():
        assert name in OWNERSHIP
        assert all(next(iter(i.document["key"])) == "org_id" for i in indexes)
    for route in router.routes:
        assert "context" in route.endpoint.__annotations__
    from app.db.migrations.runner import MIGRATIONS

    assert [m.version for m in MIGRATIONS] == list(range(1, 43))


def test_operational_replay_is_own_client_and_action_bound_without_corpus_widening():
    from app.security.policy.resources import resource_allows, resource_filter
    from app.security.tenant_policy import ResourceGrant, TenantPolicy

    policy = TenantPolicy(
        "org-a",
        "member",
        (ResourceGrant("context:write", "project", "include", ("a",)),),
        client_id="worker",
        managed=True,
        subject_kind="machine",
    )
    for collection in ("gateway_idempotency", "agent_activity"):
        assert resource_allows(
            policy, "context:write", {"org_id": "org-a", "client_id": "worker"}, collection
        )
        assert not resource_allows(
            policy, "context:write", {"org_id": "org-a", "client_id": "foreign"}, collection
        )
        assert not resource_allows(
            policy, "context:read", {"org_id": "org-a", "client_id": "worker"}, collection
        )
        assert resource_filter(policy, "context:write", collection) == {
            "org_id": "org-a",
            "client_id": "worker",
        }
    assert policy.selector("context:write", "client_memory") == ()
    assert not resource_allows(
        policy, "context:write", {"org_id": "org-a", "project_id": "b"}, "project_context"
    )


def test_machine_context_tracks_effective_policy_not_unrelated_org_metadata():
    from dataclasses import replace

    from app.security.tenant_policy import ResourceGrant, TenantPolicy

    policy = TenantPolicy(
        "org-a",
        "member",
        (ResourceGrant("context:read", "project", "include", ("a",)),),
        client_id="worker",
        managed=True,
        subject_kind="machine",
        revision=1,
        subject_revision=1,
        machine_key_id="key",
        sponsor_ids=("member",),
        sponsorship_revision=1,
    )
    assert policy.fingerprint == replace(policy, revision=2).fingerprint
    assert policy.fingerprint != replace(policy, subject_revision=2).fingerprint
    assert policy.fingerprint != replace(policy, sponsorship_revision=2).fingerprint
    assert policy.fingerprint != replace(policy, grants=()).fingerprint
    human = replace(policy, subject_kind="human")
    assert human.fingerprint != replace(human, revision=2).fingerprint


@pytest.mark.parametrize("mode,ids", [("none", ()), ("include", ("a",)), ("all_in_org", ())])
def test_legacy_projections_preserve_none_and_project_directory_dimensions(mode, ids):
    from app.managed_agents.policy import projection

    selected = projection(
        (grant("projects:read", ids=ids, mode=mode, kind="project_directory"),),
        "project",
        "project_directory",
    )
    assert selected.mode == mode and selected.ids == ids
    assert projection((grant(kind="skill"),), "project", "project_directory").mode == "none"


def test_two_50_id_tuples_keep_the_existing_100_id_budget_without_widening():
    from app.managed_agents.policy import projection, restrict

    ids = tuple("project-" + str(i) for i in range(100))
    tuples = (grant(ids=ids[:50]), grant(ids=ids[50:]))
    combined = intersect(tuples, tuples)
    assert len(combined) == 2 and all(len(g.ids) == 50 for g in combined)
    assert set(projection(combined, "project", "project_directory").ids) == set(ids)
    bounded = restrict((grant(mode="all_in_org", ids=()),), project=ids)
    assert {i for g in bounded for i in g.ids} == set(ids)
    assert all(g.selector == "include" for g in bounded)
    with pytest.raises(PolicyDenied):
        projection((*tuples, grant(ids=("one-too-many",))), "project")
