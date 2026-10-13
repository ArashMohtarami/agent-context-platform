"""V4 Phase 9 client-derived lifecycle and isolation behaviour."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.agent import AgentSessionDocument, AgentSessionStatus
from app.repositories.errors import ConcurrencyConflictError
from app.security.principal import Principal
from app.security.scopes import canonical
from app.services.agent_session_service import (
    AgentSessionAccessError,
    AgentSessionNotFoundError,
    AgentSessionService,
    AgentSessionStateError,
)

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


class Sessions:
    """Revision-aware in-memory lifecycle persistence double."""

    def __init__(self) -> None:
        self.items: dict[str, AgentSessionDocument] = {}

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
            raise ConcurrencyConflictError("session changed")
        stored = document.model_copy(update={"revision": expected_revision + 1})
        self.items[document.session_id] = stored
        return stored

    async def touch_active(self, session_id: str, client_id: str, seen_at: datetime) -> bool:
        current = self.items.get(session_id)
        if (
            current is None
            or current.client_id != client_id
            or current.status is not AgentSessionStatus.ACTIVE
        ):
            return False
        self.items[session_id] = current.model_copy(
            update={"last_seen_at": max(current.last_seen_at, seen_at)}
        )
        return True


def principal(
    client_id: str = "claude-code-main",
    client_type: str = "claude_code",
    *,
    scopes: tuple[str, ...] = ("agent:self", "session:write"),
) -> Principal:
    return Principal(
        key_id=f"key-{client_id}",
        name=client_id,
        scopes=canonical(scopes),  # type: ignore[arg-type]
        projects=("payment-service",),
        client_id=client_id,
        client_type=client_type,
    )


@pytest.mark.asyncio
async def test_session_lifecycle_is_credential_derived_revision_guarded_and_content_free() -> None:
    repository = Sessions()
    clock = [NOW]
    service = AgentSessionService(repository, clock=lambda: clock[0])

    created = await service.begin(
        principal(),
        project_id="payment-service",
        repository="git@github.com:example/payment-service.git",
        metadata={"client_version": "1.2.3", "workspace": "payments"},
    )
    clock[0] += timedelta(minutes=2)
    updated = await service.update(
        principal(),
        created.session_id,
        expected_revision=created.revision,
        changes={"repository": "https://github.com/example/payment-service.git"},
    )
    clock[0] += timedelta(minutes=1)
    ended = await service.end(
        principal(),
        updated.session_id,
        expected_revision=updated.revision,
    )

    assert created.client_id == "claude-code-main"
    assert created.client_type == "claude_code"
    assert "conversation" not in created.model_dump()
    assert updated.revision == 2
    assert ended.revision == 3
    assert ended.status is AgentSessionStatus.ENDED
    assert ended.ended_at == clock[0]
    with pytest.raises(AgentSessionStateError):
        await service.require_active_for_gateway(principal(), ended.session_id)


@pytest.mark.asyncio
async def test_session_expires_lazily_and_is_never_revived_by_a_gateway_heartbeat() -> None:
    repository = Sessions()
    clock = [NOW]
    service = AgentSessionService(
        repository,
        idle_timeout=timedelta(hours=1),
        clock=lambda: clock[0],
    )
    created = await service.begin(principal())
    clock[0] += timedelta(hours=1)

    expired = await service.get(principal(), created.session_id)

    assert expired.status is AgentSessionStatus.EXPIRED
    assert expired.ended_at == clock[0]
    with pytest.raises(AgentSessionStateError):
        await service.require_active_for_gateway(principal(), created.session_id)


@pytest.mark.asyncio
async def test_session_is_invisible_to_another_client_without_agent_management_scope() -> None:
    repository = Sessions()
    service = AgentSessionService(repository, clock=lambda: NOW)
    created = await service.begin(principal())

    with pytest.raises(AgentSessionNotFoundError):
        await service.get(principal("codex-main", "codex"), created.session_id)


@pytest.mark.asyncio
async def test_legacy_key_cannot_create_or_attach_an_agent_session() -> None:
    service = AgentSessionService(Sessions(), clock=lambda: NOW)
    legacy = Principal(key_id="legacy", name="legacy", scopes=canonical(("session:write",)))

    with pytest.raises(AgentSessionAccessError):
        await service.begin(legacy)
