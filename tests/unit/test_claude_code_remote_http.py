"""Remote Streamable HTTP MCP coverage for the Claude Code integration."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.middleware import RequestSizeLimitMiddleware
from app.api.routes.gateway import router as gateway_router
from app.mcp.auth import Authorization
from app.mcp.remote_http import RemoteMcpGateway, add_remote_mcp_route
from app.mcp.remote_security import RemoteMcpTransportPolicy
from app.models.agent import AgentActivityDocument, AgentClientDocument, AgentSessionDocument
from app.repositories.errors import ConcurrencyConflictError
from app.services.agent_activity_service import AgentActivityService
from app.services.agent_session_service import AgentSessionService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from tests.mcp_doubles import context_record, memory_record, search_body, search_result
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROJECT = "sampleco-payment"
PROTOCOL = "2025-06-18"


class Clients:
    def __init__(self, *items: AgentClientDocument) -> None:
        self.items = {item.client_id: item for item in items}

    async def get(self, client_id: str) -> AgentClientDocument | None:
        return self.items.get(client_id)

    async def touch(self, client_id: str, seen_at: datetime) -> None:
        _ = client_id, seen_at


class Activities:
    def __init__(self) -> None:
        self.items: list[AgentActivityDocument] = []

    async def create(self, document: AgentActivityDocument) -> AgentActivityDocument:
        self.items.append(document)
        return document


class Sessions:
    """Minimal revision-aware session store for the remote MCP contract."""

    def __init__(self) -> None:
        self.items: dict[str, AgentSessionDocument] = {}

    async def create(self, document: AgentSessionDocument) -> AgentSessionDocument:
        self.items[document.session_id] = document
        return document

    async def get(self, session_id: str) -> AgentSessionDocument | None:
        return self.items.get(session_id)

    async def update(
        self, document: AgentSessionDocument, *, expected_revision: int
    ) -> AgentSessionDocument:
        current = self.items.get(document.session_id)
        if current is None or current.revision != expected_revision:
            raise ConcurrencyConflictError("stale session")
        stored = document.model_copy(update={"revision": expected_revision + 1})
        self.items[document.session_id] = stored
        return stored

    async def touch_active(self, session_id: str, client_id: str, seen_at: datetime) -> bool:
        current = self.items.get(session_id)
        if current is None or current.client_id != client_id or current.status != "active":
            return False
        self.items[session_id] = current.model_copy(
            update={"last_seen_at": max(current.last_seen_at, seen_at)}
        )
        return True


def client(
    *,
    scopes: list[str],
    status: str = "active",
    client_id: str = "claude-code-main",
    client_type: str = "claude_code",
    name: str = "Claude Code",
    project_ids: tuple[str, ...] = (PROJECT,),
) -> AgentClientDocument:
    return AgentClientDocument(
        client_id=client_id,
        client_type=client_type,
        name=name,
        status=status,
        owner_id="owner",
        scopes=scopes,
        project_ids=list(project_ids),
    )


@asynccontextmanager
async def remote_application(
    *,
    scopes: tuple[str, ...] = ("search:read", "context:write", "agent:self", "session:write"),
    status: str = "active",
    session_limit: int = 100,
    client_id: str = "claude-code-main",
    client_type: str = "claude_code",
    client_name: str = "Claude Code",
    project_ids: tuple[str, ...] = (PROJECT,),
    max_request_bytes: int | None = None,
) -> AsyncIterator[tuple[FastAPI, str, Activities, list[dict[str, Any]]]]:
    """Run the real Streamable HTTP transport over a minimal in-memory API."""
    record, token = issued_key(scopes=scopes, projects=project_ids)
    record = record.model_copy(update={"client_id": client_id, "status": status})
    activities = Activities()
    writes: list[dict[str, Any]] = []
    application = FastAPI()
    clients = Clients(
        client(
            scopes=list(scopes),
            status=status,
            client_id=client_id,
            client_type=client_type,
            name=client_name,
            project_ids=project_ids,
        )
    )
    keys = FakeApiKeyRepository(record)
    application.state.auth = AuthService(keys, clients)
    application.state.test_agent_clients = clients
    application.state.test_api_keys = keys
    application.state.audit = AuditService(FakeAuditRepository())
    application.state.agent_activity_service = AgentActivityService(activities)
    application.state.agent_session_service = AgentSessionService(Sessions())
    application.include_router(gateway_router)
    if max_request_bytes is not None:
        application.add_middleware(RequestSizeLimitMiddleware, maximum_bytes=max_request_bytes)

    @application.post("/api/v1/search")
    async def search() -> dict[str, object]:
        return search_body([search_result(project_id=PROJECT)], project_id=PROJECT)

    @application.post("/api/v1/context")
    async def save_context(body: dict[str, Any]) -> dict[str, object]:
        writes.append(body)
        return context_record(PROJECT, title=str(body["title"]), content=str(body["content"]))

    @application.post("/api/v1/memory")
    async def save_memory(body: dict[str, Any]) -> dict[str, object]:
        project_id = body.get("project_id")
        return memory_record(project_id=project_id if isinstance(project_id, str) else None)

    @application.post("/api/v3/knowledge-candidates")
    async def propose_knowledge_candidate(body: dict[str, Any]) -> dict[str, object]:
        target_project_id = body.get("target_project_id")
        return {
            "research_project_id": body["research_project_id"],
            "candidate_id": body["candidate_id"],
            "candidate_type": body["candidate_type"],
            "target_project_id": target_project_id,
            "target_skill_id": None,
            "current_skill_version": None,
            "title": body["title"],
            "content": body["content"],
            "source_claim_ids": body["source_claim_ids"],
            "source_citation_ids": body.get("source_citation_ids", []),
            "confidence": body.get("confidence"),
            "confidence_rationale": body.get("confidence_rationale"),
            "status": "pending_review",
            "reviewer": None,
            "reviewed_at": None,
            "review_notes": None,
            "authorization": {
                "project_ids": [] if target_project_id is None else [target_project_id]
            },
            "revision": 1,
            "created_at": "2026-10-01T00:00:00Z",
            "updated_at": "2026-10-01T00:00:00Z",
        }

    remote = RemoteMcpGateway()
    add_remote_mcp_route(application, remote)
    async with remote.run(
        application,
        authorization=Authorization(),
        server_name="agent-context-platform",
        transport_policy=RemoteMcpTransportPolicy(max_sessions=session_limit),
    ):
        yield application, token, activities, writes


def headers(token: str, session_id: str | None = None) -> dict[str, str]:
    """Return the HTTP headers required by the Streamable HTTP transport."""
    result = {
        **bearer(token),
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id is not None:
        result["Mcp-Session-Id"] = session_id
        result["Mcp-Protocol-Version"] = PROTOCOL
    return result


async def initialize(
    http: AsyncClient,
    token: str,
    *,
    client_name: str = "claude-code-test",
) -> str:
    """Open a stateful MCP HTTP session and return its SDK-issued ID."""
    response = await http.post(
        "/mcp",
        headers=headers(token),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": "1"},
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "agent-context-platform"
    session_id = response.headers.get("mcp-session-id")
    assert session_id is not None
    return session_id


async def rpc(
    http: AsyncClient,
    token: str,
    session_id: str,
    method: str,
    params: dict[str, Any],
    request_id: int,
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Perform one JSON-RPC request using a live remote MCP session."""
    request_headers = headers(token, session_id)
    if extra_headers is not None:
        request_headers.update(extra_headers)
    response = await http.post(
        "/mcp",
        headers=request_headers,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


@pytest.mark.asyncio
async def test_remote_http_bearer_authentication_and_tool_discovery() -> None:
    async with remote_application() as (application, token, _, _):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            denied = await http.post("/mcp", headers=headers("not-a-real-token"), json={})
            session_id = await initialize(http, token)
            tools = await rpc(http, token, session_id, "tools/list", {}, 2)

    assert denied.status_code == 401
    assert denied.json() == {
        "error": {"code": "UNAUTHENTICATED", "message": "the credential was not accepted"}
    }
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert {"get_platform_capabilities", "search_knowledge", "save_context"} <= names


@pytest.mark.asyncio
async def test_remote_http_returns_claude_capabilities_in_the_stable_envelope() -> None:
    async with remote_application() as (application, token, _, _):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            session_id = await initialize(http, token)
            capabilities = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "get_platform_capabilities", "arguments": {}},
                2,
            )

    result = capabilities["result"]
    assert result["isError"] is False
    assert set(result["structuredContent"]["data"]["caller"]["scopes"]) == {
        "search:read",
        "context:write",
        "agent:self",
        "session:write",
    }
    assert result["structuredContent"]["meta"]["client_id"] == "claude-code-main"


@pytest.mark.asyncio
async def test_claude_remote_http_reads_and_writes_with_client_provenance() -> None:
    async with remote_application() as (application, token, activities, writes):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            session_id = await initialize(http, token)
            searched = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "search_knowledge", "arguments": {"query": "idempotency"}},
                2,
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
                        "title": "Claude remote MCP",
                        "content": "The request body must not enter activity logs.",
                        "importance": 0.8,
                    },
                },
                3,
            )

    assert searched["result"]["isError"] is False
    assert saved["result"]["isError"] is False
    assert writes[0]["title"] == "Claude remote MCP"
    assert [(event.client_id, event.client_type, event.tool) for event in activities.items] == [
        ("claude-code-main", "claude_code", "search_knowledge"),
        ("claude-code-main", "claude_code", "save_context"),
    ]


@pytest.mark.asyncio
async def test_remote_http_session_correlation_excludes_conversations() -> None:
    async with remote_application() as (application, token, activities, writes):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            transport_session = await initialize(http, token)
            begun = await rpc(
                http,
                token,
                transport_session,
                "tools/call",
                {
                    "name": "begin_agent_session",
                    "arguments": {
                        "project_id": PROJECT,
                        "repository": "git@github.com:example/payment-service.git",
                        "metadata": {"workspace": "payment-service"},
                    },
                },
                2,
            )
            session = begun["result"]["structuredContent"]["data"]["session"]
            session_id = session["session_id"]
            searched = await rpc(
                http,
                token,
                transport_session,
                "tools/call",
                {"name": "search_knowledge", "arguments": {"query": "session correlation"}},
                3,
            )
            saved = await rpc(
                http,
                token,
                transport_session,
                "tools/call",
                {
                    "name": "save_context",
                    "arguments": {
                        "project_id": PROJECT,
                        "type": "architecture_decision",
                        "title": "Claude session fixture",
                        "content": "This durable write has session-correlated activity.",
                        "importance": 0.8,
                    },
                },
                4,
            )
            ended = await rpc(
                http,
                token,
                transport_session,
                "tools/call",
                {
                    "name": "end_agent_session",
                    "arguments": {
                        "session_id": session_id,
                        "expected_revision": session["revision"],
                    },
                },
                5,
            )

    assert begun["result"]["isError"] is False
    assert searched["result"]["isError"] is False
    assert saved["result"]["isError"] is False
    assert ended["result"]["isError"] is False
    assert writes and writes[0]["title"] == "Claude session fixture"
    assert {event.tool for event in activities.items} >= {
        "begin_agent_session",
        "search_knowledge",
        "save_context",
        "end_agent_session",
    }
    assert all(event.session_id == session_id for event in activities.items)
    assert "conversation" not in session
    assert {"messages", "prompt", "transcript"}.isdisjoint(session["metadata"])


@pytest.mark.asyncio
async def test_remote_http_enforces_claude_scopes_and_revocation() -> None:
    async with remote_application(scopes=("search:read",)) as (application, token, _, _):
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
                        "title": "Not authorized",
                        "content": "This must not be written.",
                        "importance": 0.8,
                    },
                },
                2,
            )

    assert refused["result"]["isError"] is True
    assert refused["result"]["structuredContent"] == {
        "error": {
            "code": "FORBIDDEN",
            "message": "this client is not authorized for context:write",
        }
    }


@pytest.mark.asyncio
async def test_remote_http_does_not_allow_a_claude_client_to_bypass_project_grants() -> None:
    async with remote_application() as (application, token, _, writes):
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
                        "project_id": "other-private-project",
                        "type": "architecture_decision",
                        "title": "Forbidden write",
                        "content": "This must never reach the API.",
                        "importance": 0.8,
                    },
                },
                2,
            )

    assert refused["result"]["isError"] is True
    assert refused["result"]["structuredContent"]["error"]["code"] == "PROJECT_FORBIDDEN"
    assert writes == []


@pytest.mark.asyncio
async def test_revoked_remote_credential_is_denied_before_the_session_starts() -> None:
    async with remote_application(status="revoked") as (application, token, _, _):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            response = await http.post("/mcp", headers=headers(token), json={})

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "UNAUTHENTICATED", "message": "the credential was not accepted"}
    }


@pytest.mark.asyncio
async def test_remote_http_rejects_forged_proto_on_non_loopback() -> None:
    async with remote_application() as (application, token, _, _):
        async with AsyncClient(
            transport=ASGITransport(app=application, client=("198.51.100.10", 43210)),
            base_url="http://test",
        ) as http:
            refused = await http.post(
                "/mcp",
                headers={**headers(token), "X-Forwarded-Proto": "https"},
                json={},
            )
        async with AsyncClient(
            transport=ASGITransport(app=application, client=("127.0.0.1", 43210)),
            base_url="http://test",
        ) as local:
            session_id = await initialize(local, token)

    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "FORBIDDEN"
    assert session_id


@pytest.mark.asyncio
async def test_remote_session_limit_and_client_revocation_apply_to_live_connections() -> None:
    async with remote_application(session_limit=1) as (application, token, _, _):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            session_id = await initialize(http, token)
            capacity = await http.post(
                "/mcp",
                headers=headers(token),
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL,
                        "capabilities": {},
                        "clientInfo": {"name": "claude-code-test", "version": "1"},
                    },
                },
            )
            clients: Clients = application.state.test_agent_clients
            clients.items["claude-code-main"] = client(
                scopes=["search:read", "context:write", "agent:self", "session:write"],
                status="revoked",
            )
            revoked = await http.post(
                "/mcp",
                headers=headers(token, session_id),
                json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
            )

    assert capacity.status_code == 429
    assert capacity.json()["error"]["code"] == "RATE_LIMITED"
    assert revoked.status_code == 403
    assert revoked.json()["error"]["code"] == "CLIENT_DISABLED"
