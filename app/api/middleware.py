"""Protections that belong to every request rather than to one endpoint.

Three things happen here that no handler should have to remember: an oversized
body is refused before it is read, every response carries the headers that tell
a browser not to treat it as something it is not, and an unexpected failure
answers with as little as possible.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Final

from pymongo.errors import PyMongoError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.status import (
    HTTP_409_CONFLICT,
    HTTP_411_LENGTH_REQUIRED,
    HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_503_SERVICE_UNAVAILABLE,
)

logger = logging.getLogger("agent_context.http")

# Generous for a context item and far below what would strain the process. The
# largest documented body is one context item, whose content is bounded at
# 200 000 characters by the model.
DEFAULT_MAX_REQUEST_BYTES: Final = 1024 * 1024

# This API returns JSON to programs, never HTML to a browser, so the headers
# that matter are the ones that stop a response being reinterpreted: no
# sniffing, no framing, no referrer, and a policy that permits nothing.
SECURITY_HEADERS: Final[dict[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; sandbox",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}
# Methods that carry a body worth bounding.
_BODIED: Final = frozenset({"POST", "PUT", "PATCH"})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add the response headers every answer should carry."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Answer the request, then state what the answer is not."""
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Refuse a body larger than this deployment accepts.

    Checked from `Content-Length` before the body is read, so an oversized
    request costs the process nothing. A bodied request that declares no length
    is refused rather than streamed: without a declared size there is nothing to
    check against, and this API has no endpoint that needs chunked upload.
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        maximum_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    ) -> None:
        super().__init__(app)
        if maximum_bytes < 1:
            raise ValueError("the maximum request size must be positive")
        self._maximum = maximum_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Check the declared size, then let the request through."""
        if request.method in _BODIED:
            declared = request.headers.get("content-length")
            if declared is None:
                return _refused(
                    HTTP_411_LENGTH_REQUIRED,
                    "a request body must declare its length",
                )
            else:
                try:
                    length = int(declared)
                except ValueError:
                    return _refused(
                        HTTP_411_LENGTH_REQUIRED,
                        "a request body must declare its length",
                    )
                if length > self._maximum:
                    return _refused(
                        HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"a request body may be at most {self._maximum} bytes",
                    )
        return await call_next(request)


async def unhandled_error(request: Request, exception: Exception) -> JSONResponse:
    """Answer an unexpected failure without describing it.

    Registered as the application's handler for `Exception` because the server
    generates that response outside every middleware, so without this it would
    be the one answer carrying neither the security headers nor the shape the
    rest of the API uses.

    The message is fixed. An exception's own text is written for a traceback
    and can name a host, a path, or a connection string, so it goes to the log
    — with the stack — and never to the caller.
    """
    logger.exception(
        "unhandled_request_error",
        exc_info=exception,
        extra={"http_method": request.method, "http_path": request.url.path},
    )
    return _refused(HTTP_500_INTERNAL_SERVER_ERROR, "the request could not be completed")


async def unavailable_dependency(request: Request, exception: Exception) -> JSONResponse:
    """Answer a database outage as an outage rather than as a defect.

    The retrieval endpoints already translate a failing search into 503. Every
    other endpoint reached the database directly, so an outage arrived as an
    unhandled exception and was answered 500 — which says the platform is
    broken when what is true is that a dependency is down. The difference
    matters to everything downstream: 503 is the status a client retries, a
    proxy takes a backend out of rotation for, and an operator reads as
    "MongoDB" rather than "a bug".

    The driver's message can name a host, a replica set, and a connection
    string, so only bounded classification fields reach logs or the caller.
    """
    transient = isinstance(exception, PyMongoError) and exception.has_error_label(
        "TransientTransactionError"
    )
    code = getattr(exception, "code", None)
    logger.warning(
        "database_transaction_conflict" if transient else "database_unavailable",
        extra={
            "http_method": request.method,
            "http_path": request.url.path,
            "error_type": type(exception).__name__,
            "error_code": code if isinstance(code, int) else None,
            "transient_transaction": transient,
        },
    )
    if transient:
        # The transaction aborted. No implicit replay of human review/issuance;
        # the caller must obtain current state and deliberately retry.
        return _refused(HTTP_409_CONFLICT, "concurrent state changed; reload before retrying")
    return _refused(HTTP_503_SERVICE_UNAVAILABLE, "the platform is temporarily unavailable")


def _refused(status_code: int, detail: str) -> JSONResponse:
    """Refuse a request in the shape every other failure takes."""
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=SECURITY_HEADERS,
    )
