"""Immutable server-issued initiating authority; never an ingestion DTO."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

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
    worker_schema_version: Literal[37] = 37
