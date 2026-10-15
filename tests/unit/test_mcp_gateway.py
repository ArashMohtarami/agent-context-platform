"""Client-aware MCP gateway behavior without changing the tool surface."""

from typing import Any

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from app.mcp.auth import Authorization
from app.mcp.middleware.authentication import GatewayAuthenticationMiddleware
from app.mcp.server import build_server, dispatch
from tests.mcp_doubles import RecordedApi, api_client, context_record, search_body, search_result

PROJECT = "sampleco-payment"
OTHER_PROJECT = "sampleco-wallet"


def identity(
    *,
    client_id: str | None = "codex-main",
    client_type: str | None = "codex",
    scopes: list[str] | None = None,
    projects: list[str] | None = None,
) -> dict[str, object]:
    """Build the narrow response supplied by the API identity preflight."""
    return {
        "org_id": "org-test",
        "key_id": "a" * 16,
        "allowed_profile_ids": None,
        "client_id": client_id,
        "client_type": client_type,
        "scopes": scopes or ["search:read", "context:read", "memory:write"],
        "allowed_project_ids": projects,
    }


async def run(
    api: RecordedApi,
    name: str,
    arguments: dict[str, Any],
    *,
    authorization: Authorization | None = None,
) -> Any:
    client = api_client(api, token="never-logged")
    try:
        return await dispatch(
            name,
            arguments,
            client=client,
            authorization=authorization or Authorization(),
            gateway=GatewayAuthenticationMiddleware(client, session_id="session-1"),
        )
    finally:
        await client.close()


def error_code(result: Any) -> str:
    assert result.isError is True
    assert result.structuredContent is not None
    return str(result.structuredContent["error"]["code"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_id", "client_type"),
    [
        ("codex-main", "codex"),
        ("claude-main", "claude_code"),
        ("gemini-cli-main", "gemini_cli"),
    ],
)
async def test_registered_clients_use_the_same_search_knowledge_tool(
    client_id: str, client_type: str
) -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v4/gateway/identity",
        identity(client_id=client_id, client_type=client_type),
    )
    api.respond("POST", "/api/v1/search", search_body([search_result()], project_id=PROJECT))
    api.respond("POST", "/api/v4/gateway/activity", {}, status_code=204)

    result = await run(api, "search_knowledge", {"query": "idempotency", "project_id": PROJECT})

    assert result.isError is False
    assert api.paths == [
        "/api/v4/gateway/identity",
        "/api/v1/search",
        "/api/v4/gateway/activity",
    ]
    request = api.requests[1]
    assert request.headers["x-agent-context-client-id"] == client_id
    assert request.headers["x-agent-context-client-type"] == client_type
    assert request.headers["x-agent-context-session-id"] == "session-1"
    assert request.headers["x-request-id"] == request.headers["x-agent-context-request-id"]
    event = api.body(2)
    assert event == {
        "operation": "search_knowledge",
        "tool": "search_knowledge",
        "project_id": PROJECT,
        "resource_type": None,
        "resource_id": None,
        "status": "succeeded",
        "latency_ms": pytest.approx(event["latency_ms"]),
    }
    assert event["latency_ms"] >= 0.0
    assert "query" not in event


@pytest.mark.asyncio
async def test_codex_claude_and_gemini_receive_the_same_capabilities_for_equal_grants() -> None:
    results: list[dict[str, object]] = []
    for client_id, client_type in (
        ("codex-main", "codex"),
        ("claude-main", "claude_code"),
        ("gemini-cli-main", "gemini_cli"),
    ):
        api = RecordedApi()
        api.respond(
            "GET",
            "/api/v4/gateway/identity",
            identity(
                client_id=client_id,
                client_type=client_type,
                scopes=["agent:self", "search:read"],
                projects=[PROJECT],
            ),
        )
        api.respond("POST", "/api/v4/gateway/activity", {}, status_code=204)

        result = await run(api, "get_platform_capabilities", {})

        assert result.isError is False
        assert result.structuredContent is not None
        assert result.structuredContent["meta"] == {
            "request_id": api.requests[0].headers["x-request-id"],
            "project_id": None,
            "client_id": client_id,
            "token_count": 0,
        }
        assert api.paths == ["/api/v4/gateway/identity", "/api/v4/gateway/activity"]
        results.append(result.structuredContent["data"])

    assert results[0] == results[1] == results[2]
    assert results[0]["caller"] == {
        "scopes": ["search:read", "agent:self"],
        "project_access": {"mode": "allowlist", "project_ids": [PROJECT]},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_id", "client_type"),
    [("codex-main", "codex"), ("claude-main", "claude_code")],
)
async def test_registered_clients_use_the_same_resolve_project_tool(
    client_id: str, client_type: str
) -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v4/gateway/identity",
        identity(
            client_id=client_id,
            client_type=client_type,
            scopes=["projects:read"],
            projects=[PROJECT],
        ),
    )
    api.respond(
        "POST",
        "/api/v4/project-bindings/resolve",
        {
            "status": "resolved",
            "project_id": PROJECT,
            "confidence": 1.0,
            "matched_by": "git_remote",
            "candidates": [],
        },
    )
    api.respond("POST", "/api/v4/gateway/activity", {}, status_code=204)

    result = await run(
        api,
        "resolve_project",
        {"git_remote": "git@github.com:example/payment-service.git"},
    )

    assert result.isError is False
    assert api.paths == [
        "/api/v4/gateway/identity",
        "/api/v4/project-bindings/resolve",
        "/api/v4/gateway/activity",
    ]
    assert api.requests[1].headers["x-agent-context-client-id"] == client_id
    event = api.body(2)
    assert event["tool"] == "resolve_project"
    assert event["project_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["UNAUTHENTICATED", "CREDENTIAL_EXPIRED", "CLIENT_DISABLED"])
async def test_identity_failures_are_stable_gateway_errors(code: str) -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v4/gateway/identity",
        {"detail": {"code": code}},
        status_code=403 if code == "CLIENT_DISABLED" else 401,
    )

    result = await run(api, "search_knowledge", {"query": "idempotency"})

    assert error_code(result) == code
    assert api.paths == ["/api/v4/gateway/identity"]


@pytest.mark.asyncio
async def test_a_revoked_credential_is_indistinguishable_from_an_invalid_one() -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v4/gateway/identity",
        {"detail": {"code": "UNAUTHENTICATED"}},
        status_code=401,
    )

    result = await run(api, "search_knowledge", {"query": "idempotency"})

    assert error_code(result) == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_gateway_identity_timeout_uses_the_stable_timeout_error() -> None:
    api = RecordedApi(error=httpx.ReadTimeout("identity deadline reached"))

    result = await run(api, "search_knowledge", {"query": "idempotency"})

    assert result.structuredContent == {
        "error": {
            "code": "OPERATION_TIMEOUT",
            "message": "the platform did not answer before the request deadline",
        }
    }
    assert api.paths == ["/api/v4/gateway/identity"]


@pytest.mark.asyncio
async def test_a_legacy_v1_key_remains_usable_through_the_gateway() -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v4/gateway/identity",
        identity(client_id=None, client_type=None),
    )
    api.respond("POST", "/api/v1/search", search_body([search_result()]))

    result = await run(api, "search_knowledge", {"query": "idempotency"})

    assert result.isError is False
    assert api.paths == ["/api/v4/gateway/identity", "/api/v1/search"]
    assert "x-agent-context-client-id" not in api.requests[1].headers


@pytest.mark.asyncio
async def test_client_scope_is_refused_before_a_tool_request() -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v4/gateway/identity",
        identity(scopes=["search:read"]),
    )
    api.respond("POST", "/api/v4/gateway/activity", {}, status_code=204)

    result = await run(
        api,
        "save_memory",
        {
            "type": "preference",
            "content": "a prompt body that must not become activity metadata",
            "importance": 0.5,
            "confidence": 0.5,
        },
    )

    assert error_code(result) == "FORBIDDEN"
    assert api.paths == ["/api/v4/gateway/identity", "/api/v4/gateway/activity"]
    assert api.body(1)["status"] == "denied"
    assert "content" not in api.body(1)


@pytest.mark.asyncio
async def test_client_project_allowlist_is_refused_before_a_tool_request() -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v4/gateway/identity",
        identity(projects=[PROJECT]),
    )
    api.respond("POST", "/api/v4/gateway/activity", {}, status_code=204)

    result = await run(api, "search_context", {"project_id": OTHER_PROJECT, "query": "idempotency"})

    assert error_code(result) == "PROJECT_FORBIDDEN"
    assert api.paths == ["/api/v4/gateway/identity", "/api/v4/gateway/activity"]
    assert api.body(1)["project_id"] == OTHER_PROJECT


@pytest.mark.asyncio
async def test_context_write_activity_uses_only_the_response_resource_identifier() -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v4/gateway/identity",
        identity(scopes=["context:write"], projects=[PROJECT]),
    )
    api.respond("POST", "/api/v1/context", context_record(PROJECT), status_code=201)
    api.respond("POST", "/api/v4/gateway/activity", {}, status_code=204)

    result = await run(
        api,
        "save_context",
        {
            "project_id": PROJECT,
            "type": "architecture_decision",
            "title": "Payment retries",
            "content": "A request body must not be retained as activity metadata.",
            "importance": 0.9,
            "source": "mcp",
        },
    )

    assert result.isError is False
    event = api.body(2)
    assert event["resource_type"] == "context"
    assert event["resource_id"] == "6720a1b2c3d4e5f60718293a"
    assert "content" not in event


@pytest.mark.asyncio
async def test_session_lifecycle_binds_activity_and_subsequent_tools_to_one_client_session() -> (
    None
):
    """The shared gateway, not the caller, carries the server-created ID."""
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v4/gateway/identity",
        identity(scopes=["agent:self", "session:write", "search:read"], projects=[PROJECT]),
    )
    session_record = {
        "session_id": "claude-session-1",
        "client_id": "codex-main",
        "client_type": "codex",
        "project_id": PROJECT,
        "repository": "git@github.com:example/payment-service.git",
        "started_at": "2026-10-08T09:00:00Z",
        "last_seen_at": "2026-10-08T09:00:00Z",
        "ended_at": None,
        "status": "active",
        "metadata": {"workspace": "payments"},
        "revision": 1,
        "created_at": "2026-10-08T09:00:00Z",
        "updated_at": "2026-10-08T09:00:00Z",
    }
    api.respond("POST", "/api/v4/gateway/sessions", session_record, status_code=201)
    api.respond(
        "POST",
        "/api/v1/search",
        search_body([search_result()], project_id=PROJECT),
    )
    api.respond(
        "POST",
        "/api/v4/gateway/sessions/claude-session-1/end",
        {**session_record, "ended_at": "2026-10-08T09:01:00Z", "status": "ended", "revision": 2},
    )
    api.respond("POST", "/api/v4/gateway/activity", {}, status_code=204)
    client = api_client(api, token="never-logged")
    gateway = GatewayAuthenticationMiddleware(client)
    try:
        begun = await dispatch(
            "begin_agent_session",
            {
                "project_id": PROJECT,
                "repository": "git@github.com:example/payment-service.git",
                "metadata": {"workspace": "payments"},
            },
            client=client,
            authorization=Authorization(),
            gateway=gateway,
        )
        searched = await dispatch(
            "search_knowledge",
            {"query": "idempotency", "project_id": PROJECT},
            client=client,
            authorization=Authorization(),
            gateway=gateway,
        )
        ended = await dispatch(
            "end_agent_session",
            {"session_id": "claude-session-1", "expected_revision": 1},
            client=client,
            authorization=Authorization(),
            gateway=gateway,
        )
    finally:
        await client.close()

    assert begun.isError is False
    assert searched.isError is False
    assert ended.isError is False
    assert gateway.session_id is None
    # begin and end activity are correlated too; no tool body appears in the
    # audit payload and every interior request carries the same protected ID.
    assert api.requests[0].headers.get("x-agent-context-session-id") is None
    assert api.requests[1].headers.get("x-agent-context-session-id") is None
    assert all(
        request.headers.get("x-agent-context-session-id") == "claude-session-1"
        for request in api.requests[2:]
    )
    activity_events = [
        api.body(index)
        for index, request in enumerate(api.requests)
        if request.url.path == "/api/v4/gateway/activity"
    ]
    assert [event["tool"] for event in activity_events] == [
        "begin_agent_session",
        "search_knowledge",
        "end_agent_session",
    ]
    assert all(event["resource_type"] in {None, "session"} for event in activity_events)


@pytest.mark.asyncio
async def test_the_gateway_works_through_a_raw_mcp_client_fixture() -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v4/gateway/identity",
        identity(client_id="claude-main", client_type="claude_code"),
    )
    api.respond("POST", "/api/v1/search", search_body([search_result()], project_id=PROJECT))
    api.respond("POST", "/api/v4/gateway/activity", {}, status_code=204)
    client = api_client(api)
    server = build_server(
        client,
        Authorization(),
        gateway=GatewayAuthenticationMiddleware(client, session_id="claude-session"),
    )

    try:
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "search_knowledge", {"query": "idempotency", "project_id": PROJECT}
            )
    finally:
        await client.close()

    assert result.isError is False
    assert api.requests[1].headers["x-agent-context-client-id"] == "claude-main"
    assert api.body(2)["tool"] == "search_knowledge"
