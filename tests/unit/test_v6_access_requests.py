"""Exact independent decisions, source coverage and session-only API contracts."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.db.migrations.runner import MIGRATIONS, run_migrations
from app.identity.repository import COLLECTION_OWNERSHIP
from app.main import create_app
from app.management.inventory import COLLECTION_OWNERSHIP as HUMAN_OWNERSHIP
from app.management.models import Decision, RequestCreate
from app.management.schema import ASSIGNMENT_VALIDATOR, INDEXES, VALIDATORS
from app.models.common import utc_now
from app.security.policy.models import Grant, covered
from tests.unit.test_v6_invites import policy


def test_each_new_collection_is_classified_strict_and_indexed_with_org_first():
    assert [m.version for m in MIGRATIONS][-3:] == [40, 41, 42]
    assert set(VALIDATORS) == set(HUMAN_OWNERSHIP) <= set(COLLECTION_OWNERSHIP)
    for name, validator in VALIDATORS.items():
        assert validator["$jsonSchema"]["additionalProperties"] is False
        if not name.startswith("_"):
            assert "org_id" in validator["$jsonSchema"]["required"]
            assert all(next(iter(i.document["key"])) == "org_id" for i in INDEXES[name])
    assert "team_generation" in ASSIGNMENT_VALIDATOR["$jsonSchema"]["properties"]
    decision_index = INDEXES["access_decisions"][0].document
    assert decision_index["unique"] is True
    assert list(decision_index["key"]) == ["org_id", "request_id"]


@pytest.mark.parametrize(
    "body",
    [
        {"expected_revision": 0, "expected_policy_revision": 1, "outcome": "approved"},
        {"expected_revision": 1, "expected_policy_revision": 0, "outcome": "approved"},
        {
            "expected_revision": 1,
            "expected_policy_revision": 1,
            "outcome": "approved",
            "tuples": [],
        },
        {"expected_revision": 1, "expected_policy_revision": 1, "outcome": "replace"},
    ],
)
def test_decision_cannot_mutate_the_exact_requested_policy_or_omit_revisions(body):
    with pytest.raises(ValidationError):
        Decision.model_validate(body)


def test_access_request_requires_exact_policy_revision_expiry_and_preserves_action_coupling():
    request = RequestCreate(
        policy=policy(), expected_policy_revision=2, expires_at=utc_now() + timedelta(hours=1)
    )
    assert request.policy.issue("member").subject_id == "member"
    ceiling = (
        Grant(action="context:read", resource_kind="project", selector="include", ids=["a"]),
        Grant(action="context:write", resource_kind="project", selector="include", ids=["b"]),
    )
    assert not covered(
        Grant(action="context:write", resource_kind="project", selector="include", ids=["a"]),
        ceiling,
    )
    with pytest.raises(ValidationError):
        RequestCreate(
            policy=policy(), expected_policy_revision=2, expires_at=utc_now() + timedelta(days=2)
        )


def test_human_endpoints_are_session_only_and_no_decision_edit_or_public_signup_exists():
    schema = create_app().openapi()
    paths = schema["paths"]
    assert "post" in paths["/api/v6/organizations/{org_id}/access-requests/{request_id}/decisions"]
    assert (
        "patch"
        not in paths["/api/v6/organizations/{org_id}/access-requests/{request_id}/decisions"]
    )
    assert not any("signup" in p or "register" in p for p in paths if p.startswith("/api/v6"))
    for path, methods in paths.items():
        if any(s in path for s in ("/members", "/teams", "/access-requests")) and path.startswith(
            "/api/v6/organizations/"
        ):
            for method, operation in methods.items():
                if isinstance(operation, dict):
                    assert all(
                        p["name"] not in {"email", "provider", "password_hash"}
                        for p in operation.get("parameters", [])
                    ), (method, path)


async def test_schema41_cannot_be_opened_by_old_workers_or_silently_migrated_at_startup():
    class History:
        async def create_index(self, *args, **kwargs):
            pass

        def find(self, *args, **kwargs):
            async def rows():
                for version in range(1, 42):
                    yield {"version": version}

            return rows()

    with pytest.raises(RuntimeError, match="schema-41"):
        await run_migrations(
            {"_schema_migrations": History()},
            tenant_foundation=True,
            tenant_isolation=True,
            local_identity=True,
            sso=True,
            management_policy=True,
        )


async def test_decision_repository_rejects_updates_before_touching_mongodb():
    from unittest.mock import Mock

    from app.identity.repository import IdentityStore

    database = Mock()
    with pytest.raises(ValueError, match="append only"):
        await IdentityStore(database).update(
            "access_decisions", {"org_id": "org-a"}, {"$set": {"outcome": "approved"}}
        )
    assert not database.mock_calls


def test_every_human_management_route_requires_the_actual_browser_boundary():
    from fastapi.routing import APIRoute

    from app.api.routes.human_management import router
    from app.identity.browser import current_browser

    assert len(router.routes) == 18
    for route in router.routes:
        assert isinstance(route, APIRoute)
        assert any(
            dependency.call is current_browser for dependency in route.dependant.dependencies
        ), route.path
        assert route.response_model is not None
