"""In-memory stand-ins for the collections authentication reads and writes.

Keys and audit records are the two things a security test has to be able to
arrange precisely — an expired key, a revoked one, a key for one project only —
and reaching MongoDB for that would make the tests slower without making them
truer. The behaviour under test is the service's, not the driver's.

These deliberately store whole documents rather than fields, so a test that
asserts on what was written is asserting on what would have been persisted.
"""

from datetime import datetime, timedelta
from typing import Any

from app.models.api_key import ApiKeyDocument
from app.models.audit_log import AuditLogDocument
from app.models.common import Page, utc_now
from app.security.keys import MintedKey, hash_secret, mint
from app.security.scopes import ALL_SCOPES, Scope

NOW = utc_now()


class FakeApiKeyRepository:
    """The issued keys, in a dictionary."""

    def __init__(self, *records: ApiKeyDocument) -> None:
        self.records: dict[str, ApiKeyDocument] = {record.key_id: record for record in records}
        self.touched: list[tuple[str, datetime]] = []

    async def create(self, document: ApiKeyDocument) -> ApiKeyDocument:
        """Store one newly issued key."""
        if document.key_id in self.records:
            raise ValueError("duplicate key_id")
        self.records[document.key_id] = document
        return document

    async def get_by_key_id(self, key_id: str) -> ApiKeyDocument | None:
        """Load one key by its public identifier."""
        return self.records.get(key_id)

    async def list(
        self,
        *,
        status: str | None = None,
        client_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ApiKeyDocument]:
        """List stored keys, newest first, bounded by `limit`."""
        del cursor
        items = [
            record
            for record in sorted(self.records.values(), key=lambda item: item.created_at)
            if (status is None or record.status == status)
            and (client_id is None or record.client_id == client_id)
        ]
        return Page(items=tuple(items[:limit]))

    async def update(
        self,
        document: ApiKeyDocument,
        *,
        expected_revision: int,
    ) -> ApiKeyDocument:
        """Apply a revision-guarded change, such as a revocation."""
        current = self.records[document.key_id]
        if current.revision != expected_revision:
            raise ValueError("revision conflict")
        stored = document.model_copy(update={"revision": expected_revision + 1})
        self.records[document.key_id] = stored
        return stored

    async def touch(self, key_id: str, used_at: datetime) -> None:
        """Record that a key was used."""
        self.touched.append((key_id, used_at))
        record = self.records.get(key_id)
        if record is not None:
            self.records[key_id] = record.model_copy(update={"last_used_at": used_at})


class FakeAuditRepository:
    """The audit trail, as a list of what was appended to it."""

    def __init__(self, *, fails: bool = False) -> None:
        self.records: list[AuditLogDocument] = []
        self._fails = fails

    async def create(self, document: AuditLogDocument) -> AuditLogDocument:
        """Append one record, or fail if this double was built to."""
        if self._fails:
            raise RuntimeError("the audit trail is unwritable")
        self.records.append(document)
        return document

    def actions(self) -> list[tuple[str, str]]:
        """Return what was recorded, as `(action, outcome)` pairs."""
        return [(record.action, record.outcome) for record in self.records]

    def reasons(self) -> list[str]:
        """Return every reason recorded, for asserting what was not in one."""
        return [record.reason or "" for record in self.records]


def issued_key(
    *,
    scopes: tuple[Scope, ...] = ALL_SCOPES,
    projects: tuple[str, ...] = (),
    profiles: tuple[str, ...] = (),
    status: str = "active",
    expires_in: timedelta | None = None,
    name: str = "test key",
    now: datetime = NOW,
    minted: MintedKey | None = None,
) -> tuple[ApiKeyDocument, str]:
    """Mint one key and return the stored document with its usable token.

    The token is returned alongside because a test that presents a credential
    needs the real one: hashing is what is under test, so a fixture that faked
    the hash would test nothing.
    """
    credential = minted if minted is not None else mint()
    document = ApiKeyDocument(
        org_id="org-test",
        identity_version=2,
        project_mode="include" if projects else "all_in_org",
        profile_mode="include" if profiles else "all_in_org",
        key_id=credential.key_id,
        key_hash=credential.key_hash,
        name=name,
        scopes=list(scopes),
        projects=list(projects),
        profiles=list(profiles),
        status=status,  # type: ignore[arg-type]
        created_at=now,
        updated_at=now,
        expires_at=now + expires_in if expires_in is not None else None,
    )
    return document, credential.token


def expired_key(**overrides: Any) -> tuple[ApiKeyDocument, str]:
    """Mint one key whose expiry has already passed.

    Built by issuing it in the past, because the model refuses a key that
    expires before it was created — which is itself the behaviour that makes
    this the only honest way to construct one.
    """
    past = overrides.pop("now", NOW - timedelta(days=30))
    return issued_key(now=past, expires_in=timedelta(days=1), **overrides)


def bearer(token: str) -> dict[str, str]:
    """Build the header that presents one key."""
    return {"Authorization": f"Bearer {token}"}


def key_hash_of(secret: str) -> str:
    """Hash one secret the way the platform stores it."""
    return hash_secret(secret)
