"""V4 Phase 6 activity visibility and redaction contracts."""

from datetime import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.agent_activity import router
from app.models.agent import AgentActivityDocument, AgentClientDocument
from app.models.common import Page
from app.services.agent_activity_service import AgentActivityService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from tests.security_doubles import (
    NOW,
    FakeApiKeyRepository,
    FakeAuditRepository,
    bearer,
    issued_key,
)

PROJECT = "payment-service"
OTHER_PROJECT = "wallet-service"


class Clients:
    def __init__(self, *items: AgentClientDocument) -> None:
        self.items = {item.client_id: item for item in items}

    async def get(self, client_id: str) -> AgentClientDocument | None:
        return self.items.get(client_id)

    async def touch(self, client_id: str, seen_at: datetime) -> None:
        _ = client_id, seen_at


class Activities:
    def __init__(self, *items: AgentActivityDocument) -> None:
        self.items = list(items)
        self.last_filters: dict[str, object] = {}

    async def create(self, document: AgentActivityDocument) -> AgentActivityDocument:
        self.items.append(document)
        return document

    async def list(self, **filters: object) -> Page[AgentActivityDocument]:
        self.last_filters = filters
        items = self.items
        client_id = filters["client_id"]
        if client_id is not None:
            items = [item for item in items if item.client_id == client_id]
        project_id = filters["project_id"]
        if project_id is not None:
            items = [item for item in items if item.project_id == project_id]
        else:
            project_ids = filters["project_ids"]
            if project_ids is not None:
                allowed = set(project_ids)  # type: ignore[arg-type]
                items = [
                    item for item in items if item.project_id is None or item.project_id in allowed
                ]
        limit = filters["limit"]
        return Page(items=tuple(items[:limit]))  # type: ignore[index]


def client(client_id: str, client_type: str, projects: list[str]) -> AgentClientDocument:
    return AgentClientDocument(
        org_id="org-test",
        project_mode="include" if projects else "all_in_org",
        client_id=client_id,
        client_type=client_type,
        name=client_id,
        owner_id="owner",
        scopes=["audit:self:read"],
        project_ids=projects,
    )


def event(client_id: str, client_type: str, project_id: str | None) -> AgentActivityDocument:
    return AgentActivityDocument(
        event_id=f"{client_id}-{project_id or 'global'}",
        client_id=client_id,
        client_type=client_type,
        session_id="session-1",
        operation="save_context",
        tool="save_context",
        project_id=project_id,
        resource_type="context",
        resource_id="context-1",
        status="succeeded",
    )


def application(record: object, clients: Clients, activities: Activities) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.auth = AuthService(FakeApiKeyRepository(record), clients, clock=lambda: NOW)
    app.state.audit = AuditService(FakeAuditRepository())
    app.state.agent_activity_service = AgentActivityService(activities)
    return app


async def request(app: FastAPI, path: str, token: str) -> object:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        return await http.get(path, headers=bearer(token))


@pytest.mark.asyncio
async def test_self_audit_reads_only_the_authenticated_client_and_allowed_projects() -> None:
    record, token = issued_key(scopes=("audit:self:read",), projects=(PROJECT,))
    record = record.model_copy(update={"client_id": "claude-code-main"})
    activities = Activities(
        event("claude-code-main", "claude_code", PROJECT),
        event("claude-code-main", "claude_code", OTHER_PROJECT),
        event("claude-code-main", "claude_code", None),
        event("codex-main", "codex", PROJECT),
    )
    app = application(
        record,
        Clients(
            client("claude-code-main", "claude_code", [PROJECT]),
            client("codex-main", "codex", [PROJECT]),
        ),
        activities,
    )

    response = await request(app, "/api/v4/agent-activity?limit=20", token)

    assert response.status_code == 200  # type: ignore[attr-defined]
    payload = response.json()  # type: ignore[attr-defined]
    assert [item["event_id"] for item in payload["items"]] == [
        "claude-code-main-payment-service",
        "claude-code-main-global",
    ]
    assert activities.last_filters["client_id"] == "claude-code-main"
    assert activities.last_filters["project_ids"] == (PROJECT,)
    assert token not in str(payload)


@pytest.mark.asyncio
async def test_self_audit_cannot_select_another_client() -> None:
    record, token = issued_key(scopes=("audit:self:read",), projects=(PROJECT,))
    record = record.model_copy(update={"client_id": "claude-code-main"})
    activities = Activities(event("codex-main", "codex", PROJECT))
    app = application(
        record,
        Clients(
            client("claude-code-main", "claude_code", [PROJECT]),
            client("codex-main", "codex", [PROJECT]),
        ),
        activities,
    )

    response = await request(app, "/api/v4/agent-activity?client_id=codex-main", token)

    assert response.status_code == 403  # type: ignore[attr-defined]
    assert "codex-main" not in response.text  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_admin_can_filter_activity_for_another_client() -> None:
    record, token = issued_key(scopes=("agent:manage",), projects=(PROJECT,))
    activities = Activities(event("codex-main", "codex", PROJECT))
    app = application(record, Clients(client("codex-main", "codex", [PROJECT])), activities)

    response = await request(app, "/api/v4/agent-activity?client_id=codex-main", token)

    assert response.status_code == 200  # type: ignore[attr-defined]
    assert response.json()["items"][0]["client_id"] == "codex-main"  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing domain behavior uses an explicit test organization."""
    yield
