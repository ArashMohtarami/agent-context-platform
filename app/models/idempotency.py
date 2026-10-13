"""Durable, credential-free records for safe MCP write retries."""

from enum import StrEnum
from hashlib import sha256
from json import dumps
from typing import Any

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.models.common import Identifier, Metadata, MongoDocumentModel, deterministic_object_id


class IdempotencyRecordStatus(StrEnum):
    """The two safe states for a retry reservation."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


HASH_PATTERN = r"^[0-9a-f]{64}$"
MAX_RESULT_TEXT = 1_000_000
MAX_RESULT_BYTES = 2_000_000


def digest_idempotency_key(value: str) -> str:
    """Hash a caller key before persistence so it cannot be recovered from a dump."""
    return sha256(value.encode("utf-8")).hexdigest()


def digest_payload(value: dict[str, Any]) -> str:
    """Hash a canonical request shape without persisting its prompt or body."""
    encoded = dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


class IdempotencyRecordDocument(MongoDocumentModel):
    """One client/operation/key reservation and its replay-safe MCP result.

    The key and request payload never appear here in plaintext.  The retained
    result is needed only to answer a completed retry exactly as the original
    call was answered; it is subject to a short, bounded TTL.
    """

    client_id: Identifier
    operation: Identifier
    key_hash: str = Field(pattern=HASH_PATTERN)
    payload_hash: str = Field(pattern=HASH_PATTERN)
    status: IdempotencyRecordStatus = IdempotencyRecordStatus.IN_PROGRESS
    result_text: str | None = Field(default=None, max_length=MAX_RESULT_TEXT)
    result_data: Metadata | None = None
    expires_at: AwareDatetime

    @field_validator("result_data")
    @classmethod
    def result_must_stay_bounded(cls, value: Metadata | None) -> Metadata | None:
        """Keep a replay cache from becoming an unbounded second content store."""
        if value is None:
            return None
        try:
            encoded = dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError) as error:
            raise ValueError("idempotency result data must be JSON serializable") from error
        if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
            raise ValueError("idempotency result data exceeds the replay limit")
        return value

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        """Make a client/key reservation naturally unique without storing the key."""
        if not isinstance(value, dict) or "_id" in value or "id" in value:
            return value
        client_id = value.get("client_id")
        operation = value.get("operation")
        key_hash = value.get("key_hash")
        if not (
            isinstance(client_id, str)
            and client_id
            and isinstance(operation, str)
            and operation
            and isinstance(key_hash, str)
            and key_hash
        ):
            return value
        populated = dict(value)
        populated["_id"] = deterministic_object_id(
            "gateway-idempotency", client_id, operation, key_hash
        )
        return populated

    @model_validator(mode="after")
    def validate_state(self) -> "IdempotencyRecordDocument":
        expected = deterministic_object_id(
            "gateway-idempotency", self.client_id, self.operation, self.key_hash
        )
        if self.id != expected:
            raise ValueError("_id does not match the idempotency natural key")
        has_result = self.result_text is not None and self.result_data is not None
        if self.status is IdempotencyRecordStatus.COMPLETED and not has_result:
            raise ValueError("a completed idempotency record requires its result")
        if self.status is IdempotencyRecordStatus.IN_PROGRESS and (
            self.result_text is not None or self.result_data is not None
        ):
            raise ValueError("an in-progress idempotency record cannot have a result")
        return self
