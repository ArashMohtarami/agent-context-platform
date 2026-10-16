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
    worker_schema_version: Literal[37, 40, 41] = 37
    subject_kind: Literal["legacy", "human", "operator"] = "legacy"
    user_id: Identifier | None = None
    membership_revision: int | None = Field(default=None, ge=1)
    session_id: Identifier | None = None

    @model_validator(mode="after")
    def subject(self) -> "JobAuthority":
        human = self.subject_kind == "human"
        if human and (
            self.worker_schema_version != 41
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
