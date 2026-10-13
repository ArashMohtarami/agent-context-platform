"""Public schemas for intentional memory."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.memory.types import MemorySearchResult
from app.models.actor import ActorProvenance
from app.models.common import ContentText, Identifier, ShortText, Tag, Tags
from app.models.memory import MemoryDocument, MemoryScope
from app.models.promotion import PromotionProvenance
from app.models.skill import LifecycleStatus
from app.schemas.skill import SearchQuery
from app.skills.types import RetrievalMethod

MAX_TAGS = 50
MAX_MEMORY_TYPES = 20
ObjectIdHex = Annotated[str, Field(pattern=r"^[0-9a-f]{24}$")]


def _unique(value: list[str]) -> list[str]:
    """Reject repeated values in a filter or tag list."""
    if len(value) != len(set(value)):
        raise ValueError("values must be unique")
    return value


class MemoryCreateRequest(BaseModel):
    """One memory to store on purpose.

    Memory is never written by watching a conversation; it is written because
    something asked for it, which is what this request is.
    """

    model_config = ConfigDict(extra="forbid")

    type: Identifier
    content: ContentText
    importance: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source: Identifier
    scope: MemoryScope | None = Field(
        default=None,
        description=(
            "Optional memory visibility. Omit for legacy shared global/project memory; "
            "agent scopes use the authenticated client identity."
        ),
    )
    project_id: Identifier | None = Field(
        default=None,
        description="Owning project. Omit for a global memory every project may see.",
    )
    summary: ShortText | None = Field(
        default=None,
        description="The memory in one line, used as its title in retrieved context.",
    )
    tags: Tags = Field(default_factory=list)
    status: LifecycleStatus = "active"
    external_id: Identifier | None = Field(
        default=None,
        description="Idempotency key. Repeating a create with it returns what is stored.",
    )
    supersedes: ObjectIdHex | None = Field(
        default=None,
        description="The memory this one corrects. It is marked superseded, never deleted.",
    )

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        """Reject ambiguous duplicate tags."""
        return _unique(value)

    @model_validator(mode="after")
    def validate_scope_address(self) -> "MemoryCreateRequest":
        if self.scope is None:
            return self
        requires_project = self.scope in {"project", "project_agent"}
        if (self.project_id is not None) != requires_project:
            raise ValueError(f"{self.scope} memory has an invalid project_id")
        return self


class MemoryUpdateRequest(BaseModel):
    """A partial update to one stored memory.

    Scope, the idempotency key, and both ends of a supersession link are
    identity rather than content, so none of them can be changed: a memory
    never moves between projects and never loses its history.
    """

    model_config = ConfigDict(extra="forbid")

    type: Identifier | None = None
    content: ContentText | None = None
    summary: ShortText | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
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
    def require_a_change(self) -> "MemoryUpdateRequest":
        """Reject an update that would change nothing."""
        if not any(
            value is not None
            for value in (
                self.type,
                self.content,
                self.summary,
                self.importance,
                self.confidence,
                self.tags,
                self.status,
            )
        ):
            raise ValueError("provide at least one field to update")
        return self


class MemoryResponse(BaseModel):
    """One stored memory, without its vector."""

    memory_id: str
    scope: MemoryScope
    project_id: str | None
    client_id: str | None
    type: str
    content: str
    summary: str | None
    importance: float
    confidence: float
    tags: tuple[str, ...]
    source: str
    status: LifecycleStatus
    external_id: str | None
    supersedes: str | None
    superseded_by: str | None
    promotion_provenance: PromotionProvenance | None
    actor: ActorProvenance
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: MemoryDocument) -> "MemoryResponse":
        """Build a response without exposing vectors or storage internals."""
        return cls(
            memory_id=str(document.id),
            scope=document.scope,
            project_id=document.project_id,
            client_id=document.client_id,
            type=document.type,
            content=document.content,
            summary=document.summary,
            importance=document.importance,
            confidence=document.confidence,
            tags=tuple(document.tags),
            source=document.source,
            status=document.status,
            external_id=document.external_id,
            supersedes=None if document.supersedes is None else str(document.supersedes),
            superseded_by=(None if document.superseded_by is None else str(document.superseded_by)),
            promotion_provenance=document.promotion_provenance,
            actor=document.actor,
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class MemoryListResponse(BaseModel):
    """Cursor-paginated memories for one scope."""

    scope: MemoryScope
    project_id: str | None
    client_id: str | None
    items: tuple[MemoryResponse, ...]
    next_cursor: str | None


class MemorySearchRequest(BaseModel):
    """A bounded memory search over the scopes a request may see."""

    model_config = ConfigDict(extra="forbid")

    query: SearchQuery
    project_id: Identifier | None = Field(
        default=None,
        description="Naming a project searches its memory and global memory together.",
    )
    scopes: list[MemoryScope] | None = Field(
        default=None,
        max_length=4,
        description="Restrict shared and authenticated-client memory scopes.",
    )
    types: list[Identifier] = Field(default_factory=list, max_length=MAX_MEMORY_TYPES)
    tags: Tags = Field(default_factory=list)
    status: LifecycleStatus | None = "active"
    minimum_importance: float = Field(default=0.0, ge=0.0, le=1.0)
    phrase: bool = Field(
        default=False,
        description="Require the query words to appear together, in order.",
    )
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("tags", "types", "scopes")
    @classmethod
    def unique_values(cls, value: list[str] | None) -> list[str] | None:
        """Reject repeated search filters."""
        return None if value is None else _unique(value)

    @model_validator(mode="after")
    def validate_scopes(self) -> "MemorySearchRequest":
        """Require a project whenever project memory is searched."""
        scopes = self.scopes
        if scopes is not None and not scopes:
            raise ValueError("provide at least one scope to search")
        wanted = scopes if scopes is not None else ["global", "project"]
        if (
            scopes is not None
            and any(scope in {"project", "project_agent"} for scope in wanted)
            and self.project_id is None
        ):
            raise ValueError("searching project-scoped memory requires project_id")
        return self


class MemorySearchResultResponse(BaseModel):
    """One ranked memory with the searches that found it."""

    source: Literal["memory"] = "memory"
    score: float
    methods: tuple[RetrievalMethod, ...]
    memory: MemoryResponse

    @classmethod
    def from_result(cls, result: MemorySearchResult) -> "MemorySearchResultResponse":
        """Build a scored result from one ranked memory."""
        return cls(
            score=result.score,
            methods=result.methods,
            memory=MemoryResponse.from_document(result.memory),
        )


class MemorySearchResponse(BaseModel):
    """Ranked memory in descending score order."""

    query: str
    project_id: str | None
    results: tuple[MemorySearchResultResponse, ...]
