"""Thin MCP adapter for the API-owned, client-scoped retry ledger."""

import asyncio
from collections.abc import Awaitable, Callable

from app.mcp.client import ContextApiClient
from app.mcp.errors import ToolError, UpstreamError
from app.mcp.tools.base import ToolResult


async def run_idempotent_write(
    *,
    client: ContextApiClient,
    operation: str,
    idempotency_key: str,
    payload_hash: str,
    invoke: Callable[[], Awaitable[ToolResult]],
) -> ToolResult:
    """Run one write once, or replay its prior completed result for this client.

    The API derives the client from the bearer credential.  This adapter never
    sends a client id, retains a request body, or logs a key.  An uncertain
    cancellation deliberately leaves the reservation in place until retention
    expiry, which is safer than accidentally repeating a write.
    """
    reservation = await client.reserve_gateway_idempotency(
        operation=operation,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
    )
    decision = reservation.get("decision")
    if decision == "replay":
        result = reservation.get("result")
        if not isinstance(result, dict):
            raise ToolError("the platform returned an unusable idempotency replay")
        text = result.get("text")
        data = result.get("data")
        if not isinstance(text, str) or not isinstance(data, dict):
            raise ToolError("the platform returned an unusable idempotency replay")
        return ToolResult(text=text, data=data)
    if decision != "proceed":
        raise ToolError("the platform returned an unusable idempotency reservation")
    try:
        result = await invoke()
    except asyncio.CancelledError:
        # The upstream request may have committed after the gateway deadline.
        # Retain the reservation rather than turn a timeout into a duplicate.
        raise
    except UpstreamError as error:
        # Input and authorization rejections are known not to have completed
        # this write. Transport and server errors remain reserved because the
        # caller cannot safely know whether the domain write reached MongoDB.
        if error.status_code < 500:
            await _abandon_safely(client, operation, idempotency_key, payload_hash)
        raise
    except Exception:
        # A local or transport fault is uncertain. Leave the record pending;
        # retrying after a timeout must not create a second durable write.
        raise
    await client.complete_gateway_idempotency(
        operation=operation,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        text=result.text,
        data=result.data,
    )
    return result


async def _abandon_safely(
    client: ContextApiClient,
    operation: str,
    idempotency_key: str,
    payload_hash: str,
) -> None:
    """Best-effort cleanup for a request the API rejected before writing."""
    try:
        await client.abandon_gateway_idempotency(
            operation=operation,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
    except Exception:
        # Failing closed leaves a harmless short-lived pending reservation.
        return
