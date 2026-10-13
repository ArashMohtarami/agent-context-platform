"""V4 Phase 1 administrative client routes protect and redact credentials."""

from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes.agents import router
from app.models.agent import AgentClientDocument, AgentClientStatus
from app.models.common import Page
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError
from app.services.agent_client_service import AgentClientService
from app.services.api_key_service import ApiKeyService
from app.services.auth_service import AuthService
from tests.api_doubles import authenticated_application
from tests.security_doubles import FakeApiKeyRepository, bearer, issued_key


class Clients:
    def __init__(self) -> None:
        self.records: dict[str, AgentClientDocument] = {}

    async def create(self, document: AgentClientDocument) -> AgentClientDocument:
        if document.client_id in self.records:
            raise DuplicateDocumentError("duplicate")
        self.records[document.client_id] = document
        return document

    async def get(self, client_id: str) -> AgentClientDocument | None:
        return self.records.get(client_id)

    async def list(
        self,
        *,
        status: AgentClientStatus | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[AgentClientDocument]:
        del cursor
        values = self.records.values()
        items = tuple(item for item in values if status is None or item.status == status)
        return Page(items=items[:limit])

    async def update(
        self, document: AgentClientDocument, *, expected_revision: int
    ) -> AgentClientDocument:
        current = self.records.get(document.client_id)
        if current is None or current.revision != expected_revision:
            raise ConcurrencyConflictError("changed")
        stored = document.model_copy(update={"revision": expected_revision + 1})
        self.records[stored.client_id] = stored
        return stored

    async def touch(self, client_id: str, seen_at: datetime) -> None:
        record = self.records[client_id]
        self.records[client_id] = record.model_copy(update={"last_seen_at": seen_at})


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
async def test_agent_admin_routes_create_and_redact_a_one_time_credential() -> None:
    records = FakeApiKeyRepository()
    administrator, admin_token = issued_key(scopes=("agent:manage",))
    records.records[administrator.key_id] = administrator
    clients = Clients()
    service = AgentClientService(clients, ApiKeyService(records))
    app = authenticated_application(
        router,
        auth=AuthService(records, clients),
        agent_client_service=service,
    )
    headers = bearer(admin_token)
    body = {
        "client_id": "codex-main",
        "client_type": "codex",
        "name": "Codex",
        "owner_id": "operator",
        "scopes": ["search:read"],
        "project_ids": ["payments"],
    }

    created = await call(app, "POST", "/api/v4/clients", body, headers=headers)
    credential = await call(
        app,
        "POST",
        "/api/v4/clients/codex-main/credentials",
        {"expires_in_days": 30},
        headers=headers,
    )
    listed = await call(app, "GET", "/api/v4/clients", headers=headers)
    loaded = await call(app, "GET", "/api/v4/clients/codex-main", headers=headers)

    assert created.status_code == 201
    assert credential.status_code == 201
    assert credential.json()["token"].startswith("acp_")
    assert "key_hash" not in credential.json()
    assert listed.json()["items"][0]["client_id"] == "codex-main"
    assert "token" not in listed.text
    assert "token" not in loaded.text
    assert "key_hash" not in loaded.text


@pytest.mark.asyncio
async def test_agent_admin_routes_require_the_management_scope_and_validate_scopes() -> None:
    records = FakeApiKeyRepository()
    read_key, token = issued_key(scopes=("search:read",))
    administrator, admin_token = issued_key(scopes=("agent:manage",))
    records.records[read_key.key_id] = read_key
    records.records[administrator.key_id] = administrator
    clients = Clients()
    app = authenticated_application(
        router,
        auth=AuthService(records, clients),
        agent_client_service=AgentClientService(clients, ApiKeyService(records)),
    )
    body = {
        "client_id": "codex-main",
        "client_type": "codex",
        "name": "Codex",
        "owner_id": "operator",
        "scopes": ["typo:scope"],
    }

    forbidden = await call(app, "GET", "/api/v4/clients", headers=bearer(token))
    invalid = await call(app, "POST", "/api/v4/clients", body, headers=bearer(admin_token))

    assert forbidden.status_code == 403
    assert invalid.status_code == 422
