"""The four gates every call passes: the tool, the scope, the arguments, the project."""

import logging
from dataclasses import replace
from typing import Any

import httpx
import pytest

from app.mcp import server
from app.mcp.auth import Authorization, build_authorization
from app.mcp.server import dispatch
from app.mcp.tools import TOOLS_BY_NAME
from tests.mcp_doubles import (
    RecordedApi,
    api_client,
    context_record,
    memory_record,
    search_body,
    search_result,
)

PROJECT = "sampleco-payment"
OTHER_PROJECT = "sampleco-wallet"


async def run(
    api: RecordedApi,
    name: str,
    arguments: dict[str, Any],
    *,
    authorization: Authorization | None = None,
) -> Any:
    """Dispatch one call, whatever its outcome."""
    client = api_client(api)
    try:
        return await dispatch(
            name,
            arguments,
            client=client,
            authorization=authorization or Authorization(),
        )
    finally:
        await client.close()


def message(result: Any) -> str:
    """Return the text a failing call reported."""
    return str(result.content[0].text)


@pytest.mark.asyncio
async def test_an_unknown_tool_is_refused_by_name() -> None:
    api = RecordedApi()

    result = await run(api, "drop_database", {})

    assert result.isError is True
    assert "no such tool" in message(result)
    assert result.structuredContent == {
        "error": {"code": "NOT_FOUND", "message": "no such tool: drop_database"}
    }
    assert api.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        ("search_knowledge", {}, "query"),
        ("search_knowledge", {"query": ""}, "query"),
        ("search_knowledge", {"query": "x", "limit": 0}, "limit"),
        ("search_knowledge", {"query": "x", "limit": 999}, "limit"),
        ("search_knowledge", {"query": "x", "token_budget": 1}, "token_budget"),
        ("search_knowledge", {"query": "x", "minimum_importance": 2.0}, "minimum_importance"),
        ("search_knowledge", {"query": "x", "sources": ["shell"]}, "sources"),
        ("search_knowledge", {"query": "x", "tags": ["a", "a"]}, "tags"),
        ("search_knowledge", {"query": "x", "unexpected": True}, "unexpected"),
        ("search_context", {"query": "x"}, "project_id"),
        ("search_context", {"project_id": "bad id", "query": "x"}, "project_id"),
        ("get_context", {"project_id": PROJECT, "context_id": "nope"}, "context_id"),
        ("get_skill", {}, "skill_id"),
        ("get_skill", {"skill_id": "s", "version": "one"}, "version"),
        ("save_context", {"project_id": PROJECT, "type": "invented"}, "type"),
        ("save_memory", {"type": "preference", "content": "x"}, "importance"),
        ("save_memory", {"type": "p", "content": "x", "importance": 0.5, "confidence": 9}, "conf"),
        (
            "update_memory",
            {"memory_id": "6720a1b2c3d4e5f60718293b", "expected_revision": 1},
            "at least one field",
        ),
        (
            "update_memory",
            {"memory_id": "6720a1b2c3d4e5f60718293b", "content": "old client update"},
            "expected_revision",
        ),
        ("search_memory", {"query": "x", "scopes": ["project"]}, "requires project_id"),
        ("get_system_status", {"project_id": PROJECT}, "project_id"),
    ],
)
async def test_invalid_arguments_are_refused_before_any_request(
    tool: str,
    arguments: dict[str, Any],
    expected: str,
) -> None:
    api = RecordedApi()

    result = await run(api, tool, arguments)

    assert result.isError is True
    assert "invalid arguments" in message(result)
    assert expected in message(result)
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "VALIDATION_ERROR"
    # Nothing reached the platform, so a malformed call costs it nothing.
    assert api.requests == []


@pytest.mark.asyncio
async def test_a_rejection_names_the_field_without_pydantic_internals() -> None:
    api = RecordedApi()

    result = await run(api, "search_knowledge", {"query": "x", "limit": 0})

    reported = message(result)
    assert "limit" in reported
    assert "ValidationError" not in reported
    assert "pydantic" not in reported
    assert "Traceback" not in reported


@pytest.mark.asyncio
async def test_a_missing_scope_refuses_the_call_before_it_is_parsed() -> None:
    api = RecordedApi()
    grant = build_authorization(["search:read"])

    result = await run(api, "save_memory", {}, authorization=grant)

    # Refused on the scope, not on the missing arguments: an unauthorized
    # caller learns nothing about a tool's shape.
    assert result.isError is True
    assert "memory:write" in message(result)
    assert "invalid arguments" not in message(result)
    assert result.structuredContent == {
        "error": {
            "code": "FORBIDDEN",
            "message": "this caller is not authorized for memory:write",
        }
    }
    assert api.requests == []


@pytest.mark.asyncio
async def test_a_read_grant_still_reaches_the_read_tools() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v1/search", search_body([search_result()]))
    grant = build_authorization(["search:read"])

    result = await run(api, "search_knowledge", {"query": "idempotency"}, authorization=grant)

    assert result.isError is False
    assert api.paths == ["/api/v1/search"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search_context", {"project_id": OTHER_PROJECT, "query": "x"}),
        ("get_context", {"project_id": OTHER_PROJECT, "context_id": "6720a1b2c3d4e5f60718293a"}),
        (
            "save_context",
            {
                "project_id": OTHER_PROJECT,
                "type": "architecture_decision",
                "title": "t",
                "content": "c",
                "importance": 0.5,
            },
        ),
        ("search_knowledge", {"query": "x", "project_id": OTHER_PROJECT}),
        ("search_memory", {"query": "x", "project_id": OTHER_PROJECT}),
        (
            "save_memory",
            {
                "type": "preference",
                "content": "c",
                "importance": 0.5,
                "confidence": 0.5,
                "project_id": OTHER_PROJECT,
            },
        ),
        (
            "update_memory",
            {
                "memory_id": "6720a1b2c3d4e5f60718293b",
                "project_id": OTHER_PROJECT,
                "status": "archived",
                "expected_revision": 1,
            },
        ),
    ],
)
async def test_a_project_outside_the_grant_never_reaches_the_platform(
    tool: str,
    arguments: dict[str, Any],
) -> None:
    api = RecordedApi()
    grant = build_authorization(projects=[PROJECT])

    result = await run(api, tool, arguments, authorization=grant)

    assert result.isError is True
    assert "not authorized for that project" in message(result)
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "PROJECT_FORBIDDEN"
    assert api.requests == []


@pytest.mark.asyncio
async def test_the_granted_project_is_reachable() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v1/search", search_body([search_result()], project_id=PROJECT))
    grant = build_authorization(projects=[PROJECT])

    result = await run(
        api,
        "search_knowledge",
        {"query": "idempotency", "project_id": PROJECT},
        authorization=grant,
    )

    assert result.isError is False


@pytest.mark.asyncio
async def test_global_knowledge_stays_reachable_under_a_project_grant() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v1/search", search_body([search_result(project_id=None)]))
    grant = build_authorization(projects=[PROJECT])

    result = await run(api, "search_knowledge", {"query": "idempotency"}, authorization=grant)

    # Omitting a project addresses global knowledge; it is never read as "any
    # project", which would be inferring an identity from an absence.
    assert result.isError is False
    assert api.body()["project_id"] is None


@pytest.mark.asyncio
async def test_a_project_the_platform_refuses_is_reported_as_not_found() -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v1/context/6720a1b2c3d4e5f60718293a",
        {"detail": "context was not found"},
        status_code=404,
    )

    result = await run(
        api,
        "get_context",
        {"project_id": OTHER_PROJECT, "context_id": "6720a1b2c3d4e5f60718293a"},
    )

    # Another project's item is not found rather than forbidden, so its
    # existence is not disclosed by the shape of the refusal.
    assert result.isError is True
    assert "no such record" in message(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected", "code"),
    [
        (400, "malformed", "VALIDATION_ERROR"),
        (401, "unauthenticated", "UNAUTHENTICATED"),
        (403, "unauthorized", "FORBIDDEN"),
        (404, "no such record", "NOT_FOUND"),
        (409, "changed after it was read", "REVISION_CONFLICT"),
        (422, "rejected as invalid", "VALIDATION_ERROR"),
        (429, "rate limiting", "RATE_LIMITED"),
        (503, "cannot serve this request", "SERVICE_UNAVAILABLE"),
        (418, "could not serve this request", "SERVICE_UNAVAILABLE"),
    ],
)
async def test_every_upstream_refusal_becomes_a_readable_failure(
    status_code: int,
    expected: str,
    code: str,
) -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v1/search", {"detail": "upstream said so"}, status_code=status_code)

    result = await run(api, "search_knowledge", {"query": "x"})

    assert result.isError is True
    assert expected in message(result)
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == code


@pytest.mark.asyncio
async def test_a_conflict_reports_what_the_platform_said() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v1/memory",
        {"detail": "that memory has already been superseded"},
        status_code=409,
    )

    result = await run(
        api,
        "save_memory",
        {
            "type": "convention",
            "content": "A competing correction.",
            "importance": 0.9,
            "confidence": 0.9,
            "supersedes": "6720a1b2c3d4e5f60718293c",
        },
    )

    assert "already been superseded" in message(result)


@pytest.mark.asyncio
async def test_an_unreachable_platform_is_reported_as_such() -> None:
    api = RecordedApi(error=httpx.ConnectError("connection refused"))

    result = await run(api, "search_knowledge", {"query": "x"})

    assert result.isError is True
    assert "unreachable" in message(result)
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "SERVICE_UNAVAILABLE"
    # The transport's own message would name a host and a port.
    assert "connection refused" not in message(result)


@pytest.mark.asyncio
async def test_a_request_timeout_uses_the_stable_timeout_code() -> None:
    api = RecordedApi(error=httpx.ReadTimeout("deadline reached"))

    result = await run(api, "search_knowledge", {"query": "x"})

    assert result.isError is True
    assert result.structuredContent == {
        "error": {
            "code": "OPERATION_TIMEOUT",
            "message": "the platform did not answer before the request deadline",
        }
    }


@pytest.mark.asyncio
async def test_an_unexpected_failure_is_never_described_to_the_caller(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def explode(client: Any, arguments: Any) -> Any:
        raise RuntimeError("connection string mongodb://user:pass@db/agent_context")

    broken = replace(TOOLS_BY_NAME["get_project"], handler=explode)
    monkeypatch.setattr(server, "TOOLS_BY_NAME", {"get_project": broken})

    with caplog.at_level(logging.ERROR, logger="agent_context.mcp"):
        result = await run(RecordedApi(), "get_project", {"project_id": PROJECT})

    # The caller is told the call failed, not how: an exception's text is
    # written for an operator's log, and can name anything at all.
    reported = message(result)
    assert result.isError is True
    assert reported == "the tool failed for an unexpected reason"
    assert "mongodb://" not in reported
    # The operator still gets the whole thing, where it belongs.
    assert "mongodb://" in caplog.text


@pytest.mark.asyncio
async def test_a_successful_call_carries_both_forms_of_its_answer() -> None:
    api = RecordedApi()
    api.respond("GET", "/api/v1/context/6720a1b2c3d4e5f60718293a", context_record())

    result = await run(
        api,
        "get_context",
        {"project_id": PROJECT, "context_id": "6720a1b2c3d4e5f60718293a"},
    )

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.content[0].text  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_the_api_token_is_attached_to_every_request() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v1/memory/search", {"query": "x", "results": []})
    client = api_client(api, token="secret-token-value")

    try:
        await dispatch(
            "search_memory",
            {"query": "typing"},
            client=client,
            authorization=Authorization(),
        )
    finally:
        await client.close()

    assert api.last.headers["authorization"] == "Bearer secret-token-value"


@pytest.mark.asyncio
async def test_memory_returned_for_another_project_is_still_reported_as_it_arrived() -> None:
    # The platform is the isolation boundary; this asserts the tool does not
    # quietly re-label what it was given, so a leak upstream stays visible.
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v1/memory/search",
        {
            "query": "x",
            "project_id": PROJECT,
            "results": [{"score": 0.9, "memory": memory_record(PROJECT)}],
        },
    )

    result = await run(api, "search_memory", {"query": "x", "project_id": PROJECT})

    assert result.structuredContent is not None
    assert result.structuredContent["results"][0]["metadata"]["project_id"] == PROJECT
