"""Typed values passed through the skill ingestion and retrieval pipelines."""

from dataclasses import dataclass
from typing import Literal

from app.models.common import Page
from app.models.skill import SkillDocument
from app.models.skill_chunk import SkillChunkDocument
from app.schemas.skill import SkillFrontmatter


@dataclass(frozen=True, slots=True)
class ParsedSkill:
    """Validated canonical metadata and Markdown body."""

    metadata: SkillFrontmatter
    body: str
    source_path: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class MarkdownSection:
    """Markdown content grouped under one heading boundary."""

    heading: str | None
    heading_level: int | None
    heading_path: tuple[str, ...]
    content: str


@dataclass(frozen=True, slots=True)
class SkillChunkDraft:
    """A chunk ready to become a version-bound MongoDB document."""

    content: str
    section: str | None
    tokens: int
    heading_level: int | None
    heading_path: tuple[str, ...]
    overlap_tokens: int


type SkillImportAction = Literal["new", "updated", "unchanged"]
RetrievalMethod = Literal["vector", "lexical"]


@dataclass(frozen=True, slots=True)
class SkillImportOutcome:
    """Result of importing one canonical skill."""

    action: SkillImportAction
    skill_id: str
    version: str
    chunks_created: int
    chunks_embedded: int = 0


@dataclass(frozen=True, slots=True)
class SkillImportReport:
    """Aggregate CLI-safe importer statistics."""

    discovered: int
    new: int
    updated: int
    unchanged: int
    chunks_created: int
    chunks_embedded: int = 0


@dataclass(frozen=True, slots=True)
class SkillDetails:
    """Service result for a selected version and its history/chunk page."""

    skill: SkillDocument
    versions: tuple[SkillDocument, ...]
    chunks: Page[SkillChunkDocument]


@dataclass(frozen=True, slots=True)
class SkillLexicalMatch:
    """One active skill and literal-matching chunks."""

    skill: SkillDocument
    matched_chunks: tuple[SkillChunkDocument, ...]


@dataclass(frozen=True, slots=True)
class SkillVersionKey:
    """One immutable skill version, used to scope a chunk-level query."""

    skill_id: str
    version: str


@dataclass(frozen=True, slots=True)
class SkillChunkMatch:
    """One chunk returned by a search with its relevance score."""

    chunk: SkillChunkDocument
    score: float

    @property
    def key(self) -> SkillVersionKey:
        """Return the version this chunk belongs to."""
        return SkillVersionKey(self.chunk.skill_id, self.chunk.skill_version)


@dataclass(frozen=True, slots=True)
class SkillChunkSearchResult:
    """A scored chunk with the skill metadata that produced it.

    Vector and lexical retrieval share this shape so a later phase can merge
    and rank them without translating between two result models.
    """

    skill: SkillDocument
    chunk: SkillChunkDocument
    score: float
    method: RetrievalMethod
