"""Administrative API contracts for reusable MCP client profiles."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.agent import AgentClientDocument
from app.models.client_profile import (
    DEFAULT_CLIENT_PROFILE_DESCRIPTION,
    ClientProfileDocument,
    ClientProfileStatus,
)
from app.models.common import Identifier, Metadata, ShortText
from app.security.scopes import parse_scopes


def _validate_scopes(value: list[str] | None) -> list[str] | None:
    """Reject undefined or repeated scope grants at the API boundary."""
    if value is not None:
        parse_scopes(value)
    return value


class ClientProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    name: ShortText
    description: ShortText = DEFAULT_CLIENT_PROFILE_DESCRIPTION
    scopes: list[str] = Field(min_length=1, max_length=32)
    rate_limit_profile: Identifier | None = None
    status: ClientProfileStatus = ClientProfileStatus.ACTIVE
    metadata: Metadata = Field(default_factory=dict)

    _scopes = field_validator("scopes")(_validate_scopes)


class ClientProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    name: ShortText | None = None
    description: ShortText | None = None
    scopes: list[str] | None = Field(default=None, min_length=1, max_length=32)
    rate_limit_profile: Identifier | None = None
    status: ClientProfileStatus | None = None
    metadata: Metadata | None = None

    _scopes = field_validator("scopes")(_validate_scopes)


class ClientProfileAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: Identifier
    expected_revision: int = Field(ge=1)
    scope_overrides: list[str] | None = Field(default=None, max_length=32)
    project_overrides: list[Identifier] | None = Field(default=None, max_length=100)

    _scopes = field_validator("scope_overrides")(_validate_scopes)


class ClientProfileResponse(BaseModel):
    profile_id: str
    name: str
    description: str
    scopes: tuple[str, ...]
    rate_limit_profile: str | None
    status: ClientProfileStatus
    metadata: Metadata
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, item: ClientProfileDocument) -> "ClientProfileResponse":
        return cls(
            profile_id=item.profile_id,
            name=item.name,
            description=item.description,
            scopes=tuple(item.scopes),
            rate_limit_profile=item.rate_limit_profile,
            status=item.status,
            metadata=item.metadata,
            revision=item.revision,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class ClientProfileListResponse(BaseModel):
    items: tuple[ClientProfileResponse, ...]
    next_cursor: str | None


class ClientProfileAssignmentResponse(BaseModel):
    """The V5 assignment result without changing the V4 client response shape."""

    client_id: str
    profile_id: str | None
    scopes: tuple[str, ...]
    project_ids: tuple[str, ...]
    revision: int

    @classmethod
    def from_document(cls, item: AgentClientDocument) -> "ClientProfileAssignmentResponse":
        return cls(
            client_id=item.client_id,
            profile_id=item.profile_id,
            scopes=tuple(item.scopes),
            project_ids=tuple(item.project_ids),
            revision=item.revision,
        )
