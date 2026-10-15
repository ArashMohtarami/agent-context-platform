"""V4 Phase 1 client-linked credentials retain legacy key compatibility."""

from datetime import datetime

import pytest

from app.models.agent import AgentClientDocument
from app.security.principal import AuthenticationError
from app.services.auth_service import REFUSED, AuthService
from tests.security_doubles import FakeApiKeyRepository, issued_key


class Clients:
    def __init__(self, *clients: AgentClientDocument) -> None:
        self.records = {client.client_id: client for client in clients}
        self.touched: list[str] = []

    async def get(self, client_id: str) -> AgentClientDocument | None:
        return self.records.get(client_id)

    async def touch(self, client_id: str, seen_at: datetime) -> None:
        self.touched.append(client_id)


def client(client_id: str, *, status: str = "active") -> AgentClientDocument:
    return AgentClientDocument(
        org_id="org-test",
        project_mode="all_in_org",
        client_id=client_id,
        client_type="codex",
        name=client_id,
        status=status,
        owner_id="owner",
        scopes=["search:read"],
    )


@pytest.mark.asyncio
async def test_client_credential_authenticates_and_updates_client_last_seen() -> None:
    record, token = issued_key(scopes=("search:read",))
    record = record.model_copy(update={"client_id": "codex-main"})
    clients = Clients(client("codex-main"))

    principal = await AuthService(FakeApiKeyRepository(record), clients).authenticate(token)

    assert principal.client_id == "codex-main"
    assert clients.touched == ["codex-main"]


@pytest.mark.asyncio
async def test_disabled_client_is_refused_without_affecting_legacy_credentials() -> None:
    record, token = issued_key(scopes=("search:read",))
    linked = record.model_copy(update={"client_id": "claude-main"})
    auth = AuthService(
        FakeApiKeyRepository(linked), Clients(client("claude-main", status="disabled"))
    )

    with pytest.raises(AuthenticationError, match=REFUSED):
        await auth.authenticate(token)

    legacy = await AuthService(FakeApiKeyRepository(record)).authenticate(token)
    assert legacy.client_id is None
