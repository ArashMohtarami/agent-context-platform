"""What each tool actually calls, and what it gives back.

A tool's contract is the request it issues, so these drive the real handlers
against a fake Context API and assert the method, the path, and every filter
that reached it. A tool that dropped an argument or invented an endpoint fails
here rather than in production.
"""

from typing import Any

import pytest

from app.mcp.auth import Authorization
from app.mcp.server import dispatch
from tests.mcp_doubles import (
    RecordedApi,
    api_client,
    context_record,
    memory_record,
    project_record,
    readiness_body,
    search_body,
    search_result,
    skill_record,
)

PROJECT = "sampleco-payment"
GRANT = Authorization()


async def call(api: RecordedApi, name: str, arguments: dict[str, Any]) -> Any:
    """Run one tool against a fake API and return its structured answer."""
    client = api_client(api)
    try:
        result = await dispatch(name, arguments, client=client, authorization=GRANT)
    finally:
        await client.close()
    assert result.isError is False, result.content
    return result


@pytest.mark.asyncio
async def test_list_skills_passes_every_filter_to_the_api() -> None:
    api = RecordedApi()
    api.respond("GET", "/api/v1/skills", {"items": [skill_record()], "next_cursor": "abc"})

    result = await call(
        api,
        "list_skills",
        {"status": "superseded", "category": "backend", "tag": "python", "limit": 5},
    )

    assert api.paths == ["/api/v1/skills"]
    assert api.query() == {
        "status": "superseded",
        "category": "backend",
        "tag": "python",
        "limit": "5",
    }
    assert result.structuredContent is not None
    assert result.structuredContent["next_cursor"] == "abc"


@pytest.mark.asyncio
async def test_list_skills_returns_metadata_without_storage_internals() -> None:
    api = RecordedApi()
    api.respond("GET", "/api/v1/skills", {"items": [skill_record()], "next_cursor": None})

    result = await call(api, "list_skills", {})

    assert result.structuredContent is not None
    skill = result.structuredContent["skills"][0]
    assert skill["skill_id"] == "fastapi-production"
    assert skill["version"] == "1.0.0"
    # A content hash and a source path are provenance for an operator, not
    # something a model can use, so they never reach the prompt.
    assert "content_hash" not in skill
    assert "source_path" not in skill


@pytest.mark.asyncio
async def test_get_skill_reads_one_version_with_its_content() -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v1/skills/fastapi-production",
        {
            "skill": skill_record(),
            "versions": [{"version": "1.0.0", "status": "active"}],
            "chunks": [{"chunk_id": "chunk-0000", "section": "Rules", "content": "Use 429."}],
            "next_chunk_cursor": None,
        },
    )

    result = await call(api, "get_skill", {"skill_id": "fastapi-production", "version": "1.0.0"})

    assert api.query() == {"version": "1.0.0", "chunk_limit": "20"}
    assert result.structuredContent is not None
    assert result.structuredContent["skill"]["skill_id"] == "fastapi-production"
    assert "Use 429." in result.content[0].text  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_search_skills_uses_the_unified_pipeline_restricted_to_skills() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v1/search", search_body([search_result(source="active_skill")]))

    result = await call(
        api,
        "search_skills",
        {"query": "rate limiting", "category": "backend", "tags": ["python"], "limit": 3},
    )

    # Skills are ranked by the same pipeline as everything else, from one
    # source, rather than through a second retrieval path.
    assert api.paths == ["/api/v1/search"]
    body = api.body()
    assert body["sources"] == ["active_skill"]
    assert body["query"] == "rate limiting"
    assert body["category"] == "backend"
    assert body["tags"] == ["python"]
    assert body["limit"] == 3
    assert result.structuredContent is not None
    assert result.structuredContent["results"][0]["source"] == "active_skill"


@pytest.mark.asyncio
async def test_list_projects_and_get_project_read_the_directory() -> None:
    api = RecordedApi()
    api.respond("GET", "/api/v1/projects", {"items": [project_record()], "next_cursor": None})
    api.respond("GET", f"/api/v1/projects/{PROJECT}", project_record())

    listed = await call(api, "list_projects", {"limit": 5})
    loaded = await call(api, "get_project", {"project_id": PROJECT})

    assert listed.structuredContent is not None
    assert loaded.structuredContent is not None
    assert listed.structuredContent["projects"][0]["project_id"] == PROJECT
    assert loaded.structuredContent["project"]["environment"] == "production"
    assert api.paths == ["/api/v1/projects", f"/api/v1/projects/{PROJECT}"]


@pytest.mark.asyncio
async def test_resolve_project_uses_the_client_neutral_binding_endpoint() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v4/project-bindings/resolve",
        {
            "status": "resolved",
            "project_id": PROJECT,
            "confidence": 1.0,
            "matched_by": "repository_url",
            "candidates": [],
        },
    )

    result = await call(
        api,
        "resolve_project",
        {"repository_url": "https://github.com/example/payment-service.git"},
    )

    assert api.paths == ["/api/v4/project-bindings/resolve"]
    assert api.body() == {"repository_url": "https://github.com/example/payment-service.git"}
    assert result.structuredContent is not None
    assert result.structuredContent["project_id"] == PROJECT
    assert result.structuredContent["matched_by"] == "repository_url"


@pytest.mark.asyncio
async def test_resolve_project_accepts_workspace_alias_without_a_client_specific_route() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v4/project-bindings/resolve",
        {
            "status": "resolved",
            "project_id": PROJECT,
            "confidence": 1.0,
            "matched_by": "workspace_alias",
            "candidates": [],
        },
    )

    result = await call(api, "resolve_project", {"workspace_alias": "payment-service"})

    assert api.paths == ["/api/v4/project-bindings/resolve"]
    assert api.body() == {"workspace_hint": "payment-service"}
    assert result.structuredContent is not None
    assert result.structuredContent["project_id"] == PROJECT
    assert result.structuredContent["matched_by"] == "workspace_alias"


@pytest.mark.asyncio
async def test_search_context_searches_exactly_one_project() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v1/context/search",
        {
            "project_id": PROJECT,
            "query": "idempotency",
            "results": [{"source": "project_context", "score": 0.9, "context": context_record()}],
        },
    )

    result = await call(
        api,
        "search_context",
        {
            "project_id": PROJECT,
            "query": "idempotency",
            "types": ["architecture_decision"],
            "minimum_importance": 0.5,
            "phrase": True,
            "limit": 4,
        },
    )

    body = api.body()
    assert body["project_id"] == PROJECT
    assert body["types"] == ["architecture_decision"]
    assert body["minimum_importance"] == 0.5
    assert body["phrase"] is True
    assert body["limit"] == 4
    assert result.structuredContent is not None
    assert result.structuredContent["results"][0]["metadata"]["project_id"] == PROJECT


@pytest.mark.asyncio
async def test_get_context_scopes_the_lookup_by_project() -> None:
    api = RecordedApi()
    api.respond("GET", "/api/v1/context/6720a1b2c3d4e5f60718293a", context_record())

    result = await call(
        api,
        "get_context",
        {"project_id": PROJECT, "context_id": "6720a1b2c3d4e5f60718293a"},
    )

    # The project scopes the query rather than being checked afterwards.
    assert api.query() == {"project_id": PROJECT}
    assert result.structuredContent is not None
    assert result.structuredContent["context"]["context_id"] == "6720a1b2c3d4e5f60718293a"


@pytest.mark.asyncio
async def test_save_context_stores_one_item_in_the_named_project() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v1/context", context_record(), status_code=201)

    result = await call(
        api,
        "save_context",
        {
            "project_id": PROJECT,
            "type": "architecture_decision",
            "title": "Payment idempotency strategy",
            "content": "Payment requests carry a UUID4 idempotency key.",
            "importance": 0.9,
            "external_id": "payment-idempotency",
        },
    )

    body = api.body()
    assert body["project_id"] == PROJECT
    assert body["external_id"] == "payment-idempotency"
    # The writer identifies itself by default, so stored knowledge says where
    # it came from without the caller having to remember to say so.
    assert body["source"] == "mcp"
    assert result.structuredContent is not None
    assert result.structuredContent["context"]["revision"] == 1


@pytest.mark.asyncio
async def test_search_memory_passes_the_scopes_it_was_given() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v1/memory/search",
        {
            "query": "typing",
            "project_id": PROJECT,
            "results": [{"source": "memory", "score": 0.8, "memory": memory_record()}],
        },
    )

    result = await call(
        api,
        "search_memory",
        {"query": "typing", "project_id": PROJECT, "scopes": ["global", "project"], "limit": 4},
    )

    body = api.body()
    assert body["scopes"] == ["global", "project"]
    assert body["project_id"] == PROJECT
    assert result.structuredContent is not None
    item = result.structuredContent["results"][0]
    assert item["title"] == "Strict typing everywhere"
    assert item["metadata"]["scope"] == "global"
    assert item["metadata"]["revision"] == 1


@pytest.mark.asyncio
async def test_agent_memory_tools_never_accept_or_forward_a_client_id() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v1/memory",
        memory_record(scope="agent", client_id="claude-code-main"),
        status_code=201,
    )

    await call(
        api,
        "save_memory",
        {
            "type": "note",
            "content": "Operational checkpoint for this client.",
            "importance": 0.4,
            "confidence": 0.9,
            "scope": "agent",
        },
    )

    assert api.body()["scope"] == "agent"
    assert "client_id" not in api.body()


@pytest.mark.asyncio
async def test_save_memory_can_correct_an_earlier_memory() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v1/memory",
        memory_record(supersedes="6720a1b2c3d4e5f60718293c"),
        status_code=201,
    )

    result = await call(
        api,
        "save_memory",
        {
            "type": "convention",
            "content": "Deploys never happen on Fridays.",
            "importance": 0.9,
            "confidence": 0.95,
            "supersedes": "6720a1b2c3d4e5f60718293c",
        },
    )

    assert api.body()["supersedes"] == "6720a1b2c3d4e5f60718293c"
    assert result.structuredContent is not None
    assert result.structuredContent["memory"]["supersedes"] == "6720a1b2c3d4e5f60718293c"


@pytest.mark.asyncio
async def test_update_memory_sends_only_the_fields_it_was_asked_to_change() -> None:
    api = RecordedApi()
    api.respond(
        "PATCH",
        "/api/v1/memory/6720a1b2c3d4e5f60718293b",
        memory_record(status="archived", revision=2),
    )

    await call(
        api,
        "update_memory",
        {
            "memory_id": "6720a1b2c3d4e5f60718293b",
            "status": "archived",
            "expected_revision": 1,
        },
    )

    body = api.body()
    assert body == {"status": "archived", "expected_revision": 1}
    # A global memory is addressed without a project, and none is invented.
    assert api.query() == {}


@pytest.mark.asyncio
async def test_update_memory_scopes_a_project_memory_by_its_project() -> None:
    api = RecordedApi()
    api.respond("PATCH", "/api/v1/memory/6720a1b2c3d4e5f60718293b", memory_record(PROJECT))

    await call(
        api,
        "update_memory",
        {
            "memory_id": "6720a1b2c3d4e5f60718293b",
            "project_id": PROJECT,
            "importance": 0.4,
            "expected_revision": 1,
        },
    )

    assert api.query() == {"project_id": PROJECT}


@pytest.mark.asyncio
async def test_search_knowledge_reaches_every_source_by_default() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v1/search",
        search_body(
            [
                search_result(source="project_context"),
                search_result(source="active_skill", title="FastAPI: Rules", project_id=None),
            ],
            project_id=PROJECT,
        ),
    )

    result = await call(api, "search_knowledge", {"query": "idempotency", "project_id": PROJECT})

    body = api.body()
    assert body["sources"] is None
    assert body["project_id"] == PROJECT
    assert result.structuredContent is not None
    assert [item["source"] for item in result.structuredContent["results"]] == [
        "project_context",
        "active_skill",
    ]
    # The platform's own retrieval metadata is passed through, which is what
    # makes a surprising answer explainable rather than a guess.
    assert result.structuredContent["retrieval"]["retrieval_method"] == "hybrid"
    assert result.structuredContent["token_budget"] == 4_000


@pytest.mark.asyncio
async def test_get_system_status_reports_a_healthy_platform() -> None:
    api = RecordedApi()
    api.respond("GET", "/health/ready", readiness_body())

    result = await call(api, "get_system_status", {})

    assert result.structuredContent is not None
    assert result.structuredContent["ready"] is True
    assert result.structuredContent["unavailable"] == []
    assert result.structuredContent["embedding_model"].endswith("all-MiniLM-L6-v2")


@pytest.mark.asyncio
async def test_get_system_status_still_answers_while_the_platform_is_failing() -> None:
    api = RecordedApi()
    api.respond("GET", "/health/ready", readiness_body(ready=False), status_code=503)

    result = await call(api, "get_system_status", {})

    # The one tool that explains an outage must not become a casualty of it.
    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["ready"] is False
    assert result.structuredContent["unavailable"] == ["mongot", "vector_search"]
    assert "FAILING" in result.content[0].text  # type: ignore[union-attr]
