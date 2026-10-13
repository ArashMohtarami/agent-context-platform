"""Generic, administrative MCP client-profile persistence contracts."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pydantic import Field, field_validator, model_validator

from app.models.common import (
    Identifier,
    Metadata,
    MongoDocumentModel,
    ShortText,
    deterministic_object_id,
    populate_deterministic_id,
    validate_identifier,
)
from app.security.scopes import SCOPE_SET

MAX_CLIENT_PROFILE_SCOPES = 32
MAX_CLIENT_PROFILE_METADATA_ITEMS = 20
MAX_CLIENT_PROFILE_METADATA_TEXT = 500
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
    }
)
DEFAULT_CLIENT_PROFILE_DESCRIPTION: Final = "Generic MCP client profile"
GEMINI_DEFAULT_PROFILE_ID: Final = "coding-agent-default"
GEMINI_DEFAULT_SCOPES: Final[tuple[str, ...]] = (
    "skills:read",
    "projects:read",
    "context:read",
    "context:write",
    "memory:read",
    "memory:write",
    "search:read",
    "research:read",
    "research:write",
    "agent:self",
    "session:write",
    "audit:self:read",
)


class ClientProfileStatus(StrEnum):
    """Whether an onboarding profile may be assigned to a new client."""

    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class RecommendedClientProfile:
    """A non-persistent, operator-selectable onboarding template."""

    profile_id: str
    name: str
    description: str
    scopes: tuple[str, ...]
    rate_limit_profile: str | None = "standard"


def recommended_client_profiles() -> tuple[RecommendedClientProfile, ...]:
    """Return Phase 0 profile templates without silently seeding policy.

    An operator must explicitly persist and assign a template.  This prevents
    startup from changing any V4 client's authorization or project access.
    """
    return (
        RecommendedClientProfile(
            profile_id=GEMINI_DEFAULT_PROFILE_ID,
            name="Default Coding Agent",
            description="Read/write coding agent profile suitable for Gemini CLI.",
            scopes=GEMINI_DEFAULT_SCOPES,
        ),
        RecommendedClientProfile(
            profile_id="coding-agent-readonly",
            name="Read-only Coding Agent",
            description="Read-only technical retrieval for coding agents.",
            scopes=(
                "skills:read",
                "projects:read",
                "context:read",
                "memory:read",
                "search:read",
                "research:read",
                "agent:self",
                "audit:self:read",
            ),
            rate_limit_profile="readonly",
        ),
        RecommendedClientProfile(
            profile_id="research-agent",
            name="Research Agent",
            description="Research acquisition without approval or promotion authority.",
            scopes=(
                "skills:read",
                "projects:read",
                "context:read",
                "memory:read",
                "search:read",
                "research:read",
                "research:write",
                "agent:self",
                "session:write",
                "audit:self:read",
            ),
        ),
        RecommendedClientProfile(
            profile_id="documentation-agent",
            name="Documentation Agent",
            description="Documentation retrieval and durable context updates.",
            scopes=(
                "skills:read",
                "projects:read",
                "context:read",
                "context:write",
                "memory:read",
                "memory:write",
                "search:read",
                "research:read",
                "agent:self",
                "session:write",
                "audit:self:read",
            ),
        ),
        RecommendedClientProfile(
            profile_id="creator-agent",
            name="Creator Agent",
            description="Creator-system planning and drafting without publication authority.",
            scopes=(
                "skills:read",
                "projects:read",
                "context:read",
                "memory:read",
                "search:read",
                "creator:read",
                "creator:write",
                "content:read",
                "content:write",
                "agent:self",
                "session:write",
                "audit:self:read",
            ),
        ),
    )


def validate_client_profile_metadata(value: Metadata) -> Metadata:
    """Keep profile metadata operational, bounded, and free of secrets."""
    if len(value) > MAX_CLIENT_PROFILE_METADATA_ITEMS:
        raise ValueError(
            f"client profile metadata may contain at most {MAX_CLIENT_PROFILE_METADATA_ITEMS} items"
        )
    for key, item in value.items():
        try:
            normalized_key = validate_identifier(key).lower()
        except ValueError as error:
            raise ValueError("client profile metadata keys must be safe identifiers") from error
        if normalized_key in _FORBIDDEN_METADATA_KEYS:
            raise ValueError(f"client profile metadata must not include {normalized_key}")
        if item is None or isinstance(item, bool | int | float):
            continue
        if isinstance(item, str) and len(item) <= MAX_CLIENT_PROFILE_METADATA_TEXT:
            continue
        raise ValueError("client profile metadata values must be small scalar values")
    return value


class ClientProfileDocument(MongoDocumentModel):
    """A reusable client-onboarding template, not a separate authorization system.

    Assignment writes a bounded scope snapshot to the client identity.  That
    deliberately avoids policy inheritance and keeps V4 credential checks
    client-neutral and stable after profile edits.
    """

    profile_id: Identifier
    name: ShortText
    description: ShortText = DEFAULT_CLIENT_PROFILE_DESCRIPTION
    scopes: list[str] = Field(min_length=1, max_length=MAX_CLIENT_PROFILE_SCOPES)
    rate_limit_profile: Identifier | None = None
    status: ClientProfileStatus = ClientProfileStatus.ACTIVE
    metadata: Metadata = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        """Make the public profile identifier the stable collection identity."""
        return populate_deterministic_id(value, namespace="client-profile", fields=("profile_id",))

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        """Profiles may only name the existing capability vocabulary."""
        if len(value) != len(set(value)):
            raise ValueError("scopes must be unique")
        unknown = sorted(set(value) - SCOPE_SET)
        if unknown:
            raise ValueError(f"unknown scopes: {', '.join(unknown)}")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Metadata) -> Metadata:
        return validate_client_profile_metadata(value)

    @model_validator(mode="after")
    def validate_id(self) -> "ClientProfileDocument":
        if self.id != deterministic_object_id("client-profile", self.profile_id):
            raise ValueError("_id does not match profile_id")
        return self
