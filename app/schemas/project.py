"""Public schemas for project boundaries."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.common import Identifier, ShortText, Tags
from app.models.project import ProjectDocument


class ProjectCreateRequest(BaseModel):
    """One project boundary to create."""

    model_config = ConfigDict(extra="forbid")

    project_id: Identifier = Field(
        description="Stable identifier every context item is filed under.",
    )
    name: ShortText
    description: str = Field(min_length=1, max_length=4_000)
    repository: str = Field(min_length=1, max_length=2_048)
    environment: Identifier
    tags: Tags = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        """Reject ambiguous duplicate tags."""
        if len(value) != len(set(value)):
            raise ValueError("tags must be unique")
        return value


class ProjectResponse(BaseModel):
    """Public metadata for one project."""

    project_id: str
    name: str
    description: str
    repository: str
    environment: str
    tags: tuple[str, ...]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: ProjectDocument) -> "ProjectResponse":
        """Build a response without exposing BSON identifiers or revisions."""
        return cls(
            project_id=document.project_id,
            name=document.name,
            description=document.description,
            repository=document.repository,
            environment=document.environment,
            tags=tuple(document.tags),
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ProjectListResponse(BaseModel):
    """Cursor-paginated project metadata."""

    items: tuple[ProjectResponse, ...]
    next_cursor: str | None
