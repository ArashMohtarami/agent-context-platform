"""Candidate guards execute inside mongot stages, before limits/ranking."""

from unittest.mock import MagicMock as Mock

import pytest

from app.db.tenant_collection import TenantCollection
from app.security.tenant import TenantContext, TenantContextRequired, tenant_scope
from app.security.tenant_policy import ResourceGrant, TenantPolicy, policy_scope


async def test_lexical_vector_and_lookup_candidates_have_org_resource_prefilters():
    policy = TenantPolicy(
        "org-a", "key", (ResourceGrant("search:read", "project", "include", ("same-project",)),)
    )
    raw = Mock()
    adapter = TenantCollection(raw, "project_context", lambda: TenantContext("org-a"))
    with tenant_scope(TenantContext("org-a")), policy_scope(policy, "search:read"):
        lexical = await adapter.pipeline(
            [
                {
                    "$search": {
                        "compound": {"must": [{"text": {"path": "content", "query": "private"}}]}
                    }
                },
                {"$limit": 1},
            ]
        )
        assert "org-a" in str(lexical[0]["$search"]["compound"]["filter"])
        assert "same-project" in str(lexical[0]["$search"]["compound"]["filter"])
        vector = await adapter.pipeline(
            [{"$vectorSearch": {"index": "vector", "limit": 1, "filter": {"org_id": "org-b"}}}]
        )
        assert vector[0]["$vectorSearch"]["filter"]["$and"][0] == {"org_id": "org-b"}
        assert "org-a" in str(vector[0]["$vectorSearch"]["filter"]["$and"][1])
        joined = await adapter.pipeline(
            [{"$lookup": {"from": "project_context", "pipeline": [{"$limit": 1}], "as": "matches"}}]
        )
        assert "org-a" in str(joined[1]["$lookup"]["pipeline"][0])
        with pytest.raises(TenantContextRequired):
            await adapter.pipeline([{"$unionWith": "memories"}])
        with pytest.raises(TenantContextRequired):
            await adapter.pipeline([{"$facet": {"unscoped": []}}])


async def test_defensive_search_result_check_refuses_foreign_ownership():
    adapter = TenantCollection(Mock(), "skills", lambda: TenantContext("org-a"))
    with tenant_scope(TenantContext("org-a")):
        with pytest.raises(TenantContextRequired):
            await adapter._verify({"org_id": "org-b", "skill_id": "same-skill"})
        with pytest.raises(TenantContextRequired):
            await adapter._verify({"org_id": "org-a", "matches": [{"org_id": "org-b"}]})


async def test_empty_resource_vector_filter_compiles_to_a_valid_false_predicate():
    from unittest.mock import AsyncMock

    raw = Mock()
    cursor = Mock()
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])
    raw.find.return_value = cursor
    adapter = TenantCollection(raw, "project_context", lambda: TenantContext("org-a"))
    policy = TenantPolicy("org-a", "key", (ResourceGrant("search:read", "project", "none"),))
    with tenant_scope(TenantContext("org-a")), policy_scope(policy, "search:read"):
        stages = await adapter.pipeline([{"$vectorSearch": {"filter": {}, "limit": 1}}])
        assert stages[0]["$vectorSearch"]["filter"]["$and"][1] == {
            "$and": [{"org_id": "org-a"}, {"org_id": {"$ne": "org-a"}}],
        }
