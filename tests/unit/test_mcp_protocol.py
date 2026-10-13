"""The server as a client actually sees it, over a real MCP session.

These drive the SDK's own client against the real server object, so the
protocol layer — discovery, schema validation, result encoding — is exercised
rather than assumed. Only the Context API behind it is a double.
"""

import json
from typing import Any

import pytest
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from app.mcp.auth import Authorization, build_authorization
from app.mcp.server import build_server
from tests.mcp_doubles import RecordedApi, api_client, project_record, search_body, search_result

PROJECT = "sampleco-payment"
OTHER_PROJECT = "sampleco-wallet"


async def session(api: RecordedApi, authorization: Authorization | None = None) -> Any:
    """Open a connected client session against the real server."""
    client = api_client(api)
    server = build_server(client, authorization or Authorization())
    return create_connected_server_and_client_session(server), client


def text(result: Any) -> str:
    """Return the text a call reported."""
    return "\n".join(block.text for block in result.content if block.type == "text")


@pytest.mark.asyncio
async def test_a_client_discovers_every_tool_with_its_schema() -> None:
    context, client = await session(RecordedApi())
    try:
        async with context as connected:
            listed = await connected.list_tools()
    finally:
        await client.close()

    names = {tool.name for tool in listed.tools}
    # Phase 9 adds four client-neutral lifecycle tools to the established
    # V1--V4 MCP surface.
    assert len(names) == 55
    assert "get_platform_capabilities" in names
    assert "search_knowledge" in names
    assert {
        "begin_agent_session",
        "get_agent_session",
        "update_agent_session",
        "end_agent_session",
    } <= names
    assert {
        "create_research_project",
        "add_research_source",
        "ingest_research_source",
        "search_research",
        "create_research_claim",
        "build_evidence_packet",
        "build_research_brief",
        "propose_knowledge_candidate",
        "approve_knowledge_candidate",
        "promote_knowledge_candidate",
        "review_content_facts",
        "get_creator_profile",
        "search_content",
        "prepare_repurpose_plan",
        "review_content_for_publication",
        "mark_content_published",
        "get_content_metrics",
    } <= names
    assert all(tool.description for tool in listed.tools)
    assert all(tool.inputSchema["type"] == "object" for tool in listed.tools)


@pytest.mark.asyncio
async def test_capability_discovery_uses_the_stable_success_envelope() -> None:
    api = RecordedApi()
    context, client = await session(api)

    try:
        async with context as connected:
            result = await connected.call_tool("get_platform_capabilities", {})
    finally:
        await client.close()

    assert result.isError is False
    assert result.structuredContent is not None
    data = result.structuredContent["data"]
    meta = result.structuredContent["meta"]
    assert data["versions"] == {
        "platform": "0.1.0",
        "knowledge": "1",
        "creator": "2",
        "research": "3",
        "gateway": "4",
    }
    assert data["caller"]["project_access"] == {"mode": "all"}
    serialized = json.dumps(data).lower()
    assert "password" not in serialized
    assert "credential" not in serialized
    assert "mongodb" not in serialized
    assert meta["client_id"] is None
    assert meta["project_id"] is None
    assert meta["token_count"] == 0
    assert isinstance(meta["request_id"], str)
    assert api.requests == []


@pytest.mark.asyncio
async def test_a_call_returns_both_text_and_structured_content() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v1/search", search_body([search_result()], project_id=PROJECT))
    context, client = await session(api)

    try:
        async with context as connected:
            result = await connected.call_tool(
                "search_knowledge",
                {"query": "idempotency", "project_id": PROJECT},
            )
    finally:
        await client.close()

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["results"][0]["source"] == "project_context"
    assert result.structuredContent["meta"]["project_id"] == PROJECT
    assert result.structuredContent["meta"]["token_count"] == 28
    assert "SOURCE: project_context" in text(result)


@pytest.mark.asyncio
async def test_resolve_project_returns_a_deterministic_binding_over_the_mcp_protocol() -> None:
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
    context, client = await session(api)

    try:
        async with context as connected:
            result = await connected.call_tool(
                "resolve_project",
                {"repository_url": "https://github.com/example/payment-service.git"},
            )
    finally:
        await client.close()

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["project_id"] == PROJECT
    assert api.paths == ["/api/v4/project-bindings/resolve"]


@pytest.mark.asyncio
async def test_the_protocol_layer_rejects_an_argument_the_schema_forbids() -> None:
    api = RecordedApi()
    context, client = await session(api)

    try:
        async with context as connected:
            result = await connected.call_tool(
                "search_knowledge",
                {"query": "x", "pipeline": [{"$match": {}}]},
            )
    finally:
        await client.close()

    # Refused by the published schema, before this server's own validation and
    # long before anything could have reached the platform.
    assert result.isError is True
    assert api.requests == []


@pytest.mark.asyncio
async def test_memory_tools_refuse_a_caller_supplied_client_identity() -> None:
    api = RecordedApi()
    context, client = await session(api)

    try:
        async with context as connected:
            result = await connected.call_tool(
                "save_memory",
                {
                    "type": "note",
                    "content": "Attempt to impersonate another client.",
                    "importance": 0.4,
                    "confidence": 0.9,
                    "scope": "agent",
                    "client_id": "codex-main",
                },
            )
    finally:
        await client.close()

    assert result.isError is True
    assert api.requests == []


@pytest.mark.asyncio
async def test_a_missing_required_argument_is_refused_by_the_schema() -> None:
    api = RecordedApi()
    context, client = await session(api)

    try:
        async with context as connected:
            result = await connected.call_tool("search_context", {"query": "x"})
    finally:
        await client.close()

    assert result.isError is True
    assert "project_id" in text(result)
    assert api.requests == []


@pytest.mark.asyncio
async def test_an_unknown_tool_is_answered_not_crashed() -> None:
    context, client = await session(RecordedApi())

    try:
        async with context as connected:
            result = await connected.call_tool("run_shell", {"command": "id"})
    finally:
        await client.close()

    assert result.isError is True
    assert "no such tool" in text(result)


@pytest.mark.asyncio
async def test_an_unauthorized_call_is_refused_over_the_protocol() -> None:
    api = RecordedApi()
    context, client = await session(api, build_authorization(["search:read"]))

    try:
        async with context as connected:
            result = await connected.call_tool("list_projects", {})
    finally:
        await client.close()

    assert result.isError is True
    assert "projects:read" in text(result)
    assert api.requests == []


@pytest.mark.asyncio
async def test_a_project_outside_the_grant_is_refused_over_the_protocol() -> None:
    api = RecordedApi()
    context, client = await session(api, build_authorization(projects=[PROJECT]))

    try:
        async with context as connected:
            result = await connected.call_tool(
                "search_context",
                {"project_id": OTHER_PROJECT, "query": "x"},
            )
    finally:
        await client.close()

    assert result.isError is True
    assert "not authorized for that project" in text(result)
    assert api.requests == []


@pytest.mark.asyncio
async def test_an_upstream_failure_reaches_the_client_as_a_tool_error() -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v1/projects/absent",
        {"detail": "project was not found"},
        status_code=404,
    )
    context, client = await session(api)

    try:
        async with context as connected:
            result = await connected.call_tool("get_project", {"project_id": "absent"})
    finally:
        await client.close()

    # A failure is an answer, not a protocol error: the session stays usable.
    assert result.isError is True
    assert "project was not found" in text(result)


@pytest.mark.asyncio
async def test_the_session_survives_a_failed_call_and_answers_the_next_one() -> None:
    api = RecordedApi()
    api.respond("GET", "/api/v1/projects", {"items": [project_record()], "next_cursor": None})
    context, client = await session(api)

    try:
        async with context as connected:
            failed = await connected.call_tool("get_project", {"project_id": "absent"})
            succeeded = await connected.call_tool("list_projects", {})
    finally:
        await client.close()

    assert failed.isError is True
    assert succeeded.isError is False


@pytest.mark.asyncio
async def test_structured_content_is_json_serializable_for_any_client() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v1/search", search_body([search_result()]))
    context, client = await session(api)

    try:
        async with context as connected:
            result = await connected.call_tool("search_knowledge", {"query": "x"})
    finally:
        await client.close()

    assert json.loads(json.dumps(result.structuredContent)) == result.structuredContent


@pytest.mark.asyncio
async def test_a_read_only_tool_says_so_to_the_client() -> None:
    context, client = await session(RecordedApi())

    try:
        async with context as connected:
            listed = await connected.list_tools()
    finally:
        await client.close()

    by_name = {tool.name: tool for tool in listed.tools}
    assert by_name["search_knowledge"].annotations is not None
    assert by_name["search_knowledge"].annotations.readOnlyHint is True
    assert by_name["save_memory"].annotations is not None
    assert by_name["save_memory"].annotations.readOnlyHint is False


@pytest.mark.asyncio
async def test_the_server_states_what_it_holds_when_a_client_connects() -> None:
    context, client = await session(RecordedApi())

    try:
        async with context as connected:
            assert isinstance(connected, ClientSession)
            listed = await connected.list_tools()
    finally:
        await client.close()

    assert listed.tools
