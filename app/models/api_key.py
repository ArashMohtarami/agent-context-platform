"""API key persistence model.

The secret is not here, and cannot be: what is stored is a hash, so a dump of
this collection is a list of what exists rather than a set of working
credentials.
"""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.models.common import (
    Identifier,
    MongoDocumentModel,
    ShortText,
    normalize_mongo_timestamp,
    utc_now,
)
from app.security.scopes import SCOPE_SET

KeyStatus = Literal["active", "revoked"]
MAX_SCOPES = 32
MAX_PROJECTS = 100
MAX_PROFILES = 100
# 8 random bytes rendered as hexadecimal, and a SHA-256 digest.
KEY_ID_PATTERN = r"^[0-9a-f]{16}$"
KEY_HASH_PATTERN = r"^[0-9a-f]{64}$"


class ApiKeyDocument(MongoDocumentModel):
    """One issued API key, without its secret.

    Resource modes distinguish none, include, and all_in_org. Adoption converts
    historical empty lists using their preservation manifest. No selector grants
    access outside this key's immutable organization.
    """

    key_id: str = Field(pattern=KEY_ID_PATTERN)
    client_id: Identifier | None = None
    key_hash: str = Field(pattern=KEY_HASH_PATTERN)
    name: ShortText
    scopes: list[str] = Field(min_length=1, max_length=MAX_SCOPES)
    project_mode: Literal["none", "include", "all_in_org"] | None = None
    profile_mode: Literal["none", "include", "all_in_org"] | None = None
    projects: list[Identifier] = Field(default_factory=list, max_length=MAX_PROJECTS)
    profiles: list[Identifier] = Field(default_factory=list, max_length=MAX_PROFILES)
    status: KeyStatus = "active"
    expires_at: AwareDatetime | None = None
    last_used_at: AwareDatetime | None = None
    revoked_at: AwareDatetime | None = None

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        """Reject a scope this platform does not define."""
        if len(value) != len(set(value)):
            raise ValueError("scopes must be unique")
        unknown = sorted(set(value) - SCOPE_SET)
        if unknown:
            raise ValueError(f"unknown scopes: {', '.join(unknown)}")
        return value

    @field_validator("expires_at", "last_used_at", "revoked_at")
    @classmethod
    def normalize_instants(cls, value: datetime | None) -> datetime | None:
        """Normalize to MongoDB's UTC millisecond precision.

        Both are persisted, so MongoDB truncates them anyway. Truncating here
        as well means an expiry reads back as the instant it was set to, rather
        than a fraction of a millisecond earlier than whatever was compared
        against it.
        """
        return None if value is None else normalize_mongo_timestamp(value)

    @field_validator("projects")
    @classmethod
    def validate_projects(cls, value: list[str]) -> list[str]:
        """Reject an ambiguous repeated project."""
        if len(value) != len(set(value)):
            raise ValueError("allowed projects must be unique")
        return value

    @field_validator("profiles")
    @classmethod
    def validate_profiles(cls, value: list[str]) -> list[str]:
        """Reject an ambiguous repeated creator-profile grant."""
        if len(value) != len(set(value)):
            raise ValueError("allowed profiles must be unique")
        return value

    @model_validator(mode="after")
    def validate_expiry(self) -> "ApiKeyDocument":
        """Reject a key that expired before it was issued."""
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self

    def is_expired(self, now: datetime | None = None) -> bool:
        """Report whether this key has passed its expiry."""
        if self.expires_at is None:
            return False
        return self.expires_at <= (now if now is not None else utc_now())

    def is_usable(self, now: datetime | None = None) -> bool:
        """Report whether this key may authenticate a request right now."""
        return self.status == "active" and not self.is_expired(now)
