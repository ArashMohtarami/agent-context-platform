"""Public schemas for durable project context."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.actor import ActorProvenance
from app.models.common import ContentText, Identifier, ShortText, Tag, Tags
from app.models.project_context import ContextType, ProjectContextDocument
from app.models.promotion import PromotionProvenance
from app.models.skill import LifecycleStatus
from app.projects.types import ContextSearchResult
from app.schemas.skill import SearchQuery
from app.skills.types import RetrievalMethod

MAX_CONTEXT_TYPES = 14
MAX_TAGS = 50


def _unique(value: list[str]) -> list[str]:
    """Reject repeated values in a filter or tag list."""
    if len(value) != len(set(value)):
        raise ValueError("values must be unique")
    return value


class ContextCreateRequest(BaseModel):
    """One durable context item to store in a project."""

    model_config = ConfigDict(extra="forbid")

    project_id: Identifier
    type: ContextType
    title: ShortText
    content: ContentText
    importance: float = Field(ge=0.0, le=1.0)
    source: Identifier
    tags: Tags = Field(default_factory=list)
    status: LifecycleStatus = "active"
    external_id: Identifier | None = Field(
        default=None,
        description="Idempotency key. Repeating a create with it returns what is stored.",
    )

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        """Reject ambiguous duplicate tags."""
        return _unique(value)


class ContextUpdateRequest(BaseModel):
    """A partial update to one stored context item.

    The project and the idempotency key are identity rather than content, so
    neither can be changed: context never moves between projects.
    """

    model_config = ConfigDict(extra="forbid")

    type: ContextType | None = None
    title: ShortText | None = None
    content: ContentText | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    # Spelled out rather than reusing the Tags alias: its default factory has
    # no meaning inside a union, where absent means "leave the tags alone".
    tags: Annotated[list[Tag], Field(max_length=MAX_TAGS)] | None = None
    status: LifecycleStatus | None = None
    expected_revision: int = Field(
        ge=1,
        description=(
            "Required revision last read. A mismatch reports a conflict instead of overwriting."
        ),
    )

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str] | None) -> list[str] | None:
        """Reject ambiguous duplicate tags."""
        return None if value is None else _unique(value)

    @model_validator(mode="after")
    def require_a_change(self) -> "ContextUpdateRequest":
        """Reject an update that would change nothing."""
        if not any(
            value is not None
            for value in (
                self.type,
                self.title,
                self.content,
                self.importance,
                self.tags,
                self.status,
            )
        ):
            raise ValueError("provide at least one field to update")
        return self


class ContextResponse(BaseModel):
    """One stored context item, without its vector."""

    context_id: str
    project_id: str
    type: ContextType
    title: str
    content: str
    importance: float
    tags: tuple[str, ...]
    source: str
    status: LifecycleStatus
    external_id: str | None
    promotion_provenance: PromotionProvenance | None
    actor: ActorProvenance
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: ProjectContextDocument) -> "ContextResponse":
        """Build a response without exposing vectors or storage internals."""
        return cls(
            context_id=str(document.id),
            project_id=document.project_id,
            type=document.type,
            title=document.title,
            content=document.content,
            importance=document.importance,
            tags=tuple(document.tags),
            source=document.source,
            status=document.status,
            external_id=document.external_id,
            promotion_provenance=document.promotion_provenance,
            actor=document.actor,
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ContextListResponse(BaseModel):
    """Cursor-paginated context for one project."""

    project_id: str
    items: tuple[ContextResponse, ...]
    next_cursor: str | None


class ContextSearchRequest(BaseModel):
    """A bounded context search inside exactly one project."""

    model_config = ConfigDict(extra="forbid")

    project_id: Identifier = Field(
        description="The only project this search can return context from.",
    )
    query: SearchQuery
    types: list[ContextType] = Field(default_factory=list, max_length=MAX_CONTEXT_TYPES)
    tags: Tags = Field(default_factory=list)
    status: LifecycleStatus | None = "active"
    minimum_importance: float = Field(default=0.0, ge=0.0, le=1.0)
    phrase: bool = Field(
        default=False,
        description="Require the query words to appear together, in order.",
    )
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("tags", "types")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        """Reject repeated search filters."""
        return _unique(value)


class ContextSearchResultResponse(BaseModel):
    """One ranked context item with the searches that found it."""

    source: Literal["project_context"] = "project_context"
    score: float
    methods: tuple[RetrievalMethod, ...]
    context: ContextResponse

    @classmethod
    def from_result(cls, result: ContextSearchResult) -> "ContextSearchResultResponse":
        """Build a scored result from one ranked context document."""
        return cls(
            score=result.score,
            methods=result.methods,
            context=ContextResponse.from_document(result.context),
        )


class ContextSearchResponse(BaseModel):
    """Ranked context from one project, in descending score order."""

    project_id: str
    query: str
    results: tuple[ContextSearchResultResponse, ...]
