"""The only way out: what the API client will and will not do."""

import httpx
import pytest

from app.mcp.client import MAX_RESPONSE_BYTES, ContextApiClient
from app.mcp.errors import UnavailableError, UpstreamError
from tests.mcp_doubles import RecordedApi, api_client


@pytest.mark.asyncio
async def test_a_successful_call_returns_the_decoded_body() -> None:
    api = RecordedApi()
    api.respond("GET", "/api/v1/projects", {"items": [], "next_cursor": None})
    client = api_client(api)

    try:
        body = await client.request("GET", "/api/v1/projects")
    finally:
        await client.close()

    assert body == {"items": [], "next_cursor": None}


@pytest.mark.asyncio
async def test_query_parameters_are_rendered_as_scalars_only() -> None:
    api = RecordedApi()
    api.respond("GET", "/api/v1/skills", {"items": []})
    client = api_client(api)

    try:
        await client.request(
            "GET",
            "/api/v1/skills",
            params={"limit": 5, "active": True, "archived": False, "absent": None},
        )
    finally:
        await client.close()

    # Nothing but a scalar can reach a query string, so no argument can smuggle
    # a structure into one.
    assert api.query() == {"limit": "5", "active": "true", "archived": "false"}


@pytest.mark.asyncio
async def test_a_relative_path_is_refused() -> None:
    client = api_client(RecordedApi())

    try:
        with pytest.raises(ValueError, match="absolute"):
            await client.request("GET", "api/v1/skills")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_refusal_carries_the_platform_s_own_message() -> None:
    api = RecordedApi()
    api.respond("GET", "/api/v1/projects/x", {"detail": "project was not found"}, status_code=404)
    client = api_client(api)

    try:
        with pytest.raises(UpstreamError) as failure:
            await client.request("GET", "/api/v1/projects/x")
    finally:
        await client.close()

    assert failure.value.status_code == 404
    assert "project was not found" in str(failure.value)


@pytest.mark.asyncio
async def test_field_errors_are_summarized_without_their_internals() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v1/search",
        {
            "detail": [
                {"type": "value_error", "loc": ["body", "limit"], "msg": "too large", "ctx": {}},
                {"type": "missing", "loc": ["body", "query"], "msg": "field required"},
            ]
        },
        status_code=422,
    )
    client = api_client(api)

    try:
        with pytest.raises(UpstreamError) as failure:
            await client.request("POST", "/api/v1/search", json={})
    finally:
        await client.close()

    reported = str(failure.value)
    assert "too large" in reported
    assert "field required" in reported
    assert "value_error" not in reported


@pytest.mark.asyncio
async def test_an_unreachable_platform_names_no_host() -> None:
    client = api_client(RecordedApi(error=httpx.ConnectError("[Errno 111] to 10.0.0.4:8000")))

    try:
        with pytest.raises(UnavailableError) as failure:
            await client.request("GET", "/api/v1/projects")
    finally:
        await client.close()

    assert "unreachable" in str(failure.value)
    assert "10.0.0.4" not in str(failure.value)


@pytest.mark.asyncio
async def test_an_unreadable_body_is_reported_rather_than_parsed() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    client = api_client(responder)

    try:
        with pytest.raises(UnavailableError, match="unreadable"):
            await client.request("GET", "/api/v1/projects")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_body_that_is_not_an_object_is_refused() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    client = api_client(responder)

    try:
        with pytest.raises(UnavailableError, match="unexpected"):
            await client.request("GET", "/api/v1/projects")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_oversized_response_cannot_exhaust_this_process() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))

    client = api_client(responder)

    try:
        with pytest.raises(UnavailableError, match="more than this server accepts"):
            await client.request("GET", "/api/v1/projects")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_readiness_is_probed_without_raising_on_a_failing_platform() -> None:
    api = RecordedApi()
    api.respond("GET", "/health/ready", {"status": "not_ready"}, status_code=503)
    client = api_client(api)

    try:
        status_code, body = await client.probe("/health/ready")
    finally:
        await client.close()

    assert status_code == 503
    assert body == {"status": "not_ready"}


@pytest.mark.asyncio
async def test_no_token_means_no_authorization_header() -> None:
    api = RecordedApi()
    api.respond("GET", "/api/v1/projects", {"items": []})
    client = api_client(api)

    try:
        await client.request("GET", "/api/v1/projects")
    finally:
        await client.close()

    assert "authorization" not in api.last.headers


@pytest.mark.asyncio
async def test_ambient_proxy_settings_are_not_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `socksio` is deliberately not a dependency. More importantly, an
    # explicit self-hosted API address must not be routed through whatever
    # proxy happened to be exported by the process that launched Codex.
    monkeypatch.setenv("HTTP_PROXY", "socks5://proxy.invalid:1080")
    monkeypatch.setenv("HTTPS_PROXY", "socks5://proxy.invalid:1080")
    monkeypatch.setenv("ALL_PROXY", "socks5://proxy.invalid:1080")

    client = ContextApiClient("http://127.0.0.1:8000")
    await client.close()


@pytest.mark.parametrize("base_url", ["", "   "])
def test_a_client_without_an_address_is_refused(base_url: str) -> None:
    with pytest.raises(ValueError, match="base URL"):
        ContextApiClient(base_url.strip())


@pytest.mark.parametrize("timeout", [0.0, -1.0])
def test_a_client_without_a_timeout_is_refused(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        ContextApiClient("http://api.invalid", timeout_seconds=timeout)
