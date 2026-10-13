"""Administrative REST contracts for V4 Phase 1 agent clients."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.agent import (
    AgentActivityDocument,
    AgentActivityStatus,
    AgentClientDocument,
    AgentClientStatus,
    AgentClientType,
    AgentSessionDocument,
    AgentSessionStatus,
)
from app.models.common import Identifier, Metadata, ShortText
from app.security.scopes import parse_scopes


def _validate_scopes(value: list[str] | None) -> list[str] | None:
    """Reject undefined or repeated grants at the request boundary."""
    if value is not None:
        parse_scopes(value)
    return value


class AgentClientCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: Identifier
    client_type: AgentClientType
    name: ShortText
    owner_id: Identifier
    scopes: list[str] = Field(default_factory=list, max_length=32)
    project_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    metadata: Metadata = Field(default_factory=dict)

    _scopes = field_validator("scopes")(_validate_scopes)


class AgentClientUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    name: ShortText | None = None
    status: AgentClientStatus | None = None
    scopes: list[str] | None = Field(default=None, max_length=32)
    project_ids: list[Identifier] | None = Field(default=None, max_length=100)
    metadata: Metadata | None = None

    _scopes = field_validator("scopes")(_validate_scopes)


class AgentCredentialCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scopes: list[str] | None = Field(default=None, max_length=32)
    project_ids: list[Identifier] | None = Field(default=None, max_length=100)
    expires_in_days: int | None = Field(default=None, ge=1)

    _scopes = field_validator("scopes")(_validate_scopes)


class AgentClientResponse(BaseModel):
    client_id: str
    client_type: AgentClientType
    name: str
    status: AgentClientStatus
    owner_id: str
    scopes: tuple[str, ...]
    project_ids: tuple[str, ...]
    metadata: Metadata
    revision: int
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None

    @classmethod
    def from_document(cls, item: AgentClientDocument) -> "AgentClientResponse":
        return cls(
            client_id=item.client_id,
            client_type=item.client_type,
            name=item.name,
            status=item.status,
            owner_id=item.owner_id,
            scopes=tuple(item.scopes),
            project_ids=tuple(item.project_ids),
            metadata=item.metadata,
            revision=item.revision,
            created_at=item.created_at,
            updated_at=item.updated_at,
            last_seen_at=item.last_seen_at,
        )


class AgentClientListResponse(BaseModel):
    items: tuple[AgentClientResponse, ...]
    next_cursor: str | None


class AgentCredentialResponse(BaseModel):
    key_id: str
    client_id: str
    status: str
    scopes: tuple[str, ...]
    project_ids: tuple[str, ...]
    expires_at: datetime | None
    token: str


class AgentSessionResponse(BaseModel):
    """A small, content-free session lifecycle record."""

    session_id: str
    client_id: str
    client_type: AgentClientType
    project_id: str | None
    repository: str | None
    started_at: datetime
    last_seen_at: datetime
    ended_at: datetime | None
    status: AgentSessionStatus
    metadata: Metadata
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, item: AgentSessionDocument) -> "AgentSessionResponse":
        return cls(
            session_id=item.session_id,
            client_id=item.client_id,
            client_type=item.client_type,
            project_id=item.project_id,
            repository=item.repository,
            started_at=item.started_at,
            last_seen_at=item.last_seen_at,
            ended_at=item.ended_at,
            status=item.status,
            metadata=item.metadata,
            revision=item.revision,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class AgentActivityResponse(BaseModel):
    """One content-free gateway activity record safe to expose to its actor."""

    event_id: str
    client_id: str
    client_type: AgentClientType
    session_id: str | None
    operation: str
    tool: str | None
    project_id: str | None
    resource_type: str | None
    resource_id: str | None
    status: AgentActivityStatus
    latency_ms: float | None
    created_at: datetime

    @classmethod
    def from_document(cls, item: AgentActivityDocument) -> "AgentActivityResponse":
        return cls(
            event_id=item.event_id,
            client_id=item.client_id,
            client_type=item.client_type,
            session_id=item.session_id,
            operation=item.operation,
            tool=item.tool,
            project_id=item.project_id,
            resource_type=item.resource_type,
            resource_id=item.resource_id,
            status=item.status,
            latency_ms=item.latency_ms,
            created_at=item.created_at,
        )


class AgentActivityListResponse(BaseModel):
    items: tuple[AgentActivityResponse, ...]
    next_cursor: str | None
