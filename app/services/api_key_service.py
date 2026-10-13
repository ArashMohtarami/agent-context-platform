"""Issuing, listing, and revoking API keys.

Minting a credential is an operator's act, not an API operation: there is no
endpoint for it, so there is no bootstrap problem and no way for a key to widen
itself by creating another. This is what `contextctl keys` drives.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.models.api_key import ApiKeyDocument, KeyStatus
from app.models.common import Page, utc_now
from app.security.keys import mint
from app.security.scopes import Scope, canonical, parse_scopes


class ApiKeyNotFoundError(Exception):
    """No key with that identifier exists."""


class ApiKeyRepository(Protocol):
    """The key operations issuing depends on."""

    async def create(self, document: ApiKeyDocument) -> ApiKeyDocument: ...

    async def get_by_key_id(self, key_id: str) -> ApiKeyDocument | None: ...

    async def list(
        self,
        *,
        status: KeyStatus | None = ...,
        client_id: str | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[ApiKeyDocument]: ...

    async def update(
        self,
        document: ApiKeyDocument,
        *,
        expected_revision: int,
    ) -> ApiKeyDocument: ...


@dataclass(frozen=True, slots=True)
class IssuedKey:
    """A key that has just been created.

    `token` exists here and nowhere else. It is returned so it can be shown
    once, and the platform keeps only the hash — so this is the only moment the
    usable credential exists outside the holder's hands.
    """

    record: ApiKeyDocument
    token: str


class ApiKeyService:
    """Create, list, and revoke the credentials the API accepts."""

    def __init__(
        self,
        keys: ApiKeyRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._keys = keys
        self._clock = clock

    async def create_key(
        self,
        *,
        name: str,
        scopes: Sequence[str],
        projects: Sequence[str] = (),
        profiles: Sequence[str] = (),
        client_id: str | None = None,
        expires_in_days: int | None = None,
    ) -> IssuedKey:
        """Issue one key, returning its secret exactly once.

        Naming no project grants every project, which is the widest grant and
        therefore the one an operator has to choose by leaving the option out
        rather than one they fall into.
        """
        granted = self._validated(scopes)
        now = self._clock()
        expires_at = self._expiry(now, expires_in_days)
        minted = mint()
        record = await self._keys.create(
            ApiKeyDocument(
                key_id=minted.key_id,
                client_id=client_id,
                key_hash=minted.key_hash,
                name=name,
                scopes=list(granted),
                projects=list(projects),
                profiles=list(profiles),
                status="active",
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )
        )
        return IssuedKey(record=record, token=minted.token)

    async def list_keys(
        self,
        *,
        status: KeyStatus | None = None,
        client_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ApiKeyDocument]:
        """List issued keys, never their secrets."""
        return await self._keys.list(status=status, client_id=client_id, limit=limit, cursor=cursor)

    async def revoke_key(self, key_id: str) -> ApiKeyDocument:
        """Revoke one key.

        Revoked rather than deleted, so the audit trail keeps a name to attach
        past decisions to. A revoked key authenticates nothing.
        """
        record = await self._keys.get_by_key_id(key_id)
        if record is None:
            raise ApiKeyNotFoundError("no key with that identifier exists")
        if record.status == "revoked":
            return record
        now = self._clock()
        revoked = record.model_copy(
            update={"status": "revoked", "updated_at": now, "revoked_at": now}
        )
        return await self._keys.update(revoked, expected_revision=record.revision)

    @staticmethod
    def _validated(scopes: Sequence[str]) -> tuple[Scope, ...]:
        """Require at least one scope, and only ones this platform defines."""
        if not scopes:
            raise ValueError("a key must grant at least one scope")
        return canonical(parse_scopes(scopes))

    @staticmethod
    def _expiry(now: datetime, days: int | None) -> datetime | None:
        """Turn a lifetime in days into an instant, refusing a useless one.

        Zero or fewer days would mint a key that is already expired, which is
        a mistyped option rather than an intention worth honouring.
        """
        if days is None:
            return None
        if days < 1:
            raise ValueError("a key must be valid for at least one day")
        return now + timedelta(days=days)
