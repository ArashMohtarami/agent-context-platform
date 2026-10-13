"""Authenticate the MCP process through the existing Context API key service."""

from app.mcp.client import ContextApiClient
from app.mcp.gateway_errors import GatewayError, GatewayErrorCode
from app.mcp.middleware.client_context import ClientRequestContext
from app.models.common import validate_identifier


class GatewayAuthenticationMiddleware:
    """Resolve the configured bearer token into one fresh client context.

    The lookup is deliberately per tool call.  It adds a small local API round
    trip, but means a credential revocation or client disable takes effect on
    the very next MCP call rather than after a process restart or cache expiry.
    """

    def __init__(self, client: ContextApiClient, *, session_id: str | None = None) -> None:
        self._client = client
        self._session_id = None if session_id is None else validate_identifier(session_id)

    @property
    def session_id(self) -> str | None:
        """Return the registered lifecycle session attached to future calls."""
        return self._session_id

    def bind_session_id(self, session_id: str) -> None:
        """Attach a server-created session after a successful begin call."""
        self._session_id = validate_identifier(session_id)

    def clear_session_id(self, session_id: str) -> None:
        """Detach only the session the caller just ended."""
        if self._session_id == session_id:
            self._session_id = None

    async def authenticate(self, request_id: str) -> ClientRequestContext:
        identity = await self._client.gateway_identity(session_id=self._session_id)
        try:
            return ClientRequestContext.from_identity(
                identity,
                request_id=request_id,
                session_id=self._session_id,
            )
        except ValueError as error:
            # The API identity response is an internal contract.  If it is
            # malformed, refuse rather than granting a partial or widened
            # context, and do not leak the response details to the client.
            raise GatewayError(
                GatewayErrorCode.UNAUTHENTICATED,
                "the credential was not accepted",
            ) from error
