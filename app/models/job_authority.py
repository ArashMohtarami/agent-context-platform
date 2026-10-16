"""Immutable server-issued initiating authority; never an ingestion DTO."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.common import Identifier


class JobAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    org_id: Identifier
    key_id: str | None
    client_id: Identifier | None
    action: Literal["research:write"] = "research:write"
    project_ids: tuple[str, ...] | None
    profile_ids: tuple[str, ...] | None
    research_project_id: Identifier
    source_id: Identifier
    worker_schema_version: Literal[37, 40, 41, 42] = 37
    subject_kind: Literal["legacy", "human", "machine", "operator"] = "legacy"
    sponsor_id: Identifier | None = None
    client_policy_revision: int | None = Field(default=None, ge=1)
    policy_context: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    user_id: Identifier | None = None
    membership_revision: int | None = Field(default=None, ge=1)
    session_id: Identifier | None = None

    @model_validator(mode="after")
    def subject(self) -> "JobAuthority":
        human = self.subject_kind == "human"
        machine = self.subject_kind == "machine"
        if machine and (
            self.worker_schema_version != 42
            or self.key_id is None
            or self.client_id is None
            or self.sponsor_id is None
            or self.client_policy_revision is None
            or self.policy_context is None
        ):
            raise ValueError("machine work requires verified schema42 sponsorship context")
        if not machine and any(
            v is not None
            for v in (self.sponsor_id, self.client_policy_revision, self.policy_context)
        ):
            raise ValueError("machine sponsorship cannot be attached to another subject kind")
        if human and (
            self.worker_schema_version not in {41, 42}
            or self.key_id is not None
            or self.client_id is not None
            or self.user_id is None
            or self.membership_revision is None
            or self.session_id is None
        ):
            raise ValueError("human work requires verified schema-41 session authority")
        if not human and any(
            v is not None for v in (self.user_id, self.membership_revision, self.session_id)
        ):
            raise ValueError("human authority cannot be attached to machine/operator work")
        return self
