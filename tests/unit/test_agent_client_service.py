"""V4 Phase 1 client identity and existing-key credential behaviour."""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.models.agent import AgentClientDocument, AgentClientStatus
from app.models.common import Page, utc_now
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError
from app.security.principal import AuthenticationError
from app.services.agent_client_service import AgentClientService
from app.services.api_key_service import ApiKeyService
from app.services.auth_service import AuthService
from tests.security_doubles import FakeApiKeyRepository


class Clients:
    """In-memory client persistence with the production revision contract."""

    def __init__(self) -> None:
        self.records: dict[str, AgentClientDocument] = {}
        self.touched: list[str] = []

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
        items = tuple(
            item
            for item in sorted(self.records.values(), key=lambda value: value.client_id)
            if status is None or item.status == status
        )
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
        del seen_at
        self.touched.append(client_id)


def service(
    *, now: datetime | None = None
) -> tuple[AgentClientService, Clients, FakeApiKeyRepository]:
    clients = Clients()
    keys = FakeApiKeyRepository()
    clock = (lambda: now) if now is not None else utc_now
    return AgentClientService(clients, ApiKeyService(keys, clock=clock)), clients, keys


@pytest.mark.asyncio
async def test_credentials_are_hashed_and_isolated_between_codex_and_claude() -> None:
    agents, clients, keys = service()
    await agents.create_client(
        client_id="codex-main",
        client_type="codex",
        name="Codex",
        owner_id="operator",
        scopes=["search:read", "context:read"],
        project_ids=["payments"],
    )
    await agents.create_client(
        client_id="claude-main",
        client_type="claude_code",
        name="Claude Code",
        owner_id="operator",
        scopes=["search:read"],
        project_ids=["wallet"],
    )

    codex = await agents.create_credential("codex-main")
    claude = await agents.create_credential("claude-main")

    assert codex.token != claude.token
    assert codex.token not in codex.record.key_hash
    assert claude.token not in claude.record.key_hash
    assert codex.record.client_id == "codex-main"
    assert claude.record.client_id == "claude-main"
    assert codex.record.projects == ["payments"]
    assert claude.record.projects == ["wallet"]

    auth = AuthService(keys, clients)
    codex_principal = await auth.authenticate(codex.token)
    claude_principal = await auth.authenticate(claude.token)
    assert codex_principal.client_id == "codex-main"
    assert codex_principal.allows_project("payments")
    assert not codex_principal.allows_project("wallet")
    assert claude_principal.client_id == "claude-main"

    await agents.update_client(
        "codex-main",
        expected_revision=1,
        scopes=["search:read"],
    )
    narrowed = await auth.authenticate(codex.token)
    assert narrowed.allows_scope("search:read")
    assert not narrowed.allows_scope("context:read")

    await agents.revoke_client("claude-main")

    assert keys.records[claude.record.key_id].status == "revoked"
    assert keys.records[claude.record.key_id].revoked_at is not None
    with pytest.raises(AuthenticationError):
        await auth.authenticate(claude.token)
    assert (await auth.authenticate(codex.token)).client_id == "codex-main"


@pytest.mark.asyncio
async def test_client_credentials_enforce_expiration_disabled_clients_and_grants() -> None:
    now = utc_now()
    agents, clients, keys = service(now=now)
    await agents.create_client(
        client_id="codex-main",
        client_type="codex",
        name="Codex",
        owner_id="operator",
        scopes=["search:read"],
        project_ids=["payments"],
    )

    with pytest.raises(ValueError, match="credential scopes"):
        await agents.create_credential("codex-main", scopes=["context:read"])
    with pytest.raises(ValueError, match="credential projects"):
        await agents.create_credential("codex-main", projects=["wallet"])

    issued = await agents.create_credential("codex-main", expires_in_days=1)
    expired = AuthService(keys, clients, clock=lambda: now + timedelta(days=2))
    with pytest.raises(AuthenticationError):
        await expired.authenticate(issued.token)

    active = AuthService(keys, clients, clock=lambda: now)
    assert (await active.authenticate(issued.token)).client_id == "codex-main"
    clients.records["codex-main"] = clients.records["codex-main"].model_copy(
        update={"status": "disabled"}
    )
    with pytest.raises(AuthenticationError):
        await active.authenticate(issued.token)


def test_client_scope_validation_rejects_unknown_vocabulary() -> None:
    with pytest.raises(ValidationError, match="unknown scopes"):
        AgentClientDocument(
            client_id="codex-main",
            client_type="codex",
            name="Codex",
            owner_id="operator",
            scopes=["not:a:scope"],
        )
