"""The stable, credential-free portion of the MCP client contract.

This module deliberately holds only information that is safe to return to an
MCP client.  It does not know bearer tokens, API addresses, database details,
or the concrete authorization implementation.  The dispatcher binds one
instance for each tool call so capability discovery can report the *effective*
grant after both deployment and credential limits are applied.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.mcp.middleware.client_context import ClientRequestContext


class McpErrorCode(StrEnum):
    """The stable error codes MCP callers may safely branch on."""

    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    PROJECT_FORBIDDEN = "PROJECT_FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    OPERATION_TIMEOUT = "OPERATION_TIMEOUT"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class DescribedAuthorization(Protocol):
    """The small authorization surface capability discovery requires."""

    def describe(self) -> dict[str, object]:
        """Return an effective, credential-free authorization summary."""


@dataclass(frozen=True, slots=True)
class CallContractContext:
    """Safe facts about the MCP call currently being dispatched."""

    authorization: DescribedAuthorization
    client: ClientRequestContext | None


_current: ContextVar[CallContractContext | None] = ContextVar("mcp_contract_context", default=None)


@contextmanager
def bind_call_contract(
    authorization: DescribedAuthorization,
    client: ClientRequestContext | None,
) -> Iterator[None]:
    """Make effective authorization available only for the current tool call."""
    token: Token[CallContractContext | None] = _current.set(
        CallContractContext(authorization=authorization, client=client)
    )
    try:
        yield
    finally:
        _current.reset(token)


def current_call_contract() -> CallContractContext | None:
    """Return the current safe call facts, if dispatch has bound them."""
    return _current.get()


def error_payload(code: McpErrorCode | str, message: str) -> dict[str, dict[str, str]]:
    """Return the common error envelope used by all dispatch-owned failures."""
    return {"error": {"code": str(code), "message": message}}


def gateway_error_message(code: str) -> str:
    """Describe an HTTP-edge credential refusal without exposing internals."""
    messages = {
        McpErrorCode.UNAUTHENTICATED.value: "the credential was not accepted",
        "CREDENTIAL_EXPIRED": "the credential has expired",
        "CLIENT_DISABLED": "the client is disabled",
        McpErrorCode.FORBIDDEN.value: "the credential is not authorized",
        McpErrorCode.PROJECT_FORBIDDEN.value: "the credential is not authorized for that project",
    }
    return messages.get(code, "the gateway refused the credential")
