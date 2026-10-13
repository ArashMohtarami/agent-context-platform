"""REST contracts for V4's deterministic project-binding resolver."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.agent import (
    ProjectBindingDocument,
    ProjectBindingType,
    ProjectResolutionMatch,
    ProjectResolutionStatus,
)
from app.models.common import Identifier
from app.services.project_binding_service import ProjectResolution


class ProjectBindingCreateRequest(BaseModel):
    """Create an explicit identifier-to-project mapping."""

    model_config = ConfigDict(extra="forbid")

    project_id: Identifier
    binding_type: ProjectBindingType
    value: str = Field(min_length=1, max_length=2_048)


class ProjectBindingUpdateRequest(BaseModel):
    """Change a binding only with the revision the caller reviewed."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    project_id: Identifier | None = None
    binding_type: ProjectBindingType | None = None
    value: str | None = Field(default=None, min_length=1, max_length=2_048)

    @model_validator(mode="after")
    def require_change(self) -> "ProjectBindingUpdateRequest":
        if self.project_id is None and self.binding_type is None and self.value is None:
            raise ValueError("provide at least one project binding field to update")
        return self


class ProjectBindingResponse(BaseModel):
    """A binding's administrative metadata, excluding MongoDB internals."""

    binding_id: str
    project_id: str
    binding_type: ProjectBindingType
    value: str
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: ProjectBindingDocument) -> "ProjectBindingResponse":
        return cls(
            binding_id=document.binding_id,
            project_id=document.project_id,
            binding_type=document.binding_type,
            value=document.value,
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ProjectBindingListResponse(BaseModel):
    """Cursor-paginated authorized binding metadata."""

    items: tuple[ProjectBindingResponse, ...]
    next_cursor: str | None


class ProjectResolutionRequest(BaseModel):
    """Optional local metadata supplied by a client; no server filesystem use."""

    model_config = ConfigDict(extra="forbid")

    project_id: Identifier | None = None
    git_remote: str | None = Field(default=None, min_length=1, max_length=2_048)
    repository_url: str | None = Field(default=None, min_length=1, max_length=2_048)
    repository_name: str | None = Field(default=None, min_length=1, max_length=2_048)
    workspace_hint: str | None = Field(default=None, min_length=1, max_length=2_048)
    # Preserve the original V4 field while accepting the binding terminology
    # used by MCP-compatible clients.
    workspace_alias: str | None = Field(default=None, min_length=1, max_length=2_048)

    @model_validator(mode="after")
    def require_identifier(self) -> "ProjectResolutionRequest":
        if (
            self.workspace_hint is not None
            and self.workspace_alias is not None
            and self.workspace_hint != self.workspace_alias
        ):
            raise ValueError("workspace_hint and workspace_alias must match when both are provided")
        if all(
            value is None
            for value in (
                self.project_id,
                self.git_remote,
                self.repository_url,
                self.repository_name,
                self.workspace_hint,
                self.workspace_alias,
            )
        ):
            raise ValueError(
                "provide an explicit project_id or one repository/workspace identifier"
            )
        return self


class ProjectResolutionCandidateResponse(BaseModel):
    """One candidate retained instead of being selected silently."""

    project_id: str
    matched_by: ProjectResolutionMatch
    confidence: float


class ProjectResolutionResponse(BaseModel):
    """A deterministic resolution outcome."""

    status: ProjectResolutionStatus
    project_id: str | None
    confidence: float
    matched_by: ProjectResolutionMatch | None
    candidates: tuple[ProjectResolutionCandidateResponse, ...]

    @classmethod
    def from_resolution(cls, resolution: ProjectResolution) -> "ProjectResolutionResponse":
        return cls(
            status=resolution.status,
            project_id=resolution.project_id,
            confidence=resolution.confidence,
            matched_by=resolution.matched_by,
            candidates=tuple(
                ProjectResolutionCandidateResponse(
                    project_id=candidate.project_id,
                    matched_by=candidate.matched_by,
                    confidence=candidate.confidence,
                )
                for candidate in resolution.candidates
            ),
        )
