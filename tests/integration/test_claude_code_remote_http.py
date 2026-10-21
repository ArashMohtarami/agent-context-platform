"""Live Streamable HTTP MCP coverage for the Claude Code client workflow."""

import asyncio
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import uvicorn
from bson import ObjectId
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from v6_legacy_integration import unified_search_ready

from app.core.config import get_settings
from app.main import create_app
from app.models.client_profile import GEMINI_DEFAULT_SCOPES
from app.models.project import ProjectDocument
from app.repositories.project_repository import MongoProjectRepository
from app.services.agent_client_service import AgentClientService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]


def _port() -> int:
    """Reserve a currently free loopback port for this test's temporary server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@asynccontextmanager
async def running_application(
    *,
    client_id: str | None = None,
    client_type: str = "claude_code",
    client_name: str = "Claude Code remote HTTP integration",
    scopes: list[str] | None = None,
) -> AsyncIterator[tuple[FastAPI, str, str, str, str]]:
    """Run the real API/MCP HTTP edge and clean up only this test's records."""
    # Unified knowledge search includes local query embedding. The disposable
    # runner provides the real model from its private offline cache.
    application = create_app(get_settings())
    port = _port()
    server = uvicorn.Server(
        uvicorn.Config(
            application, host="127.0.0.1", port=port, log_level="warning", access_log=False
        )
    )
    task = asyncio.create_task(server.serve())
    # A cold local embedding model can take several minutes to load while the
    # test container has no outbound network access. The live MCP assertion is
    # meaningful only after that normal application startup completes.
    for _ in range(3_600):
        if server.started:
            break
        if task.done():
            await task
        await asyncio.sleep(0.1)
    else:
        server.should_exit = True
        await task
        raise AssertionError("the temporary Context API did not start")

    suffix = str(ObjectId())[-8:]
    client_id = client_id or f"claude-code-main-{suffix}"
    project_id = f"claude-http-{suffix}"
    key_id: str | None = None
    try:
        await MongoProjectRepository(application.state.mongodb.database).create(
            ProjectDocument(
                project_id=project_id,
                name="Claude HTTP integration",
                description="A project created only for remote MCP integration coverage.",
                repository="https://example.invalid/claude-http.git",
                environment="test",
            )
        )
        await unified_search_ready(
            application.state.mongodb.database, get_settings().embedding_dimensions
        )
        clients: AgentClientService = application.state.agent_client_service
        created = await clients.create_client(
            client_id=client_id,
            client_type=client_type,
            name=client_name,
            owner_id="integration",
            scopes=scopes or ["search:read", "context:write", "agent:self"],
            project_ids=[project_id],
        )
        issued = await clients.create_credential(created.client_id)
        key_id = issued.record.key_id
        yield application, f"http://127.0.0.1:{port}/mcp", issued.token, project_id, client_id
    finally:
        database = application.state.mongodb.database
        await database["agent_activity"].delete_many({"client_id": client_id})
        await database["project_context"].delete_many({"project_id": project_id})
        await database["projects"].delete_many({"project_id": project_id})
        if key_id is not None:
            await database["api_keys"].delete_many({"key_id": key_id})
        await database["agent_clients"].delete_many({"client_id": client_id})
        server.should_exit = True
        await task


@pytest.mark.asyncio
async def test_claude_code_client_uses_remote_http_for_discovery_search_and_write() -> None:
    async with running_application() as (application, mcp_url, token, project_id, client_id):
        async with streamablehttp_client(
            mcp_url,
            headers={"Authorization": f"Bearer {token}"},
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                capabilities = await session.call_tool("get_platform_capabilities", {})
                searched = await session.call_tool(
                    "search_knowledge",
                    {
                        "query": "Claude Code remote HTTP verification",
                        "project_id": project_id,
                    },
                )
                saved = await session.call_tool(
                    "save_context",
                    {
                        "project_id": project_id,
                        "type": "architecture_decision",
                        "title": "Claude Code remote HTTP test",
                        "content": "This context was written through Streamable HTTP MCP.",
                        "importance": 0.8,
                        "external_id": f"claude-http-{project_id}",
                    },
                )

        events = [
            event
            async for event in application.state.mongodb.database["agent_activity"].find(
                {"client_id": client_id},
                {"_id": 0},
            )
        ]

    assert {tool.name for tool in tools.tools} >= {
        "get_platform_capabilities",
        "search_knowledge",
        "save_context",
    }
    assert capabilities.isError is False
    assert capabilities.structuredContent is not None
    assert capabilities.structuredContent["meta"]["client_id"] == client_id
    assert searched.isError is False
    assert saved.isError is False
    assert {event["tool"] for event in events} >= {"search_knowledge", "save_context"}


@pytest.mark.asyncio
async def test_gemini_cli_uses_the_same_streamable_http_client_and_mcp_tools() -> None:
    async with running_application(
        client_id="gemini-cli-main",
        client_type="gemini_cli",
        client_name="Gemini CLI remote HTTP integration",
        scopes=list(GEMINI_DEFAULT_SCOPES),
    ) as (application, mcp_url, token, project_id, client_id):
        async with streamablehttp_client(
            mcp_url,
            headers={"Authorization": f"Bearer {token}"},
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                capabilities = await session.call_tool("get_platform_capabilities", {})
                searched = await session.call_tool(
                    "search_knowledge",
                    {
                        "query": "Gemini remote HTTP verification",
                        "project_id": project_id,
                    },
                )
                saved = await session.call_tool(
                    "save_context",
                    {
                        "project_id": project_id,
                        "type": "architecture_decision",
                        "title": "Gemini CLI remote HTTP test",
                        "content": (
                            "This context was written through the shared Streamable HTTP MCP."
                        ),
                        "importance": 0.8,
                        "external_id": f"gemini-http-{project_id}",
                    },
                )

        events = [
            event
            async for event in application.state.mongodb.database["agent_activity"].find(
                {"client_id": client_id},
                {"_id": 0},
            )
        ]

    assert {tool.name for tool in tools.tools} >= {
        "get_platform_capabilities",
        "search_knowledge",
        "save_context",
    }
    assert capabilities.isError is False
    assert capabilities.structuredContent is not None
    assert capabilities.structuredContent["meta"]["client_id"] == "gemini-cli-main"
    assert searched.isError is False
    assert saved.isError is False
    assert {event["tool"] for event in events} >= {"search_knowledge", "save_context"}
