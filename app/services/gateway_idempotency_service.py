"""Business rules for safe client-scoped MCP write retries."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Protocol

from app.models.common import utc_now
from app.models.idempotency import (
    IdempotencyRecordDocument,
    IdempotencyRecordStatus,
    digest_idempotency_key,
)
from app.repositories.errors import DuplicateDocumentError
from app.security.tenant import require_tenant
from app.security.tenant_policy import current_policy
from app.security.tenant_service import tenant_service


class IdempotencyRepository(Protocol):
    async def create(self, document: IdempotencyRecordDocument) -> IdempotencyRecordDocument: ...

    async def get(
        self, *, client_id: str, operation: str, key_hash: str
    ) -> IdempotencyRecordDocument | None: ...

    async def complete(
        self,
        document: IdempotencyRecordDocument,
        *,
        result_text: str,
        result_data: dict[str, object],
    ) -> IdempotencyRecordDocument | None: ...

    async def abandon(self, document: IdempotencyRecordDocument) -> None: ...

    async def prune_expired(self, now: datetime) -> None: ...


class IdempotencyConflictError(Exception):
    """One client reused a key for a materially different write."""


class IdempotencyInProgressError(Exception):
    """The prior call may still be committing and must not be duplicated."""


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    """A new write reservation or a completed result to replay."""

    document: IdempotencyRecordDocument
    replay: bool


def policy_hash() -> str:
    policy = current_policy()
    return (
        policy.fingerprint
        if policy
        else sha256(("operator:" + require_tenant().org_id).encode()).hexdigest()
    )


@tenant_service
class GatewayIdempotencyService:
    """Reserve, complete, and replay bounded client-side write retries."""

    def __init__(self, repository: IdempotencyRepository, *, retention_seconds: int) -> None:
        if retention_seconds < 60:
            raise ValueError("idempotency retention must be at least one minute")
        self._repository = repository
        self._retention = timedelta(seconds=retention_seconds)

    async def acquire(
        self,
        *,
        client_id: str,
        operation: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> IdempotencyReservation:
        """Reserve a new write or return its finished result without rerunning it."""
        now = utc_now()
        await self._repository.prune_expired(now)
        key_hash = digest_idempotency_key(idempotency_key)
        document = IdempotencyRecordDocument(
            client_id=client_id,
            operation=operation,
            key_hash=key_hash,
            payload_hash=payload_hash,
            policy_hash=policy_hash(),
            expires_at=now + self._retention,
        )
        try:
            created = await self._repository.create(document)
            return IdempotencyReservation(document=created, replay=False)
        except DuplicateDocumentError:
            existing = await self._repository.get(
                client_id=client_id, operation=operation, key_hash=key_hash
            )
            if existing is None:  # pragma: no cover - deletion raced a duplicate-key response
                return await self.acquire(
                    client_id=client_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
            if existing.policy_hash != policy_hash():
                raise IdempotencyConflictError("the initiating authority changed") from None
            if existing.payload_hash != payload_hash:
                raise IdempotencyConflictError(
                    "the idempotency key was already used for a different request"
                ) from None
            if existing.status is IdempotencyRecordStatus.COMPLETED:
                return IdempotencyReservation(document=existing, replay=True)
            raise IdempotencyInProgressError(
                "a prior request with this idempotency key is still being processed"
            ) from None

    async def complete(
        self,
        document: IdempotencyRecordDocument,
        *,
        result_text: str,
        result_data: dict[str, object],
    ) -> None:
        """Persist a replay result once, refusing an uncertain duplicate completion."""
        completed = await self._repository.complete(
            document,
            result_text=result_text,
            result_data=result_data,
        )
        if completed is None:
            raise IdempotencyInProgressError(
                "the idempotency reservation could not be completed safely"
            )

    async def complete_request(
        self,
        *,
        client_id: str,
        operation: str,
        idempotency_key: str,
        payload_hash: str,
        result_text: str,
        result_data: dict[str, object],
    ) -> None:
        """Complete exactly the reservation derived from this authenticated client."""
        document = await self._repository.get(
            client_id=client_id,
            operation=operation,
            key_hash=digest_idempotency_key(idempotency_key),
        )
        if (
            document is None
            or document.payload_hash != payload_hash
            or document.policy_hash != policy_hash()
        ):
            raise IdempotencyConflictError("the idempotency reservation was not found")
        if document.status is IdempotencyRecordStatus.COMPLETED:
            return
        await self.complete(document, result_text=result_text, result_data=result_data)

    async def abandon(self, document: IdempotencyRecordDocument) -> None:
        """Release a failure that is known not to have produced a write result."""
        await self._repository.abandon(document)

    async def abandon_request(
        self,
        *,
        client_id: str,
        operation: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> None:
        """Release a known-safe failed attempt only if its identity still matches."""
        document = await self._repository.get(
            client_id=client_id,
            operation=operation,
            key_hash=digest_idempotency_key(idempotency_key),
        )
        if (
            document is None
            or document.payload_hash != payload_hash
            or document.policy_hash != policy_hash()
        ):
            return
        if document.status is IdempotencyRecordStatus.IN_PROGRESS:
            await self.abandon(document)
