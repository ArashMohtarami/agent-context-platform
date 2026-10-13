"""The narrow Context API contract consumed by the MCP gateway."""

from datetime import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.gateway import router
from app.models.agent import AgentActivityDocument, AgentClientDocument, AgentSessionDocument
from app.repositories.errors import ConcurrencyConflictError
from app.services.agent_activity_service import AgentActivityService
from app.services.agent_session_service import AgentSessionService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from tests.security_doubles import (
    NOW,
    FakeApiKeyRepository,
    FakeAuditRepository,
    bearer,
    expired_key,
    issued_key,
)


class Clients:
    def __init__(self, *items: AgentClientDocument) -> None:
        self.items = {item.client_id: item for item in items}

    async def get(self, client_id: str) -> AgentClientDocument | None:
        return self.items.get(client_id)

    async def touch(self, client_id: str, seen_at: datetime) -> None:
        _ = client_id, seen_at


class Activities:
    def __init__(self) -> None:
        self.items: list[AgentActivityDocument] = []

    async def create(self, document: AgentActivityDocument) -> AgentActivityDocument:
        self.items.append(document)
        return document


class Sessions:
    """Minimal lifecycle repository for the gateway router contract."""

    def __init__(self, *items: AgentSessionDocument) -> None:
        self.items = {item.session_id: item for item in items}

    async def create(self, document: AgentSessionDocument) -> AgentSessionDocument:
        self.items[document.session_id] = document
        return document

    async def get(self, session_id: str) -> AgentSessionDocument | None:
        return self.items.get(session_id)

    async def update(
        self, document: AgentSessionDocument, *, expected_revision: int
    ) -> AgentSessionDocument:
        current = self.items.get(document.session_id)
        if current is None or current.revision != expected_revision:
            raise ConcurrencyConflictError("stale session")
        stored = document.model_copy(update={"revision": expected_revision + 1})
        self.items[document.session_id] = stored
        return stored

    async def touch_active(self, session_id: str, client_id: str, seen_at: datetime) -> bool:
        session = self.items.get(session_id)
        if session is None or session.client_id != client_id or session.status != "active":
            return False
        self.items[session_id] = session.model_copy(
            update={"last_seen_at": max(seen_at, session.last_seen_at)}
        )
        return True


def client(
    client_id: str = "codex-main",
    *,
    status: str = "active",
    scopes: list[str] | None = None,
) -> AgentClientDocument:
    return AgentClientDocument(
        client_id=client_id,
        client_type="codex",
        name="Codex",
        status=status,
        owner_id="owner",
        scopes=["search:read"] if scopes is None else scopes,
        project_ids=["sampleco-payment"],
    )


def application(record: object, clients: Clients, activities: Activities) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.auth = AuthService(FakeApiKeyRepository(record), clients, clock=lambda: NOW)
    app.state.audit = AuditService(FakeAuditRepository())
    app.state.agent_activity_service = AgentActivityService(activities)
    app.state.agent_session_service = AgentSessionService(
        Sessions(
            AgentSessionDocument(
                session_id="codex-session-1",
                client_id="codex-main",
                client_type="codex",
                started_at=NOW,
                last_seen_at=NOW,
            )
        ),
        clock=lambda: NOW,
    )
    return app


async def request(
    app: FastAPI,
    method: str,
    path: str,
    token: str,
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> object:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        return await http.request(
            method,
            path,
            headers={**bearer(token), **(headers or {})},
            json=body,
        )


@pytest.mark.asyncio
async def test_gateway_identity_and_activity_are_derived_from_the_client_link() -> None:
    record, token = issued_key(scopes=("search:read",), projects=("sampleco-payment",))
    record = record.model_copy(update={"client_id": "codex-main"})
    activities = Activities()
    app = application(record, Clients(client()), activities)

    identity = await request(app, "GET", "/api/v4/gateway/identity", token)
    activity = await request(
        app,
        "POST",
        "/api/v4/gateway/activity",
        token,
        {
            "operation": "search_knowledge",
            "tool": "search_knowledge",
            "project_id": "sampleco-payment",
            "resource_type": "context",
            "resource_id": "context-1",
            "status": "succeeded",
            "latency_ms": 12.5,
        },
        headers={
            "X-Agent-Context-Client-ID": "codex-main",
            "X-Agent-Context-Client-Type": "codex",
            "X-Agent-Context-Session-ID": "codex-session-1",
        },
    )

    assert identity.status_code == 200  # type: ignore[attr-defined]
    assert identity.json() == {  # type: ignore[attr-defined]
        "client_id": "codex-main",
        "client_type": "codex",
        "scopes": ["search:read"],
        "allowed_project_ids": ["sampleco-payment"],
    }
    assert activity.status_code == 204  # type: ignore[attr-defined]
    assert len(activities.items) == 1
    event = activities.items[0]
    assert event.client_id == "codex-main"
    assert event.client_type == "codex"
    assert event.operation == "search_knowledge"
    assert event.tool == "search_knowledge"
    assert event.session_id == "codex-session-1"
    assert event.resource_type == "context"
    assert event.resource_id == "context-1"


@pytest.mark.asyncio
async def test_gateway_rejects_spoofed_client_scope_and_session_metadata() -> None:
    record, token = issued_key(scopes=("search:read",), projects=("sampleco-payment",))
    record = record.model_copy(update={"client_id": "codex-main"})
    activities = Activities()
    app = application(record, Clients(client()), activities)

    identity = await request(
        app,
        "GET",
        "/api/v4/gateway/identity",
        token,
        headers={
            "X-Agent-Context-Client-ID": "claude-code-main",
            "X-Agent-Context-Client-Type": "claude_code",
            "X-Agent-Context-Scopes": "agent:manage,research:promote",
        },
    )
    activity = await request(
        app,
        "POST",
        "/api/v4/gateway/activity",
        token,
        {
            "operation": "search_knowledge",
            "tool": "search_knowledge",
            "project_id": "sampleco-payment",
            "status": "succeeded",
            "latency_ms": 1.0,
        },
        headers={
            "X-Agent-Context-Client-ID": "claude-code-main",
            "X-Agent-Context-Client-Type": "claude_code",
            "X-Agent-Context-Session-ID": "codex-session-1",
        },
    )
    forged_session = await request(
        app,
        "POST",
        "/api/v4/gateway/activity",
        token,
        {
            "operation": "search_knowledge",
            "tool": "search_knowledge",
            "project_id": "sampleco-payment",
            "status": "succeeded",
            "latency_ms": 1.0,
        },
        headers={
            "X-Agent-Context-Client-ID": "codex-main",
            "X-Agent-Context-Client-Type": "codex",
            "X-Agent-Context-Session-ID": "not-a-real-session",
        },
    )

    assert identity.status_code == 200  # type: ignore[attr-defined]
    assert identity.json()["client_id"] == "codex-main"  # type: ignore[attr-defined]
    assert identity.json()["scopes"] == ["search:read"]  # type: ignore[attr-defined]
    assert activity.status_code == 204  # type: ignore[attr-defined]
    assert activities.items[0].client_id == "codex-main"
    assert activities.items[0].session_id is None
    assert forged_session.status_code == 403  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_gateway_sessions_are_owned_by_the_credential_and_use_revisions() -> None:
    scopes = ("agent:self", "session:write")
    record, token = issued_key(scopes=scopes, projects=("sampleco-payment",))
    record = record.model_copy(update={"client_id": "codex-main"})
    app = application(record, Clients(client(scopes=list(scopes))), Activities())

    created = await request(
        app,
        "POST",
        "/api/v4/gateway/sessions",
        token,
        {
            "project_id": "sampleco-payment",
            "repository": "git@github.com:example/payment-service.git",
            "metadata": {"workspace": "payments"},
        },
    )
    assert created.status_code == 201  # type: ignore[attr-defined]
    payload = created.json()  # type: ignore[attr-defined]
    assert payload["client_id"] == "codex-main"
    assert payload["revision"] == 1
    assert "conversation" not in payload

    loaded = await request(
        app,
        "GET",
        f"/api/v4/gateway/sessions/{payload['session_id']}",
        token,
    )
    updated = await request(
        app,
        "PATCH",
        f"/api/v4/gateway/sessions/{payload['session_id']}",
        token,
        {"expected_revision": payload["revision"], "metadata": {"workspace": "payments-v2"}},
    )
    stale = await request(
        app,
        "POST",
        f"/api/v4/gateway/sessions/{payload['session_id']}/end",
        token,
        {"expected_revision": payload["revision"]},
    )

    assert loaded.status_code == 200  # type: ignore[attr-defined]
    assert updated.status_code == 200  # type: ignore[attr-defined]
    assert updated.json()["revision"] == 2  # type: ignore[attr-defined]
    assert stale.status_code == 409  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("record_factory", "client_status", "code"),
    [
        (lambda: expired_key(), "active", "CREDENTIAL_EXPIRED"),
        (lambda: issued_key(status="revoked"), "active", "UNAUTHENTICATED"),
        (lambda: issued_key(), "disabled", "CLIENT_DISABLED"),
    ],
)
async def test_gateway_identity_uses_the_fixed_authentication_error_codes(
    record_factory: object, client_status: str, code: str
) -> None:
    record, token = record_factory()  # type: ignore[operator]
    record = record.model_copy(update={"client_id": "codex-main"})
    app = application(record, Clients(client(status=client_status)), Activities())

    response = await request(app, "GET", "/api/v4/gateway/identity", token)

    assert response.status_code in {401, 403}  # type: ignore[attr-defined]
    assert response.json() == {"detail": {"code": code}}  # type: ignore[attr-defined]
