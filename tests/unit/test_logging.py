"""Structured application and request logging tests."""

import json
import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.logging import (
    _STRUCTURED_FIELDS,
    JsonFormatter,
    RequestLoggingMiddleware,
    configure_logging,
)
from app.observability import context, instruments
from app.observability.instruments import REGISTRY


@pytest.fixture
def empty_registry() -> Iterator[None]:
    """Record into a registry no other test has already written to."""
    REGISTRY.reset()
    yield
    REGISTRY.reset()


def build_application() -> FastAPI:
    """Build an application whose routes are enough to log and measure."""
    application = FastAPI()
    application.add_middleware(RequestLoggingMiddleware)

    @application.get("/probe")
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    @application.get("/projects/{project_id}/context")
    async def project_context(project_id: str) -> dict[str, str]:
        return {"project_id": project_id}

    @application.get("/annotated")
    async def annotated() -> dict[str, bool]:
        context.annotate(operation="search_knowledge", query_id="q-1", result_count=4)
        return {"ok": True}

    @application.get("/broken")
    async def broken() -> dict[str, bool]:
        raise RuntimeError("the password is hunter2")

    return application


def client_for(application: FastAPI) -> AsyncClient:
    """Build a client against one application."""
    return AsyncClient(transport=ASGITransport(app=application), base_url="http://test")


def test_json_formatter_emits_allowlisted_structured_fields() -> None:
    record = logging.LogRecord(
        name="agent_context.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="http_request_completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "request-123"
    record.http_method = "GET"
    record.http_path = "/health/live"
    record.status_code = 200
    record.duration_ms = 1.25

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "agent_context.test"
    assert payload["message"] == "http_request_completed"
    assert payload["request_id"] == "request-123"
    assert payload["http_method"] == "GET"
    assert payload["http_path"] == "/health/live"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 1.25
    assert "timestamp" in payload


def test_configure_logging_uses_json_stdout_and_disables_duplicate_access_log() -> None:
    with (
        patch("app.core.logging.dictConfig") as configure,
        patch("app.core.logging.logging.captureWarnings") as capture_warnings,
        patch("app.core.logging.logging.getLogger") as get_logger,
    ):
        configure_logging("DEBUG")

    config = configure.call_args.args[0]
    assert config["root"] == {"handlers": ["stdout"], "level": "DEBUG"}
    assert config["handlers"]["stdout"]["stream"] == "ext://sys.stdout"
    assert config["formatters"]["json"]["()"] is JsonFormatter
    assert config["loggers"]["uvicorn.access"]["handlers"] == []
    get_logger.assert_called_once_with("uvicorn.access")
    assert get_logger.return_value.disabled is True
    capture_warnings.assert_called_once_with(True)


async def test_failed_startup_reports_safe_type_and_code_without_exception_contents() -> None:
    from pymongo.errors import OperationFailure

    from app.core.config import Settings
    from app.main import create_app

    private_detail = "fictional-password-must-not-appear-in-the-startup-event"
    failure = OperationFailure("context " * 2000 + private_detail, code=85)
    mongodb = Mock(connect=AsyncMock(side_effect=failure))
    with (
        patch("app.main.configure_logging"),
        patch("app.main.MongoDBClient", return_value=mongodb),
        patch("app.main.logger") as startup_logger,
    ):
        application = create_app(
            Settings(
                _env_file=None,
                mongodb_uri="mongodb://startup-unit.invalid:27017",
                mongodb_database="startup_unit",
                mongodb_username="fixture",
                mongodb_password="unused-fictional-unit-password",
                embedding_enabled=False,
            )
        )
        with pytest.raises(OperationFailure) as raised:
            async with application.router.lifespan_context(application):
                pytest.fail("failed startup must never yield a ready application")
    assert raised.value is failure
    startup_logger.error.assert_called_once_with(
        "application_startup_failed",
        extra={"component": "startup", "error_type": "OperationFailure", "error_code": 85},
    )
    event = startup_logger.error.call_args
    record = logging.LogRecord(
        "agent_context.startup", logging.ERROR, __file__, 1, event.args[0], (), None
    )
    for key, value in event.kwargs["extra"].items():
        setattr(record, key, value)
    assert private_detail not in JsonFormatter().format(record)


@pytest.mark.asyncio
async def test_request_logging_adds_request_id_and_omits_query_string() -> None:
    with patch("app.core.logging.logger") as request_logger:
        async with client_for(build_application()) as client:
            response = await client.get("/probe?token=must-not-be-logged")

    assert response.status_code == 200
    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32
    request_logger.info.assert_called_once()
    event = request_logger.info.call_args
    assert event.args == ("http_request_completed",)
    assert event.kwargs["extra"]["request_id"] == request_id
    assert event.kwargs["extra"]["http_method"] == "GET"
    assert event.kwargs["extra"]["http_path"] == "/probe"
    assert event.kwargs["extra"]["status_code"] == 200
    assert "token" not in event.kwargs["extra"]


# --- correlation -----------------------------------------------------------


@pytest.mark.asyncio
async def test_an_identifier_a_caller_supplies_is_the_one_that_is_logged() -> None:
    # This is what closes the loop between processes: the MCP server sends the
    # identifier of the tool call it is serving, and the API logs the same one,
    # so one operation reads as one operation in two logs.
    with patch("app.core.logging.logger") as request_logger:
        async with client_for(build_application()) as client:
            response = await client.get("/probe", headers={"X-Request-ID": "mcp-call-0001"})

    assert response.headers["X-Request-ID"] == "mcp-call-0001"
    assert request_logger.info.call_args.kwargs["extra"]["request_id"] == "mcp-call-0001"


@pytest.mark.asyncio
async def test_an_unusable_supplied_identifier_is_replaced() -> None:
    # A request identifier is repeated on every line the call writes, so one
    # that could be mistaken for structure is refused rather than carried.
    with patch("app.core.logging.logger") as request_logger:
        async with client_for(build_application()) as client:
            response = await client.get("/probe", headers={"X-Request-ID": 'a" b="c'})

    logged = request_logger.info.call_args.kwargs["extra"]["request_id"]
    assert logged == response.headers["X-Request-ID"]
    assert len(logged) == 32


@pytest.mark.asyncio
async def test_the_access_line_carries_the_operation_and_the_matched_route() -> None:
    with patch("app.core.logging.logger") as request_logger:
        async with client_for(build_application()) as client:
            await client.get("/projects/payment-service/context")

    logged = request_logger.info.call_args.kwargs["extra"]
    assert logged["operation"] == "project_context"
    assert logged["http_route"] == "/projects/{project_id}/context"
    assert logged["http_path"] == "/projects/payment-service/context"
    assert logged["project_id"] == "payment-service"


@pytest.mark.asyncio
async def test_the_access_line_carries_what_the_call_turned_out_to_be_about() -> None:
    # One line, not a join: the identifier, what ran, which query it was, and
    # how much came back.
    with patch("app.core.logging.logger") as request_logger:
        async with client_for(build_application()) as client:
            await client.get("/annotated")

    logged = request_logger.info.call_args.kwargs["extra"]
    assert logged["operation"] == "search_knowledge"
    assert logged["query_id"] == "q-1"
    assert logged["result_count"] == 4
    assert logged["duration_ms"] >= 0.0


@pytest.mark.asyncio
async def test_a_call_leaves_no_correlation_behind_it() -> None:
    async with client_for(build_application()) as client:
        await client.get("/probe")

    assert context.current() is None


@pytest.mark.asyncio
async def test_a_failing_request_is_still_logged_with_its_identifier() -> None:
    application = build_application()

    with patch("app.core.logging.logger") as request_logger:
        async with client_for(application) as client:
            with pytest.raises(RuntimeError):
                await client.get("/broken")

    logged = request_logger.exception.call_args.kwargs["extra"]
    assert logged["status_code"] == 500
    assert len(logged["request_id"]) == 32


# --- request metrics -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_request_is_counted_against_the_route_template(
    empty_registry: None,
) -> None:
    async with client_for(build_application()) as client:
        await client.get("/projects/one/context")
        await client.get("/projects/two/context")

    # One series for both projects. Counting by path would make a project
    # identifier a metric label, which is both unbounded and nobody's business.
    assert (
        REGISTRY.value(
            instruments.HTTP_REQUESTS,
            method="GET",
            route="/projects/{project_id}/context",
            status="200",
        )
        == 2
    )


@pytest.mark.asyncio
async def test_a_request_matching_no_route_cannot_invent_a_series(
    empty_registry: None,
) -> None:
    # Otherwise anyone who can reach the port could create as many series as
    # they cared to type.
    async with client_for(build_application()) as client:
        await client.get("/nope")
        await client.get("/also-nope")

    assert (
        REGISTRY.value(
            instruments.HTTP_REQUESTS,
            method="GET",
            route=instruments.UNMATCHED_ROUTE,
            status="404",
        )
        == 2
    )


@pytest.mark.asyncio
async def test_a_built_in_route_is_named_rather_than_called_unmatched(
    empty_registry: None,
) -> None:
    # The schema and the docs pages are plain Starlette routes, so the router
    # records the endpoint they matched but no route object. They did match
    # something, and counting them as unmatched would say otherwise.
    async with client_for(build_application()) as client:
        await client.get("/openapi.json")

    assert (
        REGISTRY.value(instruments.HTTP_REQUESTS, method="GET", route="openapi", status="200") == 1
    )
    assert (
        REGISTRY.value(
            instruments.HTTP_REQUESTS,
            method="GET",
            route=instruments.UNMATCHED_ROUTE,
            status="200",
        )
        is None
    )


@pytest.mark.asyncio
async def test_request_latency_is_recorded_per_route(empty_registry: None) -> None:
    async with client_for(build_application()) as client:
        await client.get("/probe")

    measured = REGISTRY.summary(instruments.HTTP_DURATION, route="/probe")
    assert measured is not None
    count, total, _ = measured
    assert count == 1
    assert total >= 0.0


# --- what can never be logged ----------------------------------------------


def test_a_record_carrying_a_secret_cannot_get_it_serialized() -> None:
    # The allow-list is the enforcement, not the convention: a handler that
    # attaches a credential to a record simply cannot have it written out,
    # whatever it called the attribute.
    record = logging.LogRecord(
        name="agent_context.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="something_happened",
        args=(),
        exc_info=None,
    )
    record.api_key = "acp_0123456789abcdef_supersecret"
    record.password = "hunter2"
    record.authorization = "Bearer acp_0123456789abcdef_supersecret"
    record.query = "what is our refund policy?"
    record.content = "the retrieved document body"
    record.request_id = "request-123"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["request_id"] == "request-123"
    for forbidden in ("api_key", "password", "authorization", "query", "content"):
        assert forbidden not in payload
    assert "supersecret" not in json.dumps(payload)


def test_the_allowlist_contains_no_field_that_could_carry_content() -> None:
    forbidden = {
        "query",
        "content",
        "body",
        "document",
        "documents",
        "text",
        "password",
        "secret",
        "token",
        "api_key",
        "key_hash",
        "authorization",
        "credential",
    }

    assert not forbidden.intersection(_STRUCTURED_FIELDS)


def test_a_line_written_inside_a_call_carries_that_calls_identifier() -> None:
    record = logging.LogRecord(
        name="agent_context.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="something_happened",
        args=(),
        exc_info=None,
    )

    with context.correlated("call-0001"):
        payload = json.loads(JsonFormatter().format(record))

    assert payload["request_id"] == "call-0001"


def test_json_formatter_redacts_credentials_from_messages_fields_and_exceptions() -> None:
    record = logging.LogRecord(
        name="agent_context.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=(
            "fetch failed https://operator:uri-secret@example.test/private "
            "Authorization: Bearer bearer-secret"
        ),
        args=(),
        exc_info=None,
    )
    record.retrieval_error = "password=field-secret api_key=second-secret"
    exception_token = "".join(("exception", "-", "secret"))
    private_key_body = "".join(("private", "-", "key"))
    private_key_header = "-----BEGIN " + "PRIVATE KEY-----"
    try:
        raise RuntimeError(
            f"token={exception_token}\n{private_key_header}\n{private_key_body}\n"
            "-----END PRIVATE KEY-----"
        )
    except RuntimeError:
        import sys

        record.exc_info = sys.exc_info()

    rendered = JsonFormatter().format(record)
    payload = json.loads(rendered)

    for secret in (
        "uri-secret",
        "bearer-secret",
        "field-secret",
        "second-secret",
        "exception-secret",
        "private-key",
    ):
        assert secret not in rendered
    assert payload["message"].count("[REDACTED]") >= 2
    assert "[REDACTED]" in payload["retrieval_error"]
    assert "[REDACTED" in payload["exception"]
