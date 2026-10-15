"""Lifecycle management for small, credential-derived agent sessions."""

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Protocol
from uuid import uuid4

from app.models.agent import AgentClientType, AgentSessionDocument, AgentSessionStatus
from app.models.common import utc_now
from app.repositories.errors import ConcurrencyConflictError
from app.security.principal import Principal
from app.security.tenant_service import tenant_service

DEFAULT_SESSION_IDLE_TIMEOUT = timedelta(hours=8)


class AgentSessionNotFoundError(Exception):
    """The session is absent, or deliberately hidden from another client."""


class AgentSessionStateError(Exception):
    """The requested transition is invalid for the session's lifecycle state."""


class AgentSessionAccessError(Exception):
    """The credential has no registered client identity for session operations."""


class AgentSessionRepository(Protocol):
    """The narrow persistence operations session lifecycle needs."""

    async def create(self, document: AgentSessionDocument) -> AgentSessionDocument: ...

    async def get(self, session_id: str) -> AgentSessionDocument | None: ...

    async def update(
        self, document: AgentSessionDocument, *, expected_revision: int
    ) -> AgentSessionDocument: ...

    async def touch_active(self, session_id: str, client_id: str, seen_at: datetime) -> bool: ...


@tenant_service
class AgentSessionService:
    """Own sessions by authenticated client identity, never caller input.

    A session is deliberately small. It holds project/repository association
    and scalar diagnostic labels, never prompts, messages, or model context.
    Expiry is lazy: a session is marked expired when the gateway next sees it
    after its idle window, which needs no background worker or extra service.
    """

    def __init__(
        self,
        sessions: AgentSessionRepository,
        *,
        idle_timeout: timedelta = DEFAULT_SESSION_IDLE_TIMEOUT,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if idle_timeout <= timedelta(0):
            raise ValueError("session idle timeout must be positive")
        self._sessions = sessions
        self._idle_timeout = idle_timeout
        self._clock = clock

    async def begin(
        self,
        principal: Principal,
        *,
        project_id: str | None = None,
        repository: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AgentSessionDocument:
        """Start one session attributed entirely to the bearer-linked client."""
        client_id, client_type = _client_identity(principal)
        now = self._clock()
        return await self._sessions.create(
            AgentSessionDocument(
                session_id=uuid4().hex,
                client_id=client_id,
                client_type=AgentClientType(client_type),
                project_id=project_id,
                repository=repository,
                started_at=now,
                last_seen_at=now,
                metadata={} if metadata is None else metadata,
            )
        )

    async def get(
        self,
        principal: Principal,
        session_id: str,
        *,
        permit_admin: bool = True,
    ) -> AgentSessionDocument:
        """Return one visible session and lazily mark it expired when idle."""
        session = await self._load_visible(principal, session_id, permit_admin=permit_admin)
        return await self._expire_if_idle(session)

    async def update(
        self,
        principal: Principal,
        session_id: str,
        *,
        expected_revision: int,
        changes: Mapping[str, object],
    ) -> AgentSessionDocument:
        """Apply a revision-guarded explicit metadata or association update."""
        session = await self.get(principal, session_id)
        _require_active(session)
        allowed = {"project_id", "repository", "metadata"}
        if not changes or not set(changes).issubset(allowed):
            raise ValueError("at least one mutable session field is required")
        if session.revision != expected_revision:
            raise ConcurrencyConflictError("session changed since it was retrieved")
        now = _not_before(self._clock(), session.last_seen_at)
        values = dict(changes)
        values["last_seen_at"] = now
        return await self._sessions.update(
            session.model_copy(update=values), expected_revision=expected_revision
        )

    async def end(
        self,
        principal: Principal,
        session_id: str,
        *,
        expected_revision: int,
    ) -> AgentSessionDocument:
        """End an active session with the revision the caller reviewed."""
        session = await self.get(principal, session_id)
        _require_active(session)
        if session.revision != expected_revision:
            raise ConcurrencyConflictError("session changed since it was retrieved")
        ended_at = _not_before(self._clock(), session.last_seen_at)
        return await self._sessions.update(
            session.model_copy(
                update={
                    "last_seen_at": ended_at,
                    "ended_at": ended_at,
                    "status": AgentSessionStatus.ENDED,
                }
            ),
            expected_revision=expected_revision,
        )

    async def require_active_for_gateway(
        self,
        principal: Principal,
        session_id: str,
    ) -> AgentSessionDocument:
        """Verify a header-provided session belongs to this client and is live."""
        session = await self.get(principal, session_id, permit_admin=False)
        _require_active(session)
        seen_at = _not_before(self._clock(), session.last_seen_at)
        if not await self._sessions.touch_active(session.session_id, session.client_id, seen_at):
            # A concurrent end or expiry wins. Reload rather than retrying a
            # write that might revive a session another actor deliberately ended.
            session = await self.get(principal, session_id, permit_admin=False)
            _require_active(session)
        return session

    async def _load_visible(
        self,
        principal: Principal,
        session_id: str,
        *,
        permit_admin: bool,
    ) -> AgentSessionDocument:
        session = await self._sessions.get(session_id)
        if session is None:
            raise AgentSessionNotFoundError("agent session was not found")
        client_id, _ = _client_identity(principal)
        is_elevated = permit_admin and principal.allows_scope("agent:manage")
        if session.client_id != client_id and not is_elevated:
            # Do not confirm another client's operational history exists.
            raise AgentSessionNotFoundError("agent session was not found")
        return session

    async def _expire_if_idle(self, session: AgentSessionDocument) -> AgentSessionDocument:
        if session.status != AgentSessionStatus.ACTIVE:
            return session
        now = self._clock()
        if now - session.last_seen_at < self._idle_timeout:
            return session
        expired_at = _not_before(now, session.last_seen_at)
        expired = session.model_copy(
            update={"ended_at": expired_at, "status": AgentSessionStatus.EXPIRED}
        )
        try:
            return await self._sessions.update(expired, expected_revision=session.revision)
        except ConcurrencyConflictError as error:
            # Another explicit lifecycle write won. Reloading is safe; never
            # retry the expiry write against an unseen revision.
            current = await self._sessions.get(session.session_id)
            if current is None:
                raise AgentSessionNotFoundError("agent session was not found") from error
            return current


def _client_identity(principal: Principal) -> tuple[str, str]:
    """Require the bearer to identify one registered client, not a human key."""
    if principal.client_id is None or principal.client_type is None:
        raise AgentSessionAccessError("this credential is not linked to an agent client")
    return principal.client_id, principal.client_type


def _require_active(session: AgentSessionDocument) -> None:
    if session.status != AgentSessionStatus.ACTIVE:
        raise AgentSessionStateError("the agent session is no longer active")


def _not_before(candidate: datetime, floor: datetime) -> datetime:
    """Keep lifecycle timestamps monotonic under a controlled test clock."""
    return candidate if candidate >= floor else floor
