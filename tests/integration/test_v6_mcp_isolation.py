"""Real REST, stdio and HTTP MCP paths use the same verified tenant."""

import asyncio
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from v6_isolation_fixtures import token

from app.api.routes.agents import router as clients_router
from app.api.routes.client_profiles import router as profiles_router
from app.api.routes.content import router as content_router
from app.api.routes.context import router as context_router
from app.api.routes.creator import router as creator_router
from app.api.routes.gateway import router as gateway_router
from app.api.routes.projects import router as projects_router
from app.api.routes.research import router as research_router
from app.api.routes.skills import router as skills_router
from app.core.config import Settings
from app.mcp.auth import Authorization
from app.mcp.remote_http import RemoteMcpGateway, add_remote_mcp_route
from app.repositories.agent_repository import (
    MongoAgentActivityRepository,
    MongoAgentClientRepository,
    MongoAgentSessionRepository,
)
from app.repositories.api_key_repository import MongoApiKeyRepository
from app.repositories.client_profile_repository import MongoClientProfileRepository
from app.repositories.credential_repository import MongoCredentialResolver
from app.security.tenant_maintenance import TenantMaintenanceMiddleware
from app.services.agent_activity_service import AgentActivityService
from app.services.agent_client_service import AgentClientService
from app.services.agent_session_service import AgentSessionService
from app.services.api_key_service import ApiKeyService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.client_profile_service import ClientProfileService

pytestmark = pytest.mark.integration


def application(db):
    app = FastAPI()
    for router in (
        gateway_router,
        skills_router,
        projects_router,
        context_router,
        profiles_router,
        clients_router,
        content_router,
        creator_router,
        research_router,
    ):
        app.include_router(router)
    app.add_middleware(TenantMaintenanceMiddleware)
    app.state.tenant_control_database = db
    app.state.mongodb = SimpleNamespace(database=db)
    app.state.embeddings = None
    app.state.settings = Settings(
        _env_file=None,
        mongodb_uri="mongodb://127.0.0.1:1",
        mongodb_database=db.name,
        mongodb_username="unused",
        mongodb_password="unused-fixture-config",
    )
    app.state.auth = AuthService(MongoCredentialResolver(db), MongoAgentClientRepository(db))
    app.state.audit = AuditService(None)
    app.state.agent_session_service = AgentSessionService(MongoAgentSessionRepository(db))
    app.state.agent_activity_service = AgentActivityService(MongoAgentActivityRepository(db))
    app.state.agent_client_service = AgentClientService(
        MongoAgentClientRepository(db), ApiKeyService(MongoApiKeyRepository(db))
    )
    app.state.client_profile_service = ClientProfileService(
        MongoClientProfileRepository(db), MongoAgentClientRepository(db)
    )
    return app


async def test_rest_v1_v4_v5_and_spoofed_headers(isolated_database):
    app = application(isolated_database)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        for label in ("a", "b"):
            headers = {
                "Authorization": "Bearer " + token(label),
                "X-Org-ID": "org-b" if label == "a" else "org-a",
            }
            skill = await http.get("/api/v1/skills/retry-skill", headers=headers)
            assert skill.status_code == 200, skill.text
            assert "PRIVATE_SENTINEL_" + label.upper() in skill.text
            assert "PRIVATE_SENTINEL_" + ("B" if label == "a" else "A") not in skill.text
            for path in ("/api/v1/projects", "/api/v4/clients", "/api/v5/client-profiles"):
                response = await http.get(path, headers=headers)
                assert response.status_code == 200, (path, response.text)
        unauthenticated = await http.get("/api/v1/skills", headers={"X-Org-ID": "org-a"})
        assert unauthenticated.status_code == 401
        db = isolated_database
        foreign = await db["project_context"].find_one({"org_id": "org-b"})
        response = await http.get(
            "/api/v1/context/" + str(foreign["_id"]),
            headers={"Authorization": "Bearer " + token("a")},
        )
        assert response.status_code in (404, 422)


async def test_http_mcp_sessions_cannot_switch_org(isolated_database):
    app = application(isolated_database)
    remote = RemoteMcpGateway()
    add_remote_mcp_route(app, remote)
    async with remote.run(app, authorization=Authorization(), server_name="agent-context-platform"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as http:
            headers = {
                "Authorization": "Bearer " + token("a"),
                "Accept": "application/json, text/event-stream",
            }
            response = await http.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "phase2", "version": "1"},
                    },
                },
            )
            assert response.status_code == 200, response.text
            session = response.headers["mcp-session-id"]
            headers.update({"Mcp-Session-Id": session, "Mcp-Protocol-Version": "2025-06-18"})
            body = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_skill", "arguments": {"skill_id": "retry-skill"}},
            }
            result = await http.post("/mcp", headers=headers, json=body)
            assert result.status_code == 200, result.text
            assert "PRIVATE_SENTINEL_A" in result.text and "PRIVATE_SENTINEL_B" not in result.text
            headers["Authorization"] = "Bearer " + token("b")
            switched = await http.post("/mcp", headers=headers, json=body)
            assert switched.status_code in (401, 403, 404)
            assert "PRIVATE_SENTINEL" not in switched.text


@asynccontextmanager
async def listening(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        yield "http://127.0.0.1:" + str(port)
    finally:
        server.should_exit = True
        await task
        sock.close()


async def test_actual_stdio_mcp_uses_one_org_and_rechecks_revocation(isolated_database, tmp_path):
    app = application(isolated_database)
    async with listening(app) as url:
        for label in ("a", "b"):
            credential = tmp_path / ("key-" + label)
            credential.write_text(token(label))
            credential.chmod(0o600)
            parameters = StdioServerParameters(
                command=sys.executable,
                cwd=str(tmp_path),
                args=[
                    "-c",
                    "import asyncio; from app.mcp.server import run_stdio; "
                    "asyncio.run(run_stdio())",
                ],
                env={
                    "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                    "MCP_API_BASE_URL": url,
                    "MCP_API_TOKEN_FILE": str(credential),
                    "MCP_REQUEST_TIMEOUT_SECONDS": "10",
                },
            )
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("get_skill", {"skill_id": "retry-skill"})
                    assert result.isError is False
                    text = str(result.structuredContent)
                    assert "PRIVATE_SENTINEL_" + label.upper() in text
                    assert "PRIVATE_SENTINEL_" + ("B" if label == "a" else "A") not in text
                    await isolated_database["api_keys"].update_one(
                        {"org_id": "org-" + label}, {"$set": {"status": "revoked"}}
                    )
                    refused = await session.call_tool("get_skill", {"skill_id": "retry-skill"})
                    assert refused.isError and "PRIVATE_SENTINEL" not in str(refused)


async def test_rest_content_export_history_and_research_are_tenant_bound(isolated_database):
    db = isolated_database
    await db["content_items"].update_many({}, {"$set": {"status": "approved"}})
    for label in ("a", "b"):
        await db["content_versions"].update_many(
            {"org_id": "org-" + label}, {"$set": {"body": "VERSION_PRIVATE_" + label.upper()}}
        )
        await db["research_projects"].update_many(
            {"org_id": "org-" + label}, {"$set": {"question": "RESEARCH_PRIVATE_" + label.upper()}}
        )
    app = application(db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        for label in ("a", "b"):
            headers = {"Authorization": "Bearer " + token(label)}
            base = "/api/v2/content/payment-idempotency-linkedin-post"
            own = await http.get(base, params={"profile_id": "personal-brand"}, headers=headers)
            exported = await http.post(
                base + "/export",
                json={"profile_id": "personal-brand", "mode": "markdown"},
                headers=headers,
            )
            history = await http.get(
                base + "/versions", params={"profile_id": "personal-brand"}, headers=headers
            )
            research = await http.get(
                "/api/v3/research/projects/payment-retry-research", headers=headers
            )
            for response in (own, exported, history, research):
                assert response.status_code == 200, response.text
            assert "PRIVATE_SENTINEL_" + label.upper() in exported.text
            assert "VERSION_PRIVATE_" + label.upper() in history.text
            assert "RESEARCH_PRIVATE_" + label.upper() in research.text
            other = "B" if label == "a" else "A"
            assert "PRIVATE_SENTINEL_" + other not in exported.text
            assert "VERSION_PRIVATE_" + other not in history.text
            assert "RESEARCH_PRIVATE_" + other not in research.text
