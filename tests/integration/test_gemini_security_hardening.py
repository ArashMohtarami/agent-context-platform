"""V5 Phase 7 live gateway checks for Gemini credential isolation."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from bson import ObjectId
from fastapi import FastAPI

from app.core.config import get_settings
from app.main import create_app
from app.models.agent import AgentClientType
from app.services.agent_client_service import AgentClientService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

PROTOCOL = "2025-06-18"
SCOPES = ("search:read", "projects:read", "agent:self")


@asynccontextmanager
async def security_application(
    run_id: str,
) -> AsyncIterator[tuple[FastAPI, AgentClientService, dict[str, str]]]:
    """Provision independent Gemini, Codex, and Claude clients on one gateway."""
    settings = get_settings().model_copy(update={"embedding_enabled": False})
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        database = application.state.mongodb.database
        clients: AgentClientService = application.state.agent_client_service
        client_ids = {
            "gemini": f"gemini-security-{run_id}",
            "codex": f"codex-security-{run_id}",
            "claude": f"claude-security-{run_id}",
        }
        credentials: dict[str, str] = {}
        try:
            for name, client_type in (
                ("gemini", AgentClientType.GEMINI_CLI),
                ("codex", AgentClientType.CODEX),
                ("claude", AgentClientType.CLAUDE_CODE),
            ):
                created = await clients.create_client(
                    client_id=client_ids[name],
                    client_type=client_type,
                    name=f"V5 security {name}",
                    owner_id="v5-security",
                    scopes=SCOPES,
                )
                issued = await clients.create_credential(created.client_id, expires_in_days=1)
                credentials[name] = issued.token
                stored = await database["api_keys"].find_one({"key_id": issued.record.key_id})
                assert stored is not None
                assert issued.token not in str(stored)
                assert stored["key_hash"] != issued.token
            yield (
                application,
                clients,
                {
                    "gemini_client": client_ids["gemini"],
                    "codex_client": client_ids["codex"],
                    "claude_client": client_ids["claude"],
                    "gemini_token": credentials["gemini"],
                    "codex_token": credentials["codex"],
                    "claude_token": credentials["claude"],
                },
            )
        finally:
            for collection in (
                "agent_activity",
                "agent_sessions",
                "gateway_idempotency",
                "retrieval_logs",
                "api_keys",
                "agent_clients",
            ):
                await database[collection].delete_many(
                    {"client_id": {"$in": tuple(client_ids.values())}}
                )


def _headers(token: str, session_id: str | None = None) -> dict[str, str]:
    values = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id is not None:
        values["Mcp-Session-Id"] = session_id
        values["Mcp-Protocol-Version"] = PROTOCOL
    return values


async def _initialize(http: httpx.AsyncClient, token: str, request_id: int) -> str:
    response = await http.post(
        "/mcp",
        headers=_headers(token),
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "security-fixture", "version": "1"},
            },
        },
    )
    assert response.status_code == 200
    session_id = response.headers.get("mcp-session-id")
    assert session_id is not None
    return session_id


async def _tools_list(
    http: httpx.AsyncClient, token: str, session_id: str, request_id: int
) -> httpx.Response:
    return await http.post(
        "/mcp",
        headers=_headers(token, session_id),
        json={"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": {}},
    )


@pytest.mark.asyncio
async def test_revoking_gemini_denies_the_next_remote_mcp_call_without_affecting_peers() -> None:
    """V5 revocation is immediate and each client retains an independent key."""
    run_id = str(ObjectId())[-8:]
    async with security_application(run_id) as (application, clients, values):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="https://test"
        ) as http:
            gemini_session = await _initialize(http, values["gemini_token"], 1)
            await clients.revoke_client(values["gemini_client"])
            denied = await _tools_list(http, values["gemini_token"], gemini_session, 2)
            codex_session = await _initialize(http, values["codex_token"], 3)
            claude_session = await _initialize(http, values["claude_token"], 4)
            codex = await _tools_list(http, values["codex_token"], codex_session, 5)
            claude = await _tools_list(http, values["claude_token"], claude_session, 6)

    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "UNAUTHENTICATED"
    assert codex.status_code == 200
    assert claude.status_code == 200
    assert {tool["name"] for tool in codex.json()["result"]["tools"]} >= {
        "get_platform_capabilities",
        "search_knowledge",
    }
    assert {tool["name"] for tool in claude.json()["result"]["tools"]} >= {
        "get_platform_capabilities",
        "search_knowledge",
    }
