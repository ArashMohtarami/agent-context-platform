"""Action-specific console prefilters and browser-only route boundaries."""

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.api.routes.management_adapters import Binding, Revision, router
from app.management.adapters import client_boundary, project_boundary, require_project
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.models import Grant
from tests.unit.test_v6_memberships import team_snapshot


def grant(action, kind, *ids):
    return Grant(action=action, resource_kind=kind, selector="include", ids=list(ids))


def test_none_read_and_write_resources_do_not_flatten_for_binding_projection():
    snapshot = team_snapshot()
    snapshot["tuples"] = (
        grant("projects:read", "project_directory", "a"),
        grant("projects:write", "project_directory", "b"),
    )
    assert project_boundary(snapshot, "projects:read") == {"project_id": {"$in": ["a"]}}
    require_project(snapshot, "projects:write", "b")
    with pytest.raises(PolicyDenied):
        require_project(snapshot, "projects:write", "a")
    snapshot["tuples"] = ()
    assert project_boundary(snapshot, "projects:read") == {"project_id": {"$in": []}}


def test_machine_catalog_filters_before_paging_and_never_uses_legacy_owner():
    snapshot = team_snapshot()
    assert client_boundary(snapshot) == {"org_id": "org-a", "_id": {"$in": []}}
    snapshot["tuples"] = (grant("clients:read:team", "team", "alpha"),)
    result = client_boundary(snapshot)
    assert result["org_id"] == "org-a"
    assert result["$or"] == [
        {"team_id": "alpha", "team_generation": 1, "sponsor_id": {"$in": ["lead", "member"]}}
    ]
    snapshot["team_links"][("alpha", "member")]["status"] = "revoked"
    assert client_boundary(snapshot)["$or"][0]["sponsor_id"] == {"$in": ["lead"]}
    assert "owner_id" not in str(result)


@pytest.mark.parametrize(
    "model,body",
    [
        (Revision, {"expected_revision": 0}),
        (
            Binding,
            {
                "expected_revision": -1,
                "project_id": "a",
                "binding_type": "workspace_alias",
                "value": "x",
            },
        ),
    ],
)
def test_mutations_need_reviewed_revisions(model, body):
    with pytest.raises(ValidationError):
        model.model_validate(body)


def test_every_adapter_is_session_only_and_has_no_bearer_dependency():
    for route in router.routes:
        assert isinstance(route, APIRoute)
        assert route.path.startswith("/api/v6/organizations/{org_id}/management/")
        calls = [d.call.__name__ for d in route.dependant.dependencies]
        assert "current_browser" in calls
        assert not any("principal" in c or "bearer" in c for c in calls)


def test_console_governance_actions_are_declared_content_free_summary_events():
    from app.summaries.projection import GOVERNANCE_ACTIONS

    assert {
        "machine_session_ended",
        "project_binding_updated",
        "project_binding_deleted",
    } <= GOVERNANCE_ACTIONS
