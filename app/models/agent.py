"""V4 agent-gateway persistence contracts.

Phase 0 defines durable client identity and operational metadata only.  It
does not authenticate clients, authorize a request, resolve a project, or
record actor provenance on V1--V3 resources; those are later V4 phases.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime, Field, ValidationInfo, field_validator, model_validator

from app.models.common import (
    Identifier,
    Metadata,
    MongoDocumentModel,
    ShortText,
    deterministic_object_id,
    normalize_mongo_timestamp,
    populate_deterministic_id,
    validate_identifier,
)
from app.projects.bindings import normalize_binding_value
from app.security.scopes import SCOPE_SET

MAX_AGENT_SCOPES = 32
MAX_AGENT_PROJECTS = 100


class AgentClientType(StrEnum):
    """Descriptive client category, not a switch for business logic."""

    CODEX = "codex"
    CLAUDE_CODE = "claude_code"
    GEMINI_CLI = "gemini_cli"
    CHATGPT = "chatgpt"
    CURSOR = "cursor"
    AUTOMATION = "automation"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class AgentClientStatus(StrEnum):
    """Whether a registered client may be enabled by a later gateway phase."""

    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"


class AgentSessionStatus(StrEnum):
    """The lightweight lifecycle of a client session."""

    ACTIVE = "active"
    ENDED = "ended"
    EXPIRED = "expired"


class ProjectBindingType(StrEnum):
    """Deterministic project identifiers accepted by the future resolver."""

    GIT_REMOTE = "git_remote"
    REPOSITORY_URL = "repository_url"
    REPOSITORY_NAME = "repository_name"
    WORKSPACE_ALIAS = "workspace_alias"


class ProjectResolutionMatch(StrEnum):
    """The explicit input that deterministically identified a project."""

    EXPLICIT_PROJECT_ID = "explicit_project_id"
    GIT_REMOTE = "git_remote"
    REPOSITORY_URL = "repository_url"
    REPOSITORY_NAME = "repository_name"
    WORKSPACE_ALIAS = "workspace_alias"


class ProjectResolutionStatus(StrEnum):
    """Whether deterministic resolution found exactly one authorized project."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"


class AgentActivityStatus(StrEnum):
    """Outcome of an operational event, without recording request content."""

    SUCCEEDED = "succeeded"
    DENIED = "denied"
    FAILED = "failed"


_FORBIDDEN_SESSION_METADATA_KEYS = frozenset(
    {
        "api_key",
        "chain_of_thought",
        "context_window",
        "conversation",
        "credential",
        "messages",
        "password",
        "prompt",
        "secret",
        "token",
        "transcript",
    }
)
_MAX_SESSION_METADATA_ITEMS = 20
_MAX_SESSION_METADATA_TEXT = 500


def validate_session_metadata(value: Metadata) -> Metadata:
    """Accept only small operational labels, never conversation-like payloads.

    Session metadata is for a workspace label, client version, or similar
    debugging facts.  Nested structures and unbounded text turn it into a
    covert conversation store, which is explicitly outside the session
    contract.
    """
    if len(value) > _MAX_SESSION_METADATA_ITEMS:
        raise ValueError(
            f"session metadata may contain at most {_MAX_SESSION_METADATA_ITEMS} items"
        )
    for key, item in value.items():
        try:
            normalized_key = validate_identifier(key).lower()
        except ValueError as error:
            raise ValueError("session metadata keys must be safe identifiers") from error
        if normalized_key in _FORBIDDEN_SESSION_METADATA_KEYS:
            raise ValueError(f"session metadata must not include {normalized_key}")
        if item is None or isinstance(item, bool | int | float):
            continue
        if isinstance(item, str) and len(item) <= _MAX_SESSION_METADATA_TEXT:
            continue
        raise ValueError("session metadata values must be small scalar values")
    return value


class AgentClientDocument(MongoDocumentModel):
    """A client identity that later V4 authentication will attach to a key."""

    client_id: Identifier
    client_type: AgentClientType
    name: ShortText
    status: AgentClientStatus = AgentClientStatus.ACTIVE
    owner_id: Identifier
    # A profile is an onboarding template. Its scope policy is copied into
    # ``scopes`` at assignment time so a later profile edit cannot silently
    # widen an already-issued client credential.
    profile_id: Identifier | None = None
    scopes: list[str] = Field(default_factory=list, max_length=MAX_AGENT_SCOPES)
    project_ids: list[Identifier] = Field(default_factory=list, max_length=MAX_AGENT_PROJECTS)
    metadata: Metadata = Field(default_factory=dict)
    last_seen_at: AwareDatetime | None = None

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        """Make the public client identifier the stable collection identity."""
        return populate_deterministic_id(value, namespace="agent-client", fields=("client_id",))

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        """Keep Phase 0 client metadata aligned with the existing scope vocabulary."""
        if len(value) != len(set(value)):
            raise ValueError("scopes must be unique")
        unknown = sorted(set(value) - SCOPE_SET)
        if unknown:
            raise ValueError(f"unknown scopes: {', '.join(unknown)}")
        return value

    @field_validator("project_ids")
    @classmethod
    def validate_project_ids(cls, value: list[str]) -> list[str]:
        """Avoid ambiguous duplicate project grants in persisted metadata."""
        if len(value) != len(set(value)):
            raise ValueError("project_ids must be unique")
        return value

    @field_validator("last_seen_at")
    @classmethod
    def normalize_last_seen(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_mongo_timestamp(value)

    @model_validator(mode="after")
    def validate_id(self) -> "AgentClientDocument":
        """Prevent an accidental mismatch between Mongo and public identity."""
        if self.id != deterministic_object_id("agent-client", self.client_id):
            raise ValueError("_id does not match client_id")
        return self


class AgentSessionDocument(MongoDocumentModel):
    """Lightweight session metadata; intentionally never a conversation store."""

    session_id: Identifier
    client_id: Identifier
    client_type: AgentClientType
    project_id: Identifier | None = None
    repository: str | None = Field(default=None, min_length=1, max_length=2_048)
    started_at: AwareDatetime
    last_seen_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    status: AgentSessionStatus = AgentSessionStatus.ACTIVE
    metadata: Metadata = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Metadata) -> Metadata:
        """Keep sessions operational and content-free at the persistence boundary."""
        return validate_session_metadata(value)

    @field_validator("started_at", "last_seen_at", "ended_at")
    @classmethod
    def normalize_session_instants(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_mongo_timestamp(value)

    @model_validator(mode="after")
    def validate_session_times(self) -> "AgentSessionDocument":
        if self.last_seen_at < self.started_at:
            raise ValueError("last_seen_at must be on or after started_at")
        if self.ended_at is not None and self.ended_at < self.last_seen_at:
            raise ValueError("ended_at must be on or after last_seen_at")
        if self.status == AgentSessionStatus.ACTIVE:
            if self.ended_at is not None:
                raise ValueError("an active session cannot have ended_at")
        elif self.ended_at is None:
            raise ValueError("an ended or expired session requires ended_at")
        return self


class ProjectBindingDocument(MongoDocumentModel):
    """One explicit repository or workspace identifier for a platform project."""

    binding_id: Identifier
    project_id: Identifier
    binding_type: ProjectBindingType
    value: str = Field(min_length=1, max_length=2_048)
    # Internal canonical key for one repository's SSH and HTTPS spellings.
    # It is deliberately absent from public API responses.
    repository_locator: str | None = None

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str, info: ValidationInfo) -> str:
        """Store only the safe canonical form used by the resolver."""
        # `binding_type` precedes `value` in the model, so it is available at
        # model validation time.  A missing type is reported separately by
        # Pydantic rather than concealed behind a normalization error.
        binding_type = info.data.get("binding_type")
        if binding_type is None:
            return value
        return normalize_binding_value(str(binding_type), value)

    @model_validator(mode="after")
    def assign_repository_locator(self) -> "ProjectBindingDocument":
        """Derive one unique cross-protocol key for repository bindings."""
        expected = (
            self.value
            if self.binding_type
            in {ProjectBindingType.GIT_REMOTE, ProjectBindingType.REPOSITORY_URL}
            else None
        )
        if self.repository_locator not in {None, expected}:
            raise ValueError("repository_locator does not match the canonical binding value")
        # This is an internal derived persistence field, not caller-controlled
        # data.  Bypass assignment validation to avoid a validator recursion.
        object.__setattr__(self, "repository_locator", expected)
        return self

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        """Make the public binding identifier the stable collection identity."""
        return populate_deterministic_id(value, namespace="project-binding", fields=("binding_id",))

    @model_validator(mode="after")
    def validate_id(self) -> "ProjectBindingDocument":
        expected = deterministic_object_id("project-binding", self.binding_id)
        if self.id != expected:
            raise ValueError("_id does not match binding_id")
        return self


class AgentActivityDocument(MongoDocumentModel):
    """Append-only operational metadata, deliberately excluding prompt bodies."""

    event_id: Identifier
    client_id: Identifier
    client_type: AgentClientType
    session_id: Identifier | None = None
    operation: Identifier
    tool: Identifier | None = None
    project_id: Identifier | None = None
    resource_type: Identifier | None = None
    resource_id: Identifier | None = None
    status: AgentActivityStatus
    latency_ms: float | None = Field(default=None, ge=0.0)
