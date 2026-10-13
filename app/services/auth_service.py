"""Turning a presented credential into a principal, or refusing to.

Every reason a key can fail — malformed, unknown, revoked, expired, wrong
secret — produces the same answer to the caller: the credential was not
accepted. Distinguishing them would tell someone probing the platform which
keys exist and which merely expired, so the distinction is kept for the audit
trail and the operator's log, where it belongs.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import NoReturn, Protocol, cast

from app.models.agent import AgentClientDocument
from app.models.api_key import ApiKeyDocument
from app.models.common import utc_now
from app.security.keys import MalformedKeyError, matches, parse
from app.security.principal import AuthenticationError, Principal
from app.security.scopes import ANY_PROJECT, Scope, canonical

# What every rejected credential is told, whatever was actually wrong with it.
REFUSED = "the API key was not accepted"

logger = logging.getLogger("agent_context.security")


class GatewayAuthenticationCode(StrEnum):
    """Gateway-specific failure detail, never used by ordinary API routes."""

    UNAUTHENTICATED = "UNAUTHENTICATED"
    CLIENT_DISABLED = "CLIENT_DISABLED"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"


class GatewayAuthenticationError(AuthenticationError):
    """An authentication failure the MCP gateway may report with a fixed code."""

    def __init__(self, code: GatewayAuthenticationCode) -> None:
        self.code = code
        super().__init__(code.value)


class ApiKeyReader(Protocol):
    """The key lookups authentication depends on."""

    async def get_by_key_id(self, key_id: str) -> ApiKeyDocument | None: ...

    async def touch(self, key_id: str, used_at: datetime) -> None: ...


class AgentClientReader(Protocol):
    async def get(self, client_id: str) -> AgentClientDocument | None: ...

    async def touch(self, client_id: str, seen_at: datetime) -> None: ...


class AuthService:
    """Authenticate a presented key against the issued ones."""

    def __init__(
        self,
        keys: ApiKeyReader,
        clients: AgentClientReader | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
        record_usage: bool = True,
    ) -> None:
        self._keys = keys
        self._clients = clients
        self._clock = clock
        self._record_usage = record_usage

    async def authenticate(self, credential: str | None) -> Principal:
        """Resolve one credential into a principal, or refuse it.

        The order matters: the credential is parsed before it is looked up, so
        a value that is not shaped like a key never becomes a query, and the
        secret is compared in constant time after the record is found, so the
        comparison cannot be timed to recover it.
        """
        return await self._authenticate(credential, gateway=False)

    async def authenticate_gateway(self, credential: str | None) -> Principal:
        """Authenticate for the internal gateway identity preflight.

        Normal API endpoints intentionally keep every credential failure
        indistinguishable.  The local MCP gateway has already received the
        bearer credential from its spawning client and needs only the fixed
        operational distinctions required to report expiry and client disable.
        """
        return await self._authenticate(credential, gateway=True)

    async def _authenticate(self, credential: str | None, *, gateway: bool) -> Principal:
        if not credential:
            self._raise_refused("missing", None, gateway=gateway)
        try:
            presented = parse(credential)
        except MalformedKeyError as error:
            self._raise_refused("malformed", None, gateway=gateway, cause=error)

        record = await self._keys.get_by_key_id(presented.key_id)
        if record is None:
            self._raise_refused("unknown", presented.key_id, gateway=gateway)
        if not matches(presented.secret, record.key_hash):
            self._raise_refused("secret mismatch", presented.key_id, gateway=gateway)
        if record.status != "active":
            self._raise_refused("revoked", presented.key_id, gateway=gateway)
        now = self._clock()
        if record.is_expired(now):
            self._raise_refused(
                "expired",
                presented.key_id,
                gateway=gateway,
                code=GatewayAuthenticationCode.CREDENTIAL_EXPIRED,
            )

        client: AgentClientDocument | None = None
        if record.client_id is not None:
            clients = self._clients
            if clients is None:
                self._raise_refused("client lookup unavailable", presented.key_id, gateway=gateway)
            client = await clients.get(record.client_id)
            if client is None or client.status != "active":
                self._raise_refused(
                    "client inactive",
                    presented.key_id,
                    gateway=gateway,
                    code=GatewayAuthenticationCode.CLIENT_DISABLED,
                )
        if self._record_usage:
            await self._keys.touch(record.key_id, now)
            if client is not None and clients is not None:
                await clients.touch(client.client_id, now)
        return principal_of(record, client)

    def _raise_refused(
        self,
        reason: str,
        key_id: str | None,
        *,
        gateway: bool,
        code: GatewayAuthenticationCode = GatewayAuthenticationCode.UNAUTHENTICATED,
        cause: Exception | None = None,
    ) -> NoReturn:
        """Log a reason privately and raise the correct public auth failure."""
        self._refused(reason, key_id)
        error: AuthenticationError
        if gateway:
            error = GatewayAuthenticationError(code)
        else:
            error = AuthenticationError(REFUSED)
        if cause is not None:
            raise error from cause
        raise error

    def _refused(self, reason: str, key_id: str | None) -> None:
        """Record why a credential failed, where only an operator can read it.

        The reason never reaches the caller. It names the key identifier, which
        is public, and never the secret or the stored hash.
        """
        logger.warning(
            "authentication_refused",
            extra={"key_id": key_id or "unparseable", "auth_reason": reason},
        )


def granted_scopes(record: ApiKeyDocument) -> tuple[Scope, ...]:
    """Return the scopes a stored key carries, in canonical order.

    The cast is safe because the document model refuses to store a scope this
    platform does not define, so anything read back is one of them.
    """
    return canonical(cast(list[Scope], record.scopes))


def principal_of(record: ApiKeyDocument, client: AgentClientDocument | None = None) -> Principal:
    """Describe one stored key as the caller it authorizes.

    A client scope reduction applies to every one of that client's previously
    issued credentials immediately.  A credential can narrow a client grant,
    but cannot retain a scope the client administrator subsequently removed.
    Legacy V1 keys have no client and therefore retain their original grant.
    """
    scopes = granted_scopes(record)
    if client is not None:
        scopes = canonical(scope for scope in scopes if scope in client.scopes)
    projects = tuple(record.projects) if record.projects else ANY_PROJECT
    if client is not None and client.project_ids:
        projects = (
            tuple(client.project_ids)
            if projects is None
            else tuple(project_id for project_id in projects if project_id in client.project_ids)
        )
    return Principal(
        key_id=record.key_id,
        name=record.name,
        scopes=scopes,
        # An empty allow-list is stored to mean "every project", because that
        # is what omitting the option at creation says.
        projects=projects,
        profiles=tuple(record.profiles) if record.profiles else None,
        client_id=record.client_id,
        client_type=client.client_type if client is not None else None,
    )
