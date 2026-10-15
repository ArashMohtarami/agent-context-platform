"""Narrow, credential-free contracts between the MCP gateway and Context API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.agent import AgentActivityStatus, AgentClientType, validate_session_metadata
from app.models.common import Identifier, Metadata
from app.security.scopes import Scope


class GatewayIdentityResponse(BaseModel):
    """The safe authorization facts an MCP process needs for one tool call."""

    org_id: Identifier
    key_id: str
    allowed_profile_ids: tuple[str, ...] | None
    client_id: str | None
    client_type: AgentClientType | None
    scopes: tuple[Scope, ...]
    allowed_project_ids: tuple[str, ...] | None


class GatewayActivityRequest(BaseModel):
    """A content-free event that the API attributes to the authenticated client."""

    model_config = ConfigDict(extra="forbid")

    operation: Identifier
    tool: Identifier | None = None
    project_id: Identifier | None = None
    resource_type: Identifier | None = None
    resource_id: Identifier | None = None
    status: AgentActivityStatus
    latency_ms: float = Field(ge=0.0)


class GatewayIdempotencyAcquireRequest(BaseModel):
    """A retry reservation; the API hashes its key before it reaches MongoDB."""

    model_config = ConfigDict(extra="forbid")

    operation: Identifier
    idempotency_key: Identifier
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GatewayIdempotencyResult(BaseModel):
    """The bounded MCP response needed to replay a completed write."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=1_000_000)
    data: Metadata


class GatewayIdempotencyAcquireResponse(BaseModel):
    """Either grants a reservation or returns a previous completed result."""

    decision: Literal["proceed", "replay"]
    result: GatewayIdempotencyResult | None = None


class GatewayIdempotencyCompleteRequest(GatewayIdempotencyAcquireRequest):
    """Attach one bounded result to a previously reserved retry key."""

    result: GatewayIdempotencyResult


class GatewaySessionBeginRequest(BaseModel):
    """Client-supplied operational labels for a new gateway session."""

    model_config = ConfigDict(extra="forbid")

    project_id: Identifier | None = None
    repository: str | None = Field(default=None, min_length=1, max_length=2_048)
    metadata: Metadata = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Metadata) -> Metadata:
        return validate_session_metadata(value)


class GatewaySessionUpdateRequest(BaseModel):
    """Revision-guarded changes to an active gateway session."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    project_id: Identifier | None = None
    repository: str | None = Field(default=None, min_length=1, max_length=2_048)
    # The default makes omission distinguishable through ``model_fields_set``;
    # an explicit null is rejected rather than being allowed to erase the
    # operational metadata contract.
    metadata: Metadata = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: Metadata) -> Metadata:
        return validate_session_metadata(value)

    @model_validator(mode="after")
    def require_change(self) -> "GatewaySessionUpdateRequest":
        if not ({"project_id", "repository", "metadata"} & self.model_fields_set):
            raise ValueError("provide at least one session field to update")
        return self


class GatewaySessionEndRequest(BaseModel):
    """Revision supplied when closing an active session."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
