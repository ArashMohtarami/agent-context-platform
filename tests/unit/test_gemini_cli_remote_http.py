"""Gemini-compatible Streamable HTTP coverage for the shared `/mcp` edge."""

import pytest
from httpx import ASGITransport, AsyncClient

from tests.unit.test_claude_code_remote_http import (
    PROJECT,
    headers,
    initialize,
    remote_application,
    rpc,
)

GEMINI_CLIENT_ID = "gemini-cli-main"


@pytest.mark.asyncio
async def test_gemini_remote_http_discovers_capabilities_reads_and_writes() -> None:
    async with remote_application(
        client_id=GEMINI_CLIENT_ID,
        client_type="gemini_cli",
        client_name="Gemini CLI",
    ) as (application, token, activities, writes):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            invalid = await http.post("/mcp", headers=headers("not-a-real-token"), json={})
            session_id = await initialize(http, token)
            tools = await rpc(http, token, session_id, "tools/list", {}, 2)
            capabilities = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "get_platform_capabilities", "arguments": {}},
                3,
            )
            searched = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "search_knowledge", "arguments": {"query": "Gemini MCP verification"}},
                4,
            )
            saved = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {
                    "name": "save_context",
                    "arguments": {
                        "project_id": PROJECT,
                        "type": "architecture_decision",
                        "title": "Gemini remote MCP",
                        "content": (
                            "This context was written through the shared Streamable HTTP gateway."
                        ),
                        "importance": 0.8,
                    },
                },
                5,
            )

    assert invalid.status_code == 401
    assert {tool["name"] for tool in tools["result"]["tools"]} >= {
        "get_platform_capabilities",
        "search_knowledge",
        "save_context",
    }
    assert capabilities["result"]["isError"] is False
    assert capabilities["result"]["structuredContent"]["meta"]["client_id"] == GEMINI_CLIENT_ID
    assert searched["result"]["isError"] is False
    assert saved["result"]["isError"] is False
    assert writes[0]["title"] == "Gemini remote MCP"
    assert {event.tool for event in activities.items} == {
        "get_platform_capabilities",
        "search_knowledge",
        "save_context",
    }
    assert {(event.client_id, event.client_type.value) for event in activities.items} == {
        (GEMINI_CLIENT_ID, "gemini_cli")
    }


@pytest.mark.asyncio
async def test_gemini_remote_http_enforces_scope_restrictions_and_revocation() -> None:
    async with remote_application(
        scopes=("search:read",),
        client_id=GEMINI_CLIENT_ID,
        client_type="gemini_cli",
        client_name="Gemini CLI",
    ) as (application, token, _, writes):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            session_id = await initialize(http, token)
            refused = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {
                    "name": "save_context",
                    "arguments": {
                        "project_id": PROJECT,
                        "type": "architecture_decision",
                        "title": "Forbidden Gemini write",
                        "content": "This must not be written.",
                        "importance": 0.8,
                    },
                },
                2,
            )

    assert refused["result"]["isError"] is True
    assert refused["result"]["structuredContent"]["error"]["code"] == "FORBIDDEN"
    assert writes == []

    async with remote_application(
        status="revoked",
        client_id=GEMINI_CLIENT_ID,
        client_type="gemini_cli",
        client_name="Gemini CLI",
    ) as (application, token, _, _):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            revoked = await http.post("/mcp", headers=headers(token), json={})

    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "UNAUTHENTICATED"
