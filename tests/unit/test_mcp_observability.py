"""What the MCP server reports about the calls it serves."""

import logging
from collections.abc import Iterator
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.mcp import server
from app.mcp.auth import Authorization, build_authorization
from app.mcp.server import dispatch
from app.mcp.tools import TOOLS_BY_NAME
from app.observability import context, instruments
from app.observability.instruments import REGISTRY
from tests.mcp_doubles import RecordedApi, api_client, context_record, search_body

PROJECT = "sampleco-payment"


@pytest.fixture(autouse=True)
def empty_registry() -> Iterator[None]:
    """Record into a registry no other test has already written to."""
    REGISTRY.reset()
    yield
    REGISTRY.reset()


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


def searching() -> RecordedApi:
    """Return an API double that answers a knowledge search."""
    api = RecordedApi()
    api.respond("POST", "/api/v1/search", search_body())
    return api


def calls(tool: str, outcome: str) -> float | None:
    """Return how many calls of one tool ended one way."""
    return REGISTRY.value(instruments.MCP_CALLS, tool=tool, outcome=outcome)


@pytest.mark.asyncio
async def test_a_successful_call_is_counted_and_timed() -> None:
    await run(searching(), "search_knowledge", {"query": "refunds"})

    assert calls("search_knowledge", "ok") == 1
    measured = REGISTRY.summary(instruments.MCP_DURATION, tool="search_knowledge")
    assert measured is not None and measured[0] == 1


@pytest.mark.asyncio
async def test_a_call_the_caller_got_wrong_is_counted_as_refused() -> None:
    await run(searching(), "search_knowledge", {})

    assert calls("search_knowledge", "refused") == 1
    assert calls("search_knowledge", "ok") is None


@pytest.mark.asyncio
async def test_a_call_outside_the_grant_is_counted_as_refused() -> None:
    granted = build_authorization(["memory:read"], None)

    await run(searching(), "search_knowledge", {"query": "x"}, authorization=granted)

    assert calls("search_knowledge", "refused") == 1


@pytest.mark.asyncio
async def test_an_unreachable_platform_is_counted_as_unavailable() -> None:
    # Distinct from a refusal on purpose: an availability number that could not
    # tell a mistyped project from an outage would go red for both.
    api = RecordedApi(error=httpx.ConnectError("connection refused"))

    await run(api, "search_knowledge", {"query": "x"})

    assert calls("search_knowledge", "unavailable") == 1
    assert calls("search_knowledge", "refused") is None


@pytest.mark.asyncio
async def test_a_platform_error_is_counted_as_unavailable_and_a_rejection_is_not() -> None:
    unavailable = RecordedApi()
    unavailable.respond("POST", "/api/v1/search", {"detail": "down"}, status_code=503)
    rejected = RecordedApi()
    rejected.respond("POST", "/api/v1/search", {"detail": "no"}, status_code=422)

    await run(unavailable, "search_knowledge", {"query": "x"})
    await run(rejected, "search_knowledge", {"query": "x"})

    assert calls("search_knowledge", "unavailable") == 1
    assert calls("search_knowledge", "refused") == 1


@pytest.mark.asyncio
async def test_a_defect_in_this_server_is_counted_apart_from_everything_else(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def explode(client: Any, arguments: Any) -> Any:
        raise RuntimeError("something internal")

    broken = replace(TOOLS_BY_NAME["get_project"], handler=explode)
    monkeypatch.setattr(server, "TOOLS_BY_NAME", {"get_project": broken})

    with caplog.at_level(logging.ERROR, logger="agent_context.mcp"):
        await run(RecordedApi(), "get_project", {"project_id": PROJECT})

    assert calls("get_project", "failed") == 1


@pytest.mark.asyncio
async def test_an_unknown_tool_cannot_create_a_metric_series_of_its_own() -> None:
    # The tool name comes from the caller, so it is counted under a fixed label
    # rather than becoming a series named after whatever was sent.
    await run(RecordedApi(), "rm -rf /", {})

    assert calls("unknown", "refused") == 1
    assert REGISTRY.dropped() == {}


@pytest.mark.asyncio
async def test_every_call_is_reported_as_one_structured_event() -> None:
    # This server speaks stdio and opens no port, so there is nothing for a
    # scraper to reach: the log stream it already has is how its numbers leave.
    with patch("app.mcp.server.logger") as logger:
        await run(searching(), "search_knowledge", {"query": "refunds"})

    logged = logger.info.call_args
    assert logged.args == ("mcp_tool_completed",)
    assert logged.kwargs["extra"]["mcp_tool"] == "search_knowledge"
    assert logged.kwargs["extra"]["outcome"] == "ok"
    assert logged.kwargs["extra"]["component"] == "mcp"
    assert logged.kwargs["extra"]["duration_ms"] >= 0.0
    assert "query" not in logged.kwargs["extra"]


@pytest.mark.asyncio
async def test_the_reported_event_names_the_operation_and_the_project() -> None:
    api = RecordedApi()
    api.respond("GET", "/api/v1/context/6720a1b2c3d4e5f60718293a", context_record())

    with patch("app.mcp.server.logger") as logger:
        await run(
            api,
            "get_context",
            {"project_id": PROJECT, "context_id": "6720a1b2c3d4e5f60718293a"},
        )

    logged = logger.info.call_args.kwargs["extra"]
    assert logged["operation"] == "get_context"
    assert logged["project_id"] == PROJECT
    assert len(logged["request_id"]) == 32


@pytest.mark.asyncio
async def test_the_call_identifier_travels_to_the_platform_with_the_request() -> None:
    # One tool call and the API request it became carry the same identifier, in
    # two processes' logs, which is what makes an agent's slow search traceable
    # to the query that was slow.
    api = searching()

    with patch("app.mcp.server.logger") as logger:
        await run(api, "search_knowledge", {"query": "refunds"})

    sent = api.last.headers["x-request-id"]
    assert sent == logger.info.call_args.kwargs["extra"]["request_id"]


@pytest.mark.asyncio
async def test_a_call_leaves_no_correlation_behind_it() -> None:
    await run(searching(), "search_knowledge", {"query": "refunds"})

    assert context.current() is None
