"""The authenticated Streamable HTTP entry point for remote MCP clients.

This is deliberately a transport adapter, not another MCP implementation.  It
uses the same low-level server and the same tool registry as stdio, and turns
the incoming bearer credential into the existing gateway context before a
session is allowed to start.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx
from mcp.server.streamable_http import MCP_SESSION_ID_HEADER
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.datastructures import URLPath
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Match
from starlette.types import Message, Receive, Scope, Send

from app.api.security import credential
from app.mcp.auth import Authorization
from app.mcp.client import ContextApiClient
from app.mcp.contract import error_payload, gateway_error_message
from app.mcp.gateway_controls import McpGatewayControls
from app.mcp.gateway_errors import GatewayErrorCode
from app.mcp.middleware.authentication import GatewayAuthenticationMiddleware
from app.mcp.remote_security import RemoteMcpTransportPolicy
from app.security.principal import Principal
from app.services.auth_service import (
    AuthService,
    GatewayAuthenticationCode,
    GatewayAuthenticationError,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("agent_context.mcp")

_CHALLENGE = {"WWW-Authenticate": "Bearer"}


@dataclass(slots=True)
class RemoteMcpRuntime:
    """One authenticated remote MCP connection, with its in-memory bearer.

    The token is intentionally held only by ``ContextApiClient`` for the
    lifetime of an MCP transport session.  It is never serialized, logged, or
    placed in a response.
    """

    principal: Principal
    client: ContextApiClient
    authorization: Authorization
    gateway: GatewayAuthenticationMiddleware

    async def close(self) -> None:
        """Release the HTTP client when its MCP session is terminated."""
        await self.client.close()


_runtime: ContextVar[RemoteMcpRuntime | None] = ContextVar("remote_mcp_runtime", default=None)


@contextmanager
def bind_remote_mcp_runtime(runtime: RemoteMcpRuntime) -> Iterator[None]:
    """Make a connection's client available to the shared MCP handlers."""
    token: Token[RemoteMcpRuntime | None] = _runtime.set(runtime)
    try:
        yield
    finally:
        _runtime.reset(token)


def current_remote_mcp_runtime() -> RemoteMcpRuntime | None:
    """Return the remote transport context inherited by an MCP session task."""
    return _runtime.get()


class RemoteMcpGateway:
    """Serve the one client-neutral MCP server through Streamable HTTP.

    A manager is constructed once per application lifespan, as required by the
    MCP SDK.  The ASGI edge authenticates every HTTP request with the existing
    API-key service.  Tool calls authenticate again through the Phase 2
    gateway preflight, so revocation takes effect on the next call even inside
    an already-established MCP session.
    """

    def __init__(self) -> None:
        self._application: FastAPI | None = None
        self._authorization: Authorization | None = None
        self._manager: StreamableHTTPSessionManager | None = None
        self._sessions: dict[str, RemoteMcpRuntime] = {}
        self._session_lock = asyncio.Lock()
        self._pending_sessions = 0
        self._transport_policy = RemoteMcpTransportPolicy()

    @asynccontextmanager
    async def run(
        self,
        application: FastAPI,
        *,
        authorization: Authorization,
        server_name: str,
        controls: McpGatewayControls | None = None,
        transport_policy: RemoteMcpTransportPolicy | None = None,
    ) -> AsyncIterator[None]:
        """Run the SDK session manager for one FastAPI application lifespan."""
        if self._manager is not None:
            raise RuntimeError("the remote MCP gateway is already running")
        # Importing here keeps the shared low-level server independent of this
        # HTTP-only transport module and avoids a module import cycle.
        from app.mcp.server import build_server

        self._application = application
        self._authorization = authorization
        self._transport_policy = transport_policy or RemoteMcpTransportPolicy()
        server = build_server(
            None,
            authorization,
            name=server_name,
            controls=controls or McpGatewayControls.defaults(),
        )
        # Streamable HTTP permits JSON responses as well as SSE.  JSON keeps
        # one request/response exchange bounded for these request-driven tools
        # while remaining compatible with Claude Code's HTTP transport.
        manager = StreamableHTTPSessionManager(server, json_response=True)
        self._manager = manager
        try:
            async with manager.run():
                yield
        finally:
            await self._close_sessions()
            self._manager = None
            self._authorization = None
            self._application = None
            self._transport_policy = RemoteMcpTransportPolicy()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Authenticate an HTTP request, then pass it to the MCP transport."""
        if scope["type"] != "http":
            await _plain_response(HTTPStatus.NOT_FOUND, "not found", scope, receive, send)
            return
        application = self._application
        manager = self._manager
        authorization = self._authorization
        if application is None or manager is None or authorization is None:
            await _plain_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "the remote MCP gateway is not ready",
                scope,
                receive,
                send,
            )
            return

        # `scope["scheme"]` comes from the socket or a specifically trusted
        # reverse proxy.  Never inspect a caller-provided X-Forwarded-Proto
        # here: that would let a public HTTP client claim it used TLS.
        if not self._transport_policy.permits(scope):
            await _transport_response(scope, receive, send)
            return

        request = Request(scope, receive)
        presented = credential(request.headers.get("authorization"))
        try:
            principal = await _authenticate(application, presented)
        except _RemoteAuthenticationFailure as error:
            await _authentication_response(error.code, scope, receive, send)
            return

        requested_session = request.headers.get(MCP_SESSION_ID_HEADER)
        runtime = await self._get_session(requested_session) if requested_session else None
        if runtime is not None and (
            runtime.principal.key_id != principal.key_id
            or runtime.principal.org_id != principal.org_id
        ):
            # A random session ID must not turn one valid credential into
            # authority to send requests through a different client's session.
            await _authentication_response(GatewayErrorCode.UNAUTHENTICATED, scope, receive, send)
            return
        if runtime is None:
            if not await self._reserve_session_slot():
                await _session_limit_response(scope, receive, send)
                return
            runtime = _runtime_for(application, principal, presented, authorization)
            created_runtime = True
            reserved_session_slot = True
        else:
            created_runtime = False
            reserved_session_slot = False

        session_created: str | None = None
        response_status: int | None = None

        async def recording_send(message: Message) -> None:
            nonlocal response_status, session_created
            if message["type"] == "http.response.start":
                status = message.get("status")
                response_status = status if isinstance(status, int) else None
                raw_headers = message.get("headers", [])
                for name, value in raw_headers:
                    if name.lower() == MCP_SESSION_ID_HEADER.encode("ascii"):
                        session_created = value.decode("ascii")
                        break
            await send(message)

        try:
            with bind_remote_mcp_runtime(runtime):
                await manager.handle_request(scope, receive, recording_send)

            if (
                response_status is not None
                and 200 <= response_status < 300
                and session_created is not None
            ):
                await self._track_session(
                    session_created,
                    runtime,
                    consumes_reserved_slot=reserved_session_slot,
                )
                reserved_session_slot = False
                created_runtime = False
            if request.method == "DELETE" and requested_session is not None:
                await self._close_session(requested_session)
        finally:
            if reserved_session_slot:
                await self._release_session_slot()
            # Invalid session IDs and malformed initialization requests never
            # become tracked SDK sessions, so their ephemeral API client must
            # not outlive this one HTTP request.
            if created_runtime:
                await runtime.close()

    async def _close_session(self, session_id: str) -> None:
        async with self._session_lock:
            runtime = self._sessions.pop(session_id, None)
        if runtime is not None:
            await runtime.close()

    async def _close_sessions(self) -> None:
        async with self._session_lock:
            sessions, self._sessions = tuple(self._sessions.values()), {}
            self._pending_sessions = 0
        for runtime in sessions:
            await runtime.close()

    async def _get_session(self, session_id: str) -> RemoteMcpRuntime | None:
        """Return a tracked session without exposing the mutable mapping."""
        async with self._session_lock:
            return self._sessions.get(session_id)

    async def _reserve_session_slot(self) -> bool:
        """Reserve capacity before an untrusted initialize request is handled."""
        async with self._session_lock:
            used = len(self._sessions) + self._pending_sessions
            if used >= self._transport_policy.max_sessions:
                return False
            self._pending_sessions += 1
            return True

    async def _release_session_slot(self) -> None:
        """Release a reservation for a request that never created a session."""
        async with self._session_lock:
            self._pending_sessions = max(0, self._pending_sessions - 1)

    async def _track_session(
        self,
        session_id: str,
        runtime: RemoteMcpRuntime,
        *,
        consumes_reserved_slot: bool,
    ) -> None:
        """Track a successful SDK session and consume its reserved capacity."""
        async with self._session_lock:
            self._sessions[session_id] = runtime
            if consumes_reserved_slot:
                self._pending_sessions = max(0, self._pending_sessions - 1)


class _RemoteAuthenticationFailure(Exception):
    """An HTTP-edge authentication rejection with a fixed public code."""

    def __init__(self, code: GatewayErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


async def _authenticate(application: FastAPI, presented: str | None) -> Principal:
    """Authenticate only through the existing API-key authenticator."""
    auth = getattr(application.state, "auth", None)
    if not isinstance(auth, AuthService):
        # Remote MCP is never anonymous, even if an operator enabled an open
        # API for a local development workflow.
        raise _RemoteAuthenticationFailure(GatewayErrorCode.UNAUTHENTICATED)
    try:
        return await auth.authenticate_gateway(presented)
    except GatewayAuthenticationError as error:
        raise _RemoteAuthenticationFailure(_gateway_code(error.code)) from error


def _runtime_for(
    application: FastAPI,
    principal: Principal,
    presented: str | None,
    authorization: Authorization,
) -> RemoteMcpRuntime:
    """Build one in-process API client carrying the authenticated bearer."""
    if presented is None:  # Defensive: `_authenticate` rejected this already.
        raise RuntimeError("an authenticated remote request must have a bearer credential")
    client = ContextApiClient(
        "http://agent-context.internal",
        token=presented,
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
    )
    return RemoteMcpRuntime(
        principal=principal,
        client=client,
        authorization=authorization,
        gateway=GatewayAuthenticationMiddleware(client),
    )


def _gateway_code(code: GatewayAuthenticationCode) -> GatewayErrorCode:
    """Translate the existing authentication detail into gateway vocabulary."""
    return GatewayErrorCode(code.value)


async def _authentication_response(
    code: GatewayErrorCode,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    status = (
        HTTPStatus.FORBIDDEN
        if code == GatewayErrorCode.CLIENT_DISABLED
        else HTTPStatus.UNAUTHORIZED
    )
    response = JSONResponse(
        status_code=status,
        content=error_payload(code, gateway_error_message(code.value)),
        headers=_CHALLENGE,
    )
    await response(scope, receive, send)


async def _transport_response(
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    """Refuse remote plaintext without revealing deployment topology."""
    response = JSONResponse(
        status_code=HTTPStatus.FORBIDDEN,
        content=error_payload(
            GatewayErrorCode.FORBIDDEN,
            "remote MCP requires HTTPS",
        ),
    )
    await response(scope, receive, send)


async def _session_limit_response(
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    """Bound live remote MCP sessions before they consume unbounded clients."""
    response = JSONResponse(
        status_code=HTTPStatus.TOO_MANY_REQUESTS,
        content=error_payload(
            "RATE_LIMITED",
            "remote MCP session limit reached",
        ),
    )
    await response(scope, receive, send)


async def _plain_response(
    status: HTTPStatus,
    detail: str,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    response = JSONResponse(status_code=status, content={"detail": detail})
    await response(scope, receive, send)


class RemoteMcpRoute(BaseRoute):
    """An exact `/mcp` ASGI route with no trailing-slash redirect.

    The Streamable HTTP endpoint is a protocol URL, so changing `/mcp` to
    `/mcp/` through Starlette's normal mount redirect would make documented
    client configuration needlessly brittle.
    """

    path = "/mcp"

    def __init__(self, gateway: RemoteMcpGateway) -> None:
        self.endpoint = gateway
        self.name = "mcp"

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope["type"] == "http" and _route_path(scope) == self.path:
            return Match.FULL, {"endpoint": self.endpoint, "route": self}
        return Match.NONE, {}

    def url_path_for(self, name: str, /, **path_params: object) -> URLPath:
        if name != self.name or path_params:
            raise RuntimeError(f'No route exists for name "{name}" and params "{path_params}".')
        return URLPath(self.path)

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.endpoint(scope, receive, send)


def add_remote_mcp_route(application: FastAPI, gateway: RemoteMcpGateway) -> None:
    """Register the exact remote MCP route without a Mount redirect."""
    application.router.routes.append(RemoteMcpRoute(gateway))


def _route_path(scope: Scope) -> str:
    """Return the router-relative path without relying on Starlette internals."""
    path = scope.get("path", "")
    root_path = scope.get("root_path", "")
    if isinstance(path, str) and isinstance(root_path, str) and path.startswith(root_path):
        return path[len(root_path) :]
    return path if isinstance(path, str) else ""
