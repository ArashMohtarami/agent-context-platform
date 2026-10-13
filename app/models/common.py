"""Shared types and invariants for persisted documents."""

from base64 import b64decode, urlsafe_b64encode
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from json import JSONDecodeError, dumps, loads
from typing import Annotated

from bson import ObjectId
from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    TypeAdapter,
    WithJsonSchema,
    field_validator,
    model_validator,
)

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
ContentText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200_000),
]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Tags = Annotated[list[Tag], Field(default_factory=list, max_length=50)]
Metadata = dict[str, object]
_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


def normalize_mongo_timestamp(value: datetime) -> datetime:
    """Normalize an aware timestamp to MongoDB's UTC millisecond precision."""
    normalized = value.astimezone(UTC)
    return normalized.replace(microsecond=(normalized.microsecond // 1_000) * 1_000)


def validate_identifier(value: object) -> str:
    """Validate an identifier at non-model boundaries such as repository scope."""
    return _IDENTIFIER_ADAPTER.validate_python(value)


def _validate_object_id(value: object) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    raise ValueError("must be a valid MongoDB ObjectId")


MongoObjectId = Annotated[
    ObjectId,
    BeforeValidator(_validate_object_id),
    PlainSerializer(str, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": "^[0-9a-f]{24}$"}),
]


def deterministic_object_id(namespace: str, *parts: str) -> ObjectId:
    """Build a stable BSON ObjectId from a namespaced natural key."""
    digest = sha256("\x00".join((namespace, *parts)).encode()).digest()
    return ObjectId(digest[:12])


def populate_deterministic_id(
    value: object,
    *,
    namespace: str,
    fields: tuple[str, ...],
) -> object:
    """Populate `_id` when all natural-key fields are present."""
    if not isinstance(value, Mapping) or "_id" in value or "id" in value:
        return value
    parts = [value.get(field) for field in fields]
    if not all(isinstance(part, str) and part for part in parts):
        return value
    populated = dict(value)
    populated["_id"] = deterministic_object_id(namespace, *parts)  # type: ignore[arg-type]
    return populated


class MongoDocumentModel(BaseModel):
    """Base for strict, revisioned MongoDB documents."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        arbitrary_types_allowed=True,
        validate_assignment=True,
    )

    id: MongoObjectId = Field(default_factory=ObjectId, alias="_id")
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)
    revision: int = Field(default=1, ge=1)

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        """Normalize timestamps to MongoDB's UTC millisecond precision."""
        return normalize_mongo_timestamp(value)

    @field_validator("id")
    @classmethod
    def require_object_id(cls, value: ObjectId) -> ObjectId:
        """Keep BSON document identifiers strongly typed."""
        return value

    @model_validator(mode="after")
    def validate_timestamps(self) -> "MongoDocumentModel":
        """Prevent an update timestamp from predating creation."""
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be on or after created_at")
        return self

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: int) -> int:
        """Reject booleans, which BSON otherwise treats as integer-like values."""
        if isinstance(value, bool):
            raise ValueError("revision must be an integer")
        return value


class TaggedDocument(MongoDocumentModel):
    """Base for documents carrying bounded, unique tags."""

    tags: Tags = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        """Reject duplicates while preserving exact technical identifier casing."""
        if len(value) != len(set(value)):
            raise ValueError("tags must be unique")
        return value


class Page[TDocument: MongoDocumentModel](BaseModel):
    """A bounded cursor page."""

    model_config = ConfigDict(frozen=True)

    items: tuple[TDocument, ...]
    next_cursor: str | None = None


class InvalidCursorError(ValueError):
    """Raised when a pagination cursor is malformed."""


def encode_page_cursor(created_at: datetime, document_id: ObjectId) -> str:
    """Encode a stable `(created_at, _id)` keyset cursor."""
    payload = dumps(
        {"created_at": created_at.astimezone(UTC).isoformat(), "id": str(document_id)},
        separators=(",", ":"),
    ).encode()
    return urlsafe_b64encode(payload).decode().rstrip("=")


def decode_page_cursor(cursor: str) -> tuple[datetime, ObjectId]:
    """Decode and strictly validate a keyset cursor."""
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = loads(b64decode(cursor + padding, altchars=b"-_", validate=True))
        created_at = datetime.fromisoformat(payload["created_at"])
        document_id = _validate_object_id(payload["id"])
    except (KeyError, TypeError, ValueError, JSONDecodeError) as error:
        raise InvalidCursorError("invalid pagination cursor") from error
    if created_at.tzinfo is None:
        raise InvalidCursorError("invalid pagination cursor")
    return created_at.astimezone(UTC), document_id
