"""V5 Phase 0 administrative client-profile API tests."""

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes.client_profiles import router
from app.models.agent import AgentClientDocument
from app.services.auth_service import AuthService
from app.services.client_profile_service import ClientProfileService
from tests.api_doubles import authenticated_application
from tests.security_doubles import FakeApiKeyRepository, bearer, issued_key
from tests.unit.test_client_profiles import Clients, Profiles


async def call(
    app: Any,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, json=body, headers=headers)


@pytest.mark.asyncio
async def test_client_profile_api_requires_management_scope_and_assigns_a_snapshot() -> None:
    records = FakeApiKeyRepository()
    administrator, token = issued_key(scopes=("agent:manage",))
    records.records[administrator.key_id] = administrator
    client = AgentClientDocument(
        client_id="gemini-cli-main",
        client_type="gemini_cli",
        name="Gemini CLI",
        owner_id="operator",
        scopes=["search:read"],
        project_ids=["payments", "ledger"],
    )
    clients = Clients(client)
    profiles = ClientProfileService(Profiles(), clients)
    app = authenticated_application(
        router,
        auth=AuthService(records, clients),
        client_profile_service=profiles,
    )
    headers = bearer(token)
    body = {
        "profile_id": "coding-agent-default",
        "name": "Default Coding Agent",
        "description": "Generic coding client",
        "scopes": ["projects:read", "search:read"],
        "rate_limit_profile": "standard",
    }

    created = await call(app, "POST", "/api/v5/client-profiles", body, headers=headers)
    assigned = await call(
        app,
        "POST",
        "/api/v5/client-profiles/coding-agent-default/assignments",
        {
            "client_id": "gemini-cli-main",
            "expected_revision": 1,
            "project_overrides": ["payments"],
        },
        headers=headers,
    )
    forbidden = await call(app, "GET", "/api/v5/client-profiles", headers=bearer("invalid"))

    assert created.status_code == 201
    assert "token" not in created.text
    assert assigned.status_code == 200
    assert assigned.json() == {
        "client_id": "gemini-cli-main",
        "profile_id": "coding-agent-default",
        "scopes": ["projects:read", "search:read"],
        "project_ids": ["payments"],
        "revision": 2,
    }
    assert forbidden.status_code == 401


@pytest.mark.asyncio
async def test_client_profile_api_returns_revision_conflicts_and_refuses_scope_escalation() -> None:
    records = FakeApiKeyRepository()
    administrator, token = issued_key(scopes=("agent:manage",))
    records.records[administrator.key_id] = administrator
    clients = Clients(
        AgentClientDocument(
            client_id="gemini-cli-main",
            client_type="gemini_cli",
            name="Gemini CLI",
            owner_id="operator",
        )
    )
    profiles = ClientProfileService(Profiles(), clients)
    await profiles.create_profile(
        profile_id="readonly",
        name="Read only",
        scopes=["search:read"],
    )
    app = authenticated_application(
        router,
        auth=AuthService(records, clients),
        client_profile_service=profiles,
    )
    headers = bearer(token)

    escalated = await call(
        app,
        "POST",
        "/api/v5/client-profiles/readonly/assignments",
        {
            "client_id": "gemini-cli-main",
            "expected_revision": 1,
            "scope_overrides": ["research:promote"],
        },
        headers=headers,
    )
    stale = await call(
        app,
        "POST",
        "/api/v5/client-profiles/readonly/assignments",
        {"client_id": "gemini-cli-main", "expected_revision": 2},
        headers=headers,
    )

    assert escalated.status_code == 422
    assert stale.status_code == 409
