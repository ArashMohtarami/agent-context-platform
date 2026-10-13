"""The one way an in-process client reaches the platform.

Every MCP tool goes through this client, and so does `contextctl`: both are API
clients with the same needs, and two implementations of "talk to the API
safely" would be two things to keep correct.

It only ever issues the documented Context API requests. There is no query
parameter a caller can fill with a database expression, no path a caller can
choose, and no escape hatch — a caller names a method and a route this module
already knows, or it cannot make a request at all.
"""

from collections.abc import Mapping
from types import TracebackType
from typing import Any, Final, Literal, Self

import httpx

from app.mcp.errors import (
    IdempotencyConflictToolError,
    OperationTimeoutError,
    RevisionConflictToolError,
    UnavailableError,
    UpstreamError,
)
from app.mcp.gateway_errors import GatewayError, GatewayErrorCode
from app.mcp.middleware.client_context import current_client_context
from app.observability import context

Method = Literal["GET", "POST", "PATCH", "DELETE"]

# Responses are bounded by the API itself, but a malfunctioning or wrong
# endpoint should not be able to exhaust this process's memory either.
MAX_RESPONSE_BYTES: Final = 8 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS: Final = 30.0


class ContextApiClient:
    """An HTTP client bound to one Context API deployment.

    The bearer token, when a deployment sets one, is attached to every request
    and never logged, echoed into an error, or returned to a caller.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("the Context API base URL must not be empty")
        if timeout_seconds <= 0.0:
            raise ValueError("the request timeout must be positive")
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
            # The Context API address is explicit and self-hosted. Inheriting
            # a developer shell's proxy can route private knowledge outside
            # the host, and a SOCKS proxy also makes an unrelated optional
            # dependency necessary before this client can even start.
            trust_env=False,
        )

    @property
    def base_url(self) -> str:
        """Return the platform address this client was built for.

        Useful in a message that has to tell somebody where nothing answered.
        It carries no credential: the token lives in a header, not the URL.
        """
        return str(self._client.base_url)

    async def __aenter__(self) -> Self:
        """Enter the client's lifetime."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the connection pool."""
        await self.close()

    async def close(self) -> None:
        """Close the underlying connection pool."""
        await self._client.aclose()

    async def request(
        self,
        method: Method,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        json: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Issue one documented API request and return its decoded body.

        A refusal becomes a `ToolError` carrying the API's own message, and an
        unreachable API becomes one saying so. Neither reveals the URL, the
        transport, or the exception that caused it.
        """
        if not path.startswith("/"):
            raise ValueError("an API path must be absolute")
        try:
            response = await self._client.request(
                method,
                path,
                params=None if params is None else _query(params),
                json=None if json is None else dict(json),
                headers=_headers(),
            )
        except httpx.TimeoutException as error:
            raise OperationTimeoutError(
                "the platform did not answer before the request deadline"
            ) from error
        except httpx.HTTPError as error:
            raise UnavailableError("the platform is unreachable") from error
        return self._decode(response)

    async def probe(self, path: str) -> tuple[int, dict[str, Any]]:
        """Read one health endpoint, treating "not ready" as an answer.

        Readiness answers with 503 precisely when something is wrong, which is
        the case a status tool exists to report. Raising there would turn the
        one tool that explains an outage into another casualty of it.
        """
        if not path.startswith("/"):
            raise ValueError("an API path must be absolute")
        try:
            response = await self._client.get(path, headers=_headers())
        except httpx.TimeoutException as error:
            raise OperationTimeoutError(
                "the platform did not answer before the request deadline"
            ) from error
        except httpx.HTTPError as error:
            raise UnavailableError("the platform is unreachable") from error
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}

    async def gateway_identity(self, *, session_id: str | None = None) -> dict[str, Any]:
        """Resolve this MCP process's bearer token into safe gateway facts."""
        headers = _headers()
        # Authentication happens before a client context is bound, so a
        # registered lifecycle session has to be carried explicitly here.
        # The API verifies that it belongs to this bearer-linked client.
        if session_id is not None:
            headers["X-Agent-Context-Session-ID"] = session_id
        try:
            response = await self._client.get("/api/v4/gateway/identity", headers=headers)
        except httpx.TimeoutException as error:
            raise OperationTimeoutError(
                "the platform did not answer before the request deadline"
            ) from error
        except httpx.HTTPError as error:
            raise UnavailableError("the platform is unreachable") from error
        if response.is_success:
            return self._decode(response)
        raise GatewayError(_gateway_error_code(response), _gateway_error_message(response))

    async def record_gateway_activity(
        self,
        *,
        operation: str,
        tool: str | None,
        project_id: str | None,
        resource_type: str | None,
        resource_id: str | None,
        status: str,
        latency_ms: float,
    ) -> None:
        """Persist one actor-derived, content-free MCP activity event."""
        await self.request(
            "POST",
            "/api/v4/gateway/activity",
            json={
                "operation": operation,
                "tool": tool,
                "project_id": project_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "status": status,
                "latency_ms": latency_ms,
            },
        )

    async def reserve_gateway_idempotency(
        self,
        *,
        operation: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> dict[str, Any]:
        """Reserve one client-scoped MCP write or receive its replay result."""
        return await self._gateway_idempotency_request(
            "/api/v4/gateway/idempotency/reservations",
            operation=operation,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )

    async def complete_gateway_idempotency(
        self,
        *,
        operation: str,
        idempotency_key: str,
        payload_hash: str,
        text: str,
        data: dict[str, object],
    ) -> None:
        """Persist the exact bounded result for a completed MCP retry reservation."""
        await self._gateway_idempotency_request(
            "/api/v4/gateway/idempotency/reservations/complete",
            operation=operation,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            result={"text": text, "data": data},
        )

    async def abandon_gateway_idempotency(
        self,
        *,
        operation: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> None:
        """Release a retry reservation only after a known pre-write refusal."""
        await self._gateway_idempotency_request(
            "/api/v4/gateway/idempotency/reservations/abandon",
            operation=operation,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )

    async def _gateway_idempotency_request(
        self,
        path: str,
        *,
        operation: str,
        idempotency_key: str,
        payload_hash: str,
        result: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        """Call the narrow idempotency contract without turning its key into a log field."""
        body: dict[str, object] = {
            "operation": operation,
            "idempotency_key": idempotency_key,
            "payload_hash": payload_hash,
        }
        if result is not None:
            body["result"] = result
        try:
            response = await self._client.post(path, json=body, headers=_headers())
        except httpx.TimeoutException as error:
            raise OperationTimeoutError(
                "the platform did not answer before the request deadline"
            ) from error
        except httpx.HTTPError as error:
            raise UnavailableError("the platform is unreachable") from error
        domain_code = _gateway_domain_error_code(response)
        if domain_code == "IDEMPOTENCY_CONFLICT":
            raise IdempotencyConflictToolError(
                "the idempotency key was already used for a different request"
            )
        if domain_code == "OPERATION_TIMEOUT":
            raise OperationTimeoutError(
                "a prior request with this idempotency key is still being processed"
            )
        return self._decode(response)

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        """Turn one response into a body or a safe failure."""
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise UnavailableError("the platform returned more than this server accepts")
        if response.is_success:
            if response.status_code == 204:
                return {}
            try:
                decoded = response.json()
            except ValueError as error:
                raise UnavailableError("the platform returned an unreadable response") from error
            if not isinstance(decoded, dict):
                raise UnavailableError("the platform returned an unexpected response")
            return decoded
        if _is_revision_conflict(response):
            raise RevisionConflictToolError()
        raise UpstreamError(response.status_code, _detail(response))


def _headers() -> dict[str, str]:
    """Carry request correlation and safe gateway provenance to the platform.

    Nothing here carries tool arguments, prompt bodies, retrieved content, or
    secrets.  The bearer token remains in the HTTP client's private default
    headers, not in correlation or provenance metadata.
    """
    request_id = context.request_id()
    headers = {} if request_id is None else {context.HEADER: request_id}
    gateway_context = current_client_context()
    if gateway_context is not None:
        headers.update(gateway_context.headers())
    return headers


def _gateway_error_code(response: httpx.Response) -> GatewayErrorCode:
    """Read a fixed API gateway code, collapsing malformed responses safely."""
    try:
        body = response.json()
    except ValueError:
        return GatewayErrorCode.UNAUTHENTICATED
    if isinstance(body, dict) and isinstance(body.get("detail"), dict):
        body = body["detail"]
    if isinstance(body, dict) and isinstance(body.get("code"), str):
        try:
            return GatewayErrorCode(body["code"])
        except ValueError:
            pass
    return GatewayErrorCode.UNAUTHENTICATED


def _gateway_error_message(response: httpx.Response) -> str:
    """Return a fixed caller-facing phrase for a failed identity preflight."""
    messages = {
        GatewayErrorCode.UNAUTHENTICATED: "the credential was not accepted",
        GatewayErrorCode.CREDENTIAL_EXPIRED: "the credential has expired",
        GatewayErrorCode.CLIENT_DISABLED: "the client is disabled",
    }
    return messages.get(_gateway_error_code(response), "the gateway refused the credential")


def _detail(response: httpx.Response) -> str | None:
    """Extract the API's own message, if it sent one in the documented shape."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        # FastAPI reports validation failures as a list of field errors; only
        # the human-readable half of each is worth passing on.
        messages = [str(item["msg"]) for item in detail if isinstance(item, dict) and "msg" in item]
        return "; ".join(messages) or None
    return None


def _is_revision_conflict(response: httpx.Response) -> bool:
    """Recognize only the documented Phase 7 conflict envelope."""
    if response.status_code != 409:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    error = body.get("error")
    return isinstance(error, dict) and error.get("code") == "REVISION_CONFLICT"


def _gateway_domain_error_code(response: httpx.Response) -> str | None:
    """Read the tiny error envelope used only by gateway control endpoints."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    if not isinstance(detail, dict):
        return None
    code = detail.get("code")
    return code if isinstance(code, str) else None


def _query(params: Mapping[str, object]) -> dict[str, str]:
    """Render query parameters, dropping the ones that were not set.

    Values are stringified here rather than passed through, so nothing but a
    scalar can ever reach a query string.
    """
    rendered: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            rendered[key] = "true" if value else "false"
        else:
            rendered[key] = str(value)
    return rendered
