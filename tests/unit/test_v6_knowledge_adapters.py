"""The human and machine adapters share exact action/resource enforcement."""

from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.api.routes.knowledge import router
from app.knowledge.boundary import action_gate, resource, shared_memory
from app.schemas.context import ContextUpdateRequest
from app.schemas.memory import MemoryCreateRequest
from app.schemas.search import KnowledgeSearchRequest
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.resources import resource_allows, resource_filter
from app.security.tenant import TenantContext, TenantContextRequired, tenant_scope
from app.security.tenant_policy import ResourceGrant, TenantPolicy, policy_scope, select_action


def policy(kind="human"):
    return TenantPolicy(
        "org-a",
        "human-or-verified-client",
        (
            ResourceGrant("context:read", "project", "include", ("a",)),
            ResourceGrant("context:write", "project", "include", ("b",)),
            ResourceGrant("memory:read", "project", "include", ("a",)),
            ResourceGrant("memory:write", "project", "include", ("b",)),
            ResourceGrant("memory:read", "organization_knowledge", "include", ("org-a",)),
            ResourceGrant("search:read", "project", "all_in_org"),
            ResourceGrant("skills:read", "skill", "include", ("canonical",)),
            ResourceGrant("search:read", "skill", "include", ("canonical",)),
        ),
        managed=True,
        subject_kind=kind,
    )


@pytest.mark.parametrize("subject_kind", ["human", "machine"])
def test_human_machine_rights_are_action_coupled_before_queries(subject_kind):
    p = policy(subject_kind)
    for action, allowed, denied in (("context:read", "a", "b"), ("context:write", "b", "a")):
        with tenant_scope(TenantContext("org-a")), policy_scope(p, action):
            resource(action, "project", allowed)
            with pytest.raises(PolicyDenied):
                resource(action, "project", denied)
            assert resource_allows(
                p, action, {"org_id": "org-a", "project_id": allowed}, "project_context"
            )
            assert not resource_allows(
                p, action, {"org_id": "org-a", "project_id": denied}, "project_context"
            )
            assert not resource_allows(
                p, action, {"org_id": "org-b", "project_id": allowed}, "project_context"
            )


def test_search_prefilter_intersects_source_read_instead_of_borrowing_write():
    p = policy()
    query = resource_filter(p, "search:read", "project_context")
    assert query["$and"][1]["$and"][1]["$or"][0] == {"project_id": {"$in": ["a"]}}
    for project, allowed in (("a", True), ("b", False)):
        assert (
            resource_allows(
                p, "search:read", {"org_id": "org-a", "project_id": project}, "project_context"
            )
            is allowed
        )
    assert not resource_allows(p, "search:read", {"org_id": "org-a", "skill_id": "other"}, "skills")


@pytest.mark.parametrize("scope,project", [("agent", None), ("project_agent", "a")])
def test_management_never_manufactures_a_private_memory_client(scope, project):
    from dataclasses import replace

    human = policy()
    human = replace(
        human,
        grants=(
            *human.grants,
            ResourceGrant("memory:read", "client_memory", "all_in_org"),
            ResourceGrant("clients:manage:org", "organization", "include", ("org-a",)),
        ),
    )
    with tenant_scope(TenantContext("org-a")), policy_scope(human, "memory:read"):
        with pytest.raises(PolicyDenied):
            shared_memory("memory:read", scope, project)
        assert shared_memory("memory:read", "project", "a") == "project"
        assert shared_memory("memory:read", "global", None) == "global"
        with pytest.raises(PolicyDenied):
            shared_memory("memory:write", "project", "a")


def test_none_missing_policy_and_unknown_fields_fail_closed():
    with (
        tenant_scope(TenantContext("org-a")),
        policy_scope(TenantPolicy("org-a", "none", (), managed=True)),
    ):
        with pytest.raises(TenantContextRequired):
            select_action("context:read")
        with pytest.raises(PolicyDenied):
            resource("context:read", "project", "a")
    with pytest.raises(ValidationError):
        MemoryCreateRequest(
            type="note",
            content="x",
            importance=0.5,
            confidence=0.5,
            source="test",
            client_id="someone-else",
        )
    with pytest.raises(ValidationError):
        ContextUpdateRequest(content="replace without a revision")
    with pytest.raises(ValidationError):
        KnowledgeSearchRequest(query="q", org_id="org-b")


def test_every_route_is_typed_session_authenticated_and_skills_are_read_only():
    assert len(router.routes) == 14
    for route in router.routes:
        assert isinstance(route, APIRoute)
        assert route.response_model is not None
        dependencies = route.dependant.dependencies
        assert any(d.call.__name__ == "gate" for d in dependencies)
        gate = next(d for d in dependencies if d.call.__name__ == "gate")
        assert any(d.call.__name__ == "current_browser" for d in gate.dependencies)
        assert any(p.name == "org_id" for p in gate.path_params)
        assert all("principal" not in d.call.__name__ for d in dependencies)
        if "/skills" in route.path:
            assert route.methods == {"GET"}


@pytest.mark.asyncio
async def test_gate_refuses_a_machine_even_if_its_grants_match(monkeypatch):
    from unittest.mock import AsyncMock

    from app.knowledge import boundary

    monkeypatch.setattr(boundary, "enabled", AsyncMock())
    with tenant_scope(TenantContext("org-a")), policy_scope(policy("machine")):
        with pytest.raises(PolicyDenied):
            await action_gate("context:read")(SimpleNamespace(), SimpleNamespace(), "org-a")
