"""Validated skill frontmatter and public API schemas."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.models.common import Identifier, Metadata, ShortText, Tags
from app.models.skill import LifecycleStatus, SemanticVersion, SkillDocument, SkillTrustLevel
from app.models.skill_chunk import SkillChunkDocument

Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000),
]
SearchQuery = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]


class SkillFrontmatter(BaseModel):
    """Required metadata at the start of every canonical Markdown skill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Identifier
    name: ShortText
    version: SemanticVersion
    description: Description
    category: Identifier
    trust_level: SkillTrustLevel
    tags: Tags = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        """Reject ambiguous duplicate metadata tags."""
        if len(value) != len(set(value)):
            raise ValueError("tags must be unique")
        return value


class SkillSummary(BaseModel):
    """Public metadata for one persisted skill version."""

    skill_id: str
    name: str
    version: str
    description: str
    category: str
    tags: tuple[str, ...]
    trust_level: SkillTrustLevel
    status: LifecycleStatus
    is_active: bool
    source_path: str
    content_hash: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: SkillDocument) -> "SkillSummary":
        """Build a response without exposing BSON identifiers or revisions."""
        return cls(
            skill_id=document.skill_id,
            name=document.name,
            version=document.version,
            description=document.description,
            category=document.category,
            tags=tuple(document.tags),
            trust_level=document.trust_level,
            status=document.status,
            is_active=document.status == "active",
            source_path=document.source_path,
            content_hash=document.content_hash,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class SkillVersionSummary(BaseModel):
    """Lifecycle information for a stored historical version."""

    version: str
    status: LifecycleStatus
    is_active: bool
    content_hash: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: SkillDocument) -> "SkillVersionSummary":
        """Build a compact version-history item."""
        return cls(
            version=document.version,
            status=document.status,
            is_active=document.status == "active",
            content_hash=document.content_hash,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class SkillChunkResponse(BaseModel):
    """One section-aware persisted skill chunk."""

    chunk_id: str
    content: str
    section: str | None
    chunk_index: int
    tokens: int
    metadata: Metadata

    @classmethod
    def from_document(cls, document: SkillChunkDocument) -> "SkillChunkResponse":
        """Build a chunk response without exposing persistence internals."""
        return cls(
            chunk_id=document.chunk_id,
            content=document.content,
            section=document.section,
            chunk_index=document.chunk_index,
            tokens=document.tokens,
            metadata=document.metadata,
        )


class SkillListResponse(BaseModel):
    """Cursor-paginated skill metadata."""

    items: tuple[SkillSummary, ...]
    next_cursor: str | None


class SkillDetailResponse(BaseModel):
    """Selected skill version, its chunks, and preserved version history."""

    skill: SkillSummary
    versions: tuple[SkillVersionSummary, ...]
    chunks: tuple[SkillChunkResponse, ...]
    next_chunk_cursor: str | None


class SkillSearchRequest(BaseModel):
    """Bounded filters for Phase 4 literal metadata/content matching."""

    model_config = ConfigDict(extra="forbid")

    query: SearchQuery
    category: Identifier | None = None
    tags: Tags = Field(default_factory=list)
    trust_level: SkillTrustLevel | None = None
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        """Reject repeated search filters."""
        if len(value) != len(set(value)):
            raise ValueError("tags must be unique")
        return value


class SkillSearchResult(BaseModel):
    """An active skill plus the chunks containing literal query terms."""

    skill: SkillSummary
    matched_chunks: tuple[SkillChunkResponse, ...]


class SkillSearchResponse(BaseModel):
    """Bounded Phase 4 lexical search results."""

    results: tuple[SkillSearchResult, ...]
