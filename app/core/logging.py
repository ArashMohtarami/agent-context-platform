"""Credential-safe structured application and HTTP request logging."""

import json
import logging
import re
from contextvars import Token
from datetime import UTC, datetime
from logging.config import dictConfig
from time import perf_counter
from typing import TextIO

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app import provenance
from app.core.config import LogLevel
from app.observability import context
from app.observability.instruments import UNMATCHED_ROUTE, record_http_request

# Only these credential-free fields are ever serialized from a log record. The
# retrieval fields are identifiers and counters on purpose: a search's query
# text and the content it returned are never among them, so shipping these logs
# never ships what somebody asked or what the platform answered.
#
# The security fields are the same bargain. `key_id` is the public half of a
# credential and is what makes a refusal traceable to a key; `auth_reason` is
# one word from a fixed vocabulary this codebase writes. The secret, the stored
# hash, and the Authorization header are not here and are never logged, so a
# shipped log says which key was refused and why, and never how to become it.
#
# Adding a field here is the only way one reaches a log line, which is what
# makes the rule enforceable rather than a convention: a handler that attaches
# an API key, a password, a query, or a retrieved document to a record cannot
# get it serialized, whatever it called the attribute.
_STRUCTURED_FIELDS = (
    "request_id",
    "operation",
    "component",
    "outcome",
    "http_method",
    "http_path",
    "http_route",
    "status_code",
    "duration_ms",
    "db_command",
    "mcp_tool",
    "error_type",
    "embedding_model",
    "embedding_dimensions",
    "embedding_device",
    "embedding_error",
    "query_id",
    "project_id",
    "retrieval_method",
    "requested_sources",
    "result_count",
    "latency_ms",
    "retrieval_error",
    "key_id",
    "auth_reason",
    "auth_scope",
    "client_id",
    "client_type",
    "session_id",
)

logger = logging.getLogger("agent_context.http")

_MAX_LOG_TEXT_CHARS = 8_000
_REDACTION_PATTERNS = (
    # Agent Context bearer values can appear in an exception without an
    # ``Authorization`` or ``token=`` label (for example, in a third-party
    # transport diagnostic). Redact the complete recognizable credential
    # shape before any log record or traceback is serialized.
    (
        re.compile(r"\bacp_[0-9a-f]{16}_[A-Za-z0-9_-]{32,}\b"),
        "[REDACTED AGENT CONTEXT TOKEN]",
    ),
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^@\s/]+@"), r"\1[REDACTED]@"),
    (re.compile(r"(?i)\b(bearer)\s+[a-z0-9._~+/=-]+"), r"\1 [REDACTED]"),
    (
        re.compile(
            r"(?i)\b(password|secret|api[_-]?key|token|authorization|credential|code|state|nonce|"
            r"code_verifier|client_secret|id_token|refresh_token|access_token|cookie)"
            r"\s*([=:])\s*([^\s,;]+)"
        ),
        r"\1\2[REDACTED]",
    ),
    (
        re.compile(
            r"(?s)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
        ),
        "[REDACTED PRIVATE KEY]",
    ),
)


def _redact_log_text(value: str) -> str:
    """Keep error diagnostics useful without shipping credential-bearing text."""
    redacted = value
    for pattern, replacement in _REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    if len(redacted) > _MAX_LOG_TEXT_CHARS:
        return f"{redacted[:_MAX_LOG_TEXT_CHARS]}…[truncated]"
    return redacted


def _safe_log_value(value: object) -> object:
    if isinstance(value, str):
        return _redact_log_text(value)
    if isinstance(value, list):
        return [_safe_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_log_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_log_value(item) for key, item in value.items()}
    return value


class JsonFormatter(logging.Formatter):
    """Serialize safe, selected log-record fields as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_log_text(record.getMessage()),
        }

        # Every line written inside a call carries that call's identifier
        # without the caller passing one, which is what makes a log grep-able
        # by request rather than only by time.
        contextual_request_id = context.request_id()
        if contextual_request_id is not None and not hasattr(record, "request_id"):
            payload["request_id"] = contextual_request_id

        for field_name in _STRUCTURED_FIELDS:
            if hasattr(record, field_name):
                payload[field_name] = _safe_log_value(getattr(record, field_name))

        if record.exc_info is not None:
            payload["exception"] = _redact_log_text(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: LogLevel, stream: TextIO | None = None) -> None:
    """Configure application and server logs as structured JSON on one stream.

    Logs go to standard output by default. A process whose standard output
    carries a protocol — the MCP server speaks JSON-RPC over stdio — passes
    standard error instead, because a log line written into that stream would
    corrupt the conversation rather than describe it.
    """
    handler: dict[str, object] = {"class": "logging.StreamHandler", "formatter": "json"}
    if stream is None:
        handler["stream"] = "ext://sys.stdout"
    else:
        handler["stream"] = stream
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {"stdout": handler},
            "root": {"handlers": ["stdout"], "level": level},
            "loggers": {
                "uvicorn": {"handlers": ["stdout"], "level": level, "propagate": False},
                "uvicorn.error": {
                    "handlers": ["stdout"],
                    "level": level,
                    "propagate": False,
                },
                # The request middleware emits the access event with a request ID.
                "uvicorn.access": {"handlers": [], "level": level, "propagate": False},
            },
        }
    )
    logging.getLogger("uvicorn.access").disabled = True
    logging.captureWarnings(True)


def _matched_route(request: Request) -> tuple[str | None, str | None]:
    """Return the template and name of the route that answered, if one did.

    Read after the request has been handled, because that is when the router
    has recorded what it matched. The template is what a metric is labelled
    with: `/projects/{project_id}/context` is one series whatever the project,
    where the path itself would be one series per project.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    name = getattr(route, "name", None)
    if template is not None:
        return template, name
    # The application's own built-in routes — the OpenAPI schema and the docs
    # pages — are plain Starlette routes, which record the endpoint they
    # matched but no route object. Naming them by that endpoint keeps them
    # apart from a request that matched nothing at all, and an endpoint
    # function is as bounded a thing to label with as a route template.
    endpoint = getattr(request.scope.get("endpoint"), "__name__", None)
    return endpoint, name or endpoint


def _path_project(request: Request) -> str | None:
    """Return the project a route named in its path, if it named one."""
    parameters = request.scope.get("path_params")
    if not isinstance(parameters, dict):
        return None
    project_id = parameters.get("project_id")
    return project_id if isinstance(project_id, str) else None


def _request_fields(
    request: Request,
    status_code: int,
    started_at: float,
) -> dict[str, object]:
    """Build the allowlisted, credential-free HTTP request log context.

    The correlation fields come from the call itself — whatever a handler or a
    service learned along the way — so one access line carries the identifier,
    the operation, the project, the query, and how much came back, and an
    operator needs one line rather than a join.
    """
    template, name = _matched_route(request)
    fields: dict[str, object] = {
        **context.fields(),
        "http_method": request.method,
        "http_path": request.url.path,
        "status_code": status_code,
        "duration_ms": round((perf_counter() - started_at) * 1_000, 3),
    }
    if template is not None:
        fields["http_route"] = template
    if name is not None:
        fields.setdefault("operation", name)
    return fields


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Emit exactly one structured access event for each HTTP request.

    The identifier is minted here unless the caller sent an acceptable one, so
    an MCP tool call and the API request it became share an identifier and can
    be read as the single operation they are.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        token: Token[context.Correlation | None] = context.begin(
            request.headers.get(context.HEADER)
        )
        actor_token = provenance.begin()
        correlation = context.current_or_fail()
        # Also on the request, because the audit trail records the identifier
        # of the request it is describing and reaches it from the handler.
        request.state.request_id = correlation.request_id
        # The project a route names is known only once the router has matched,
        # which happens inside `call_next`, so it is read on the way back out.
        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            context.annotate(project_id=_path_project(request))
            self._measure(request, 500, started_at)
            logger.exception(
                "http_request_failed",
                extra=_request_fields(request, 500, started_at),
            )
            raise
        else:
            context.annotate(project_id=_path_project(request))
            response.headers[context.HEADER] = correlation.request_id
            self._measure(request, response.status_code, started_at)
            logger.info(
                "http_request_completed",
                extra=_request_fields(request, response.status_code, started_at),
            )
            return response
        finally:
            provenance.end(actor_token)
            context.end(token)

    @staticmethod
    def _measure(request: Request, status_code: int, started_at: float) -> None:
        """Count the request against the route it matched, or against none.

        A request that matched no route is counted as `unmatched` rather than
        by its path: labelling by the path would let anyone who can reach the
        port create as many series as they cared to type.
        """
        template, _ = _matched_route(request)
        record_http_request(
            method=request.method,
            route=template or UNMATCHED_ROUTE,
            status_code=status_code,
            seconds=perf_counter() - started_at,
        )
