"""Managed decisions use complete action/resource tuples; legacy is explicit."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.db.tenant_schema import TENANT_COLLECTIONS
from app.identity.models import MANAGEMENT_ACTIONS
from app.security.policy.catalog import ROLE_PERMISSIONS, ROLES, can_appoint, role_allows
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.models import Grant, Selector, covered
from app.security.policy.resources import RESOURCE_PATHS, resource_allows, resource_filter
from app.security.policy.reviews import require_review
from app.security.principal import Principal
from app.security.scopes import ALL_SCOPES
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_policy import ResourceGrant, TenantPolicy, policy_of, policy_scope

# Independent transcription of the reviewed 28 x 9 matrix. Columns follow ROLES.
MATRIX = {
    "org:read": "011111111",
    "org:manage": "010000000",
    "org:transfer-owner": "010000000",
    "members:read": "011010000",
    "members:invite": "011010000",
    "members:suspend": "011010000",
    "members:reinstate": "011010000",
    "teams:read": "011110000",
    "teams:manage": "011000000",
    "grants:read": "011111111",
    "grants:delegate": "011010000",
    "grants:all-in-org": "010000000",
    "clients:read:own": "011111111",
    "clients:read:team": "000010000",
    "clients:read:org": "011000000",
    "clients:manage:own": "000000000",
    "clients:manage:team": "000010000",
    "clients:manage:org": "011000000",
    "credentials:issue:own": "000000000",
    "credentials:revoke:own": "000000000",
    "credentials:revoke:team": "000010000",
    "credentials:revoke:org": "011000000",
    "activity:read:self": "011111111",
    "activity:read:team": "000010000",
    "activity:read:org": "011100010",
    "audit:read:org": "011000010",
    "reports:export": "011000010",
    "reviews:decide": "000001000",
}


@pytest.mark.parametrize("action", sorted(MATRIX))
@pytest.mark.parametrize("index,role", enumerate(ROLES))
def test_complete_management_allow_deny_matrix(action, index, role):
    assert role_allows(role, action) == (MATRIX[action][index] == "1")


def test_fixed_catalog_has_no_corpus_or_operator_bypass():
    assert set(MATRIX) == MANAGEMENT_ACTIONS and len(MATRIX) == 28 and len(ROLES) == 9
    assert all(not set(ALL_SCOPES) & permissions for permissions in ROLE_PERMISSIONS.values())
    assert ROLE_PERMISSIONS["installation_operator"] == frozenset()
    assert not role_allows("executive", "grants:delegate")
    assert role_allows("executive", "grants:delegate", explicit=True)
    assert role_allows("contributor", "credentials:issue:own", explicit=True)
    assert not can_appoint("team_lead", "team_lead")
    assert not can_appoint("team_lead", "org_admin")
    assert not can_appoint("org_owner", "org_owner")


def test_none_is_default_and_never_serializes_as_all():
    grant = Grant(action="memory:read", resource_kind="organization_knowledge")
    assert grant.model_dump()["selector"] == "none" and grant.ids == []
    assert Selector().model_dump() == {"mode": "none", "ids": ()}
    for mode, ids in (
        ("include", []),
        ("none", ["a"]),
        ("all_in_org", ["a"]),
        ("include", ["a", "a"]),
    ):
        with pytest.raises(ValidationError):
            Selector(mode=mode, ids=ids)
        with pytest.raises(ValidationError):
            Grant(action="context:read", resource_kind="project", selector=mode, ids=ids)
    with pytest.raises(ValidationError):
        Grant(action="arbitrary:script", resource_kind="project")
    with pytest.raises(ValidationError):
        Grant(action="context:read", resource_kind="project", conditions={"script": "allow"})


def policy(*grants):
    return TenantPolicy("org-a", "reviewer", tuple(grants), managed=True, subject_kind="human")


def test_read_a_write_b_never_forms_a_cartesian_product_or_legacy_summary():
    p = policy(
        ResourceGrant("context:read", "project", "include", ("a",)),
        ResourceGrant("context:write", "project", "include", ("b",)),
    )
    principal = Principal(
        "reviewer",
        "Reviewer",
        ("context:read", "context:write"),
        projects=None,
        org_id="org-a",
        managed_policy=p,
    )
    with tenant_scope(TenantContext("org-a")), policy_scope(p, "context:write"):
        assert not principal.allows_project("a") and principal.allows_project("b")
        assert not principal.allows_project(None)
        assert not resource_allows(
            p, "context:write", {"org_id": "org-a", "project_id": "a"}, "project_context"
        )
        assert resource_allows(
            p, "context:write", {"org_id": "org-a", "project_id": "b"}, "project_context"
        )
        assert resource_filter(p, "context:write", "project_context") != resource_filter(
            p, "context:read", "project_context"
        )
    assert principal.describe()["projects"] == []
    assert policy_of(principal) is p
    assert p.fingerprint != replace(p, revision=2).fingerprint
    assert not covered(
        Grant(action="context:write", resource_kind="project", selector="include", ids=["a"]),
        (Grant(action="context:read", resource_kind="project", selector="all_in_org"),),
    )


def test_every_served_source_has_an_explicit_managed_resource_gate():
    assert set(RESOURCE_PATHS) == set(TENANT_COLLECTIONS)
    p = policy()
    for collection in TENANT_COLLECTIONS:
        assert not resource_allows(p, "search:read", {"org_id": "org-a"}, collection)
        assert "org-a" in repr(resource_filter(p, "search:read", collection))


def test_org_global_skill_private_and_research_dimensions_are_explicit():
    p = policy(ResourceGrant("memory:read", "project", "all_in_org"))
    global_memory = {"org_id": "org-a", "scope": "global"}
    assert not resource_allows(p, "memory:read", global_memory, "memories")
    assert not resource_allows(p, "skills:read", {"org_id": "org-a", "skill_id": "same"}, "skills")
    assert not resource_allows(
        p, "memory:read", {"org_id": "org-a", "scope": "agent", "client_id": "same"}, "memories"
    )
    p = replace(
        p,
        grants=(
            *p.grants,
            ResourceGrant("memory:read", "organization_knowledge", "include", ("org-a",)),
        ),
    )
    assert resource_allows(p, "memory:read", global_memory, "memories")
    assert not resource_allows(p, "memory:read", {**global_memory, "org_id": "org-b"}, "memories")
    research = {
        "org_id": "org-a",
        "research_project_id": "research",
        "authorization": {"project_ids": ["a"]},
    }
    p = policy(
        ResourceGrant("research:read", "research_project", "include", ("research",)),
        ResourceGrant("research:read", "project", "include", ("a",)),
    )
    assert resource_allows(p, "research:read", research, "research_projects")
    assert not resource_allows(
        p, "research:read", {**research, "authorization": {"project_ids": []}}, "research_projects"
    )
    assert not resource_allows(
        p,
        "research:read",
        {**research, "authorization": {"project_ids": ["b"]}},
        "research_projects",
    )


def test_review_needs_read_operation_specific_permission_and_independent_human():
    row = {
        "org_id": "org-a",
        "profile_id": "profile",
        "actor": {"actor_type": "human", "user_id": "author", "org_id": "org-a"},
    }
    tuples = tuple(
        ResourceGrant(action, "creator_profile", "include", ("profile",))
        for action in ("content:read", "content:write", "reviews:decide")
    )
    for missing in ("content:read", "content:write", "reviews:decide"):
        p = policy(*(g for g in tuples if g.action != missing))
        with (
            tenant_scope(TenantContext("org-a")),
            policy_scope(p, "content:write"),
            pytest.raises(PolicyDenied),
        ):
            require_review(row, "content_items", "content:write")
    p = policy(*tuples)
    with tenant_scope(TenantContext("org-a")), policy_scope(p, "content:write"):
        require_review(row, "content_items", "content:write")
        for actor in (
            {"actor_type": "agent", "owner_id": "independent-human"},
            {"actor_type": "human", "user_id": "reviewer", "org_id": "org-a"},
            {},
        ):
            with pytest.raises(PolicyDenied):
                require_review({**row, "actor": actor}, "content_items", "content:write")
    with (
        tenant_scope(TenantContext("org-a")),
        policy_scope(replace(p, subject_kind="machine"), "content:write"),
        pytest.raises(PolicyDenied),
    ):
        require_review(row, "content_items", "content:write")


def test_legacy_empty_lists_and_all_are_kept_distinct_in_admitted_adapter():
    principal = Principal(
        "key", "Legacy", ("context:read",), projects=(), profiles=(), org_id="org-a"
    )
    p = policy_of(principal)
    assert not p.managed and p.selector("context:read", "project") == ()
    assert all(g.mode == "none" for g in p.grants)
    assert policy_of(replace(principal, projects=None)).selector("context:read", "project") is None


def test_search_intersects_operation_specific_resources_before_candidate_selection():
    p = policy(
        ResourceGrant("search:read", "project", "all_in_org"),
        ResourceGrant("context:read", "project", "include", ("a",)),
        ResourceGrant("context:write", "project", "include", ("b",)),
    )
    assert resource_allows(
        p, "search:read", {"org_id": "org-a", "project_id": "a"}, "project_context"
    )
    assert not resource_allows(
        p, "search:read", {"org_id": "org-a", "project_id": "b"}, "project_context"
    )
    assert "'a'" in repr(resource_filter(p, "search:read", "project_context"))


async def test_machine_policy_resolution_is_shared_by_ordinary_and_gateway_authentication():
    from unittest.mock import AsyncMock

    from app.security.principal import AuthenticationError
    from app.services.auth_service import AuthService
    from tests.security_doubles import NOW, FakeApiKeyRepository, issued_key

    record, token = issued_key(scopes=("content:write",))
    keys = FakeApiKeyRepository(record)
    keys.resolve_policy = AsyncMock(side_effect=PolicyDenied("unverified sponsor"))
    service = AuthService(keys, clock=lambda: NOW)
    for method in (service.authenticate, service.authenticate_gateway):
        with pytest.raises(AuthenticationError):
            await method(token)
    assert keys.resolve_policy.await_count == 2


def test_every_governance_route_uses_the_current_session_identity_boundary():
    from app.api.routes.management_policy import router
    from app.identity.browser import current_browser, identity_boundary

    def dependencies(dependant):
        return {
            dependant.call,
            *(call for child in dependant.dependencies for call in dependencies(child)),
        }

    for route in router.routes:
        calls = dependencies(route.dependant)
        assert current_browser in calls and identity_boundary in calls, route.path


def test_adoption_adapter_never_turns_a_bearer_or_admin_key_into_a_verified_reviewer():
    # This exception was explicitly available before managed cutover. Once the
    # adapter is attached to the Phase 5 engine, even old admin keys lack a
    # verified human/sponsor and operation-specific managed review assignment.
    p = TenantPolicy(
        "org-a",
        "old-admin-key",
        (
            ResourceGrant("content:read", "creator_profile", "all_in_org"),
            ResourceGrant("content:write", "creator_profile", "all_in_org"),
        ),
        engine=object(),
    )
    with (
        tenant_scope(TenantContext("org-a")),
        policy_scope(p, "content:write"),
        pytest.raises(PolicyDenied, match="sponsorship"),
    ):
        require_review(
            {"org_id": "org-a", "profile_id": "profile"}, "content_items", "content:write"
        )
