"""The metrics endpoint: who may read it, what it says, and what it costs."""

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes.metrics import router as metrics_router
from app.observability import instruments
from app.observability.instruments import REGISTRY
from app.observability.metrics import CONTENT_TYPE
from app.security.scopes import Scope
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from tests.api_doubles import authenticated_application, open_application
from tests.security_doubles import NOW, FakeApiKeyRepository, bearer, issued_key


@pytest.fixture(autouse=True)
def empty_registry() -> Iterator[None]:
    """Read a registry no other test has already written to."""
    REGISTRY.reset()
    yield
    REGISTRY.reset()


def client_for(application: object) -> AsyncClient:
    """Build a client against one application."""
    return AsyncClient(transport=ASGITransport(app=application), base_url="http://test")  # type: ignore[arg-type]


def open_platform(*, metrics_enabled: bool = True) -> AsyncClient:
    """Build a client against a deployment that answers without a credential."""
    directory = TemporaryDirectory()
    path = Path(directory.name) / "operator-token"
    path.write_text("operator-test-token-at-least-32-characters")
    application = open_application(
        metrics_router,
        settings=SimpleNamespace(metrics_enabled=metrics_enabled, operator_metrics_token_file=path),
    )
    client = client_for(application)
    client.headers["Authorization"] = "Bearer operator-test-token-at-least-32-characters"
    application.state.test_token_directory = directory
    return client


def guarded(*scopes: Scope) -> tuple[AsyncClient, str]:
    """Build a client against a deployment requiring a key with these scopes."""
    record, token = issued_key(scopes=scopes)
    application = authenticated_application(
        metrics_router,
        auth=AuthService(FakeApiKeyRepository(record), clock=lambda: NOW),
        audit=AuditService(None),
        settings=SimpleNamespace(metrics_enabled=True, operator_metrics_token_file=None),
    )
    return client_for(application), token


@pytest.mark.asyncio
async def test_metrics_render_in_the_format_a_scraper_parses() -> None:
    instruments.record_http_request(method="GET", route="/search", status_code=200, seconds=0.02)

    async with open_platform() as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE
    lines = response.text.splitlines()
    assert f"# TYPE {instruments.HTTP_REQUESTS} counter" in lines
    assert 'acp_http_requests_total{method="GET",route="/search",status="200"} 1' in lines


@pytest.mark.asyncio
async def test_a_deployment_that_publishes_nothing_answers_404() -> None:
    # Rather than an empty body: a scraper pointed at a deployment with metrics
    # switched off should fail visibly, not record that all is quiet.
    async with open_platform(metrics_enabled=False) as client:
        response = await client.get("/metrics")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reading_metrics_requires_a_credential() -> None:
    client, _ = guarded("system:read")
    async with client:
        refused = await client.get("/metrics")

    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_tenant_search_scope_does_not_grant_raw_shared_metrics() -> None:
    # A key that can search must not thereby be able to read how the platform
    # is doing: operational data describes a deployment, not its knowledge.
    client, token = guarded("search:read")
    async with client:
        refused = await client.get("/metrics", headers=bearer(token))

    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_tenant_system_read_scope_does_not_grant_raw_shared_metrics() -> None:
    client, token = guarded("system:read")
    async with client:
        response = await client.get("/metrics", headers=bearer(token))

    assert response.status_code == 403
    assert "installation operator" in response.text


@pytest.mark.asyncio
async def test_a_scrape_runs_no_dependency_checks() -> None:
    # Monitoring must not be on the critical path of the thing it monitors, so
    # the readiness gauges are whatever `/health/ready` last wrote and a scrape
    # touches no database.
    async with open_platform() as client:
        response = await client.get("/metrics")

    assert instruments.DEPENDENCY_READY in response.text
    assert REGISTRY.value(instruments.DEPENDENCY_READY, dependency="mongodb") is None


@pytest.mark.asyncio
async def test_the_body_carries_no_credential_and_no_knowledge() -> None:
    client = open_platform()
    token = "operator-test-token-at-least-32-characters"
    instruments.record_mcp_call(tool="search_knowledge", outcome="ok", seconds=0.1)
    instruments.record_memory_operation(operation="create")

    async with client:
        body = (await client.get("/metrics", headers=bearer(token))).text

    assert token not in body
    assert "Bearer" not in body
    assert "password" not in body.lower()
    # And nothing anybody asked or anything the platform answered with.
    assert "content" not in body.lower()
