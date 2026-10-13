"""What happens to every request, whichever endpoint it was going to reach.

A body too large is refused before it is read, and every answer says what it is
not — both are properties of the deployment rather than of any handler, which
is why they are tested against the assembled application.
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.api.middleware import (
    DEFAULT_MAX_REQUEST_BYTES,
    SECURITY_HEADERS,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    unhandled_error,
)

router = APIRouter()


class Echo(BaseModel):
    """One body, accepted so the size limit has something to guard."""

    content: str


@router.post("/echo")
async def echo(request: Echo) -> dict[str, int]:
    """Report how much arrived."""
    return {"length": len(request.content)}


@router.get("/read")
async def read() -> dict[str, bool]:
    """Answer without a body."""
    return {"ok": True}


@router.get("/fails")
async def fails() -> dict[str, bool]:
    """Fail, so an unexpected failure's answer can be checked too."""
    raise ValueError("mongodb://application_user:hunter2@mongodb:27017/agent_context")


def application(*, maximum_bytes: int = DEFAULT_MAX_REQUEST_BYTES) -> FastAPI:
    """Build an application wrapped the way the real one is."""
    built = FastAPI()
    built.include_router(router)
    built.add_exception_handler(Exception, unhandled_error)
    built.add_middleware(SecurityHeadersMiddleware)
    built.add_middleware(RequestSizeLimitMiddleware, maximum_bytes=maximum_bytes)
    return built


async def call(
    built: FastAPI,
    method: str,
    path: str,
    *,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    raising: bool = True,
) -> Any:
    """Send one request, optionally with a hand-built body."""
    transport = ASGITransport(app=built, raise_app_exceptions=raising)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, content=content, headers=headers)


@pytest.mark.asyncio
async def test_every_answer_states_what_it_is_not() -> None:
    response = await call(application(), "GET", "/read")

    assert response.status_code == 200
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


@pytest.mark.asyncio
async def test_the_headers_say_this_is_never_a_page() -> None:
    # This API answers programs in JSON. Everything here exists to stop a
    # response being reinterpreted as something a browser would run or frame.
    response = await call(application(), "GET", "/read")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_knowledge_is_never_stored_by_a_cache() -> None:
    # A retrieval answer is somebody's project knowledge; a shared cache
    # holding it would hand it to the next caller.
    response = await call(application(), "GET", "/read")

    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_a_refusal_carries_the_headers_too() -> None:
    built = application(maximum_bytes=100)

    response = await call(built, "POST", "/echo", content=b"x" * 200)

    assert response.status_code == 413
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


@pytest.mark.asyncio
async def test_an_unexpected_failure_carries_the_headers_too() -> None:
    response = await call(application(), "GET", "/fails", raising=False)

    assert response.status_code == 500
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


@pytest.mark.asyncio
async def test_an_unexpected_failure_describes_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="agent_context.http"):
        response = await call(application(), "GET", "/fails", raising=False)

    body = response.text
    assert response.json() == {"detail": "the request could not be completed"}
    # An exception's text is written for a traceback and can name a host, a
    # path, or a connection string. It goes to the log, not to the caller.
    assert "mongodb://" not in body
    assert "hunter2" not in body
    assert "ValueError" not in body
    assert "Traceback" not in body
    # The operator still gets the whole thing, where it belongs.
    assert "hunter2" in caplog.text
    assert "Traceback" in caplog.text


@pytest.mark.asyncio
async def test_a_body_within_the_limit_is_served() -> None:
    built = application(maximum_bytes=10_000)

    response = await call(
        built,
        "POST",
        "/echo",
        content=b'{"content": "' + b"x" * 100 + b'"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"length": 100}


@pytest.mark.asyncio
async def test_a_body_over_the_limit_is_refused_without_being_read() -> None:
    built = application(maximum_bytes=64)

    response = await call(
        built,
        "POST",
        "/echo",
        content=b'{"content": "' + b"x" * 5_000 + b'"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "a request body may be at most 64 bytes"}


@pytest.mark.asyncio
async def test_the_limit_is_what_the_deployment_configured() -> None:
    small = await call(
        application(maximum_bytes=32),
        "POST",
        "/echo",
        content=b"x" * 100,
        headers={"content-type": "application/json"},
    )
    large = await call(
        application(maximum_bytes=1_000),
        "POST",
        "/echo",
        content=b'{"content": "' + b"x" * 100 + b'"}',
        headers={"content-type": "application/json"},
    )

    assert small.status_code == 413
    assert large.status_code == 200


@pytest.mark.asyncio
async def test_a_streamed_body_of_undeclared_length_is_refused() -> None:
    # Without a declared size there is nothing to check against, and no
    # endpoint here needs a streamed upload. Sent as an async iterator, which
    # is what makes httpx chunk it and omit Content-Length — handing it the
    # header by hand does not, since it sets a real length anyway.
    async def streamed() -> AsyncIterator[bytes]:
        yield b'{"content": "x"}'

    built = application(maximum_bytes=64)
    transport = ASGITransport(app=built)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/echo", content=streamed(), headers={"content-type": "application/json"}
        )

    assert response.request.headers.get("transfer-encoding") == "chunked"
    assert "content-length" not in response.request.headers
    assert response.status_code == 411
    assert response.json() == {"detail": "a request body must declare its length"}


@pytest.mark.asyncio
async def test_a_nonsense_content_length_is_refused() -> None:
    response = await call(
        application(),
        "POST",
        "/echo",
        content=b"{}",
        headers={"content-type": "application/json", "content-length": "not-a-number"},
    )

    assert response.status_code == 411


@pytest.mark.asyncio
async def test_a_request_without_a_body_is_never_size_checked() -> None:
    response = await call(application(maximum_bytes=1), "GET", "/read")

    assert response.status_code == 200


def test_a_limit_that_could_not_be_enforced_is_refused() -> None:
    with pytest.raises(ValueError, match="positive"):
        RequestSizeLimitMiddleware(FastAPI(), maximum_bytes=0)


def test_the_default_limit_admits_the_largest_documented_item() -> None:
    # One context item's content is bounded at 200 000 characters by the model,
    # so the transport limit has to be above it or the two would disagree.
    assert DEFAULT_MAX_REQUEST_BYTES > 200_000
