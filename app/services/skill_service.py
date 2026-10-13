"""Business rules for versioned skill import, lookup, and lexical matching."""

from collections.abc import Sequence
from typing import Protocol

from app.embeddings.errors import EmbeddingCompatibilityError
from app.embeddings.types import EmbeddingVector
from app.models.common import Page, utc_now
from app.models.skill import LifecycleStatus, SkillDocument, SkillTrustLevel
from app.models.skill_chunk import SkillChunkDocument
from app.skills.errors import SkillChunkingError, SkillNotFoundError, SkillVersionConflictError
from app.skills.types import (
    ParsedSkill,
    SkillChunkDraft,
    SkillDetails,
    SkillImportOutcome,
    SkillLexicalMatch,
)

_MAX_CHUNKS_PER_SKILL_VERSION = 512


class SkillRepository(Protocol):
    """Persistence required by the skill service."""

    async def get(self, skill_id: str, version: str) -> SkillDocument | None: ...

    async def get_active(self, skill_id: str) -> SkillDocument | None: ...

    async def list(
        self,
        *,
        status: LifecycleStatus | None = None,
        category: str | None = None,
        tag: str | None = None,
        trust_level: SkillTrustLevel | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[SkillDocument]: ...

    async def list_versions(
        self,
        skill_id: str,
        *,
        limit: int = 100,
    ) -> tuple[SkillDocument, ...]: ...

    async def search_active_lexical(
        self,
        query: str,
        *,
        category: str | None = None,
        tags: Sequence[str] = (),
        trust_level: SkillTrustLevel | None = None,
        limit: int = 10,
    ) -> tuple[SkillLexicalMatch, ...]: ...

    async def install_version(
        self,
        skill: SkillDocument,
        chunks: Sequence[SkillChunkDocument],
    ) -> bool: ...


class SkillChunkRepository(Protocol):
    """Chunk reads required to return a selected skill version."""

    async def list_for_version(
        self,
        skill_id: str,
        skill_version: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[SkillChunkDocument]: ...


class ChunkEmbedder(Protocol):
    """Vector generation required to store embedded skill chunks."""

    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]: ...


class SkillService:
    """Keep skill lifecycle decisions outside HTTP, CLI, and MongoDB details."""

    def __init__(
        self,
        skills: SkillRepository,
        chunks: SkillChunkRepository,
        embedder: ChunkEmbedder | None = None,
    ) -> None:
        self._skills = skills
        self._chunks = chunks
        self._embedder = embedder

    async def import_skill(
        self,
        parsed: ParsedSkill,
        chunk_drafts: Sequence[SkillChunkDraft],
    ) -> SkillImportOutcome:
        """Install a new immutable version or report an idempotent retry."""
        metadata = parsed.metadata
        existing = await self._skills.get(metadata.id, metadata.version)
        if existing is not None:
            return self._classify_existing(parsed, existing)
        if not chunk_drafts or len(chunk_drafts) > _MAX_CHUNKS_PER_SKILL_VERSION:
            raise SkillChunkingError("skill must produce between 1 and 512 chunks")

        active = await self._skills.get_active(metadata.id)
        skill = SkillDocument(
            skill_id=metadata.id,
            name=metadata.name,
            version=metadata.version,
            description=metadata.description,
            category=metadata.category,
            tags=list(metadata.tags),
            trust_level=metadata.trust_level,
            status="active",
            source_path=parsed.source_path,
            content_hash=parsed.content_hash,
        )
        vectors = await self._embed(chunk_drafts)
        generated_at = utc_now()
        chunks = tuple(
            SkillChunkDocument(
                skill_id=metadata.id,
                skill_version=metadata.version,
                chunk_id=f"chunk-{index:04d}",
                content=draft.content,
                section=draft.section,
                chunk_index=index,
                tokens=draft.tokens,
                metadata={
                    "heading_level": draft.heading_level,
                    "heading_path": list(draft.heading_path),
                    "overlap_tokens": draft.overlap_tokens,
                    "source_path": parsed.source_path,
                },
                embedding=None if vector is None else list(vector.values),
                embedding_model=None if vector is None else vector.model,
                embedding_dimensions=None if vector is None else vector.dimensions,
                embedding_generated_at=None if vector is None else generated_at,
            )
            for index, (draft, vector) in enumerate(zip(chunk_drafts, vectors, strict=True))
        )
        created = await self._skills.install_version(skill, chunks)
        if not created:
            concurrent = await self._skills.get(metadata.id, metadata.version)
            if concurrent is None:
                raise SkillVersionConflictError("skill version changed concurrently")
            return self._classify_existing(parsed, concurrent)
        return SkillImportOutcome(
            action="updated" if active is not None else "new",
            skill_id=skill.skill_id,
            version=skill.version,
            chunks_created=len(chunks),
            chunks_embedded=sum(1 for chunk in chunks if chunk.has_embedding),
        )

    async def _embed(
        self,
        chunk_drafts: Sequence[SkillChunkDraft],
    ) -> tuple[EmbeddingVector | None, ...]:
        """Embed every chunk of one version, or none of them."""
        if self._embedder is None:
            return (None,) * len(chunk_drafts)
        vectors = await self._embedder.embed_chunks([draft.content for draft in chunk_drafts])
        if len(vectors) != len(chunk_drafts):
            raise EmbeddingCompatibilityError(
                "the embedder returned a different number of vectors than chunks"
            )
        return vectors

    @staticmethod
    def _classify_existing(
        parsed: ParsedSkill,
        existing: SkillDocument,
    ) -> SkillImportOutcome:
        if (
            existing.content_hash == parsed.content_hash
            and existing.source_path == parsed.source_path
            and existing.status == "active"
        ):
            return SkillImportOutcome(
                action="unchanged",
                skill_id=existing.skill_id,
                version=existing.version,
                chunks_created=0,
            )
        raise SkillVersionConflictError(
            f"{existing.skill_id} {existing.version} already exists with different "
            "content, source, or lifecycle state; declare a new version"
        )

    async def list_skills(
        self,
        *,
        status: LifecycleStatus | None = "active",
        category: str | None = None,
        tag: str | None = None,
        trust_level: SkillTrustLevel | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[SkillDocument]:
        """List bounded skill metadata with optional lifecycle filters."""
        return await self._skills.list(
            status=status,
            category=category,
            tag=tag,
            trust_level=trust_level,
            limit=limit,
            cursor=cursor,
        )

    async def get_skill(
        self,
        skill_id: str,
        *,
        version: str | None = None,
        chunk_limit: int = 20,
        chunk_cursor: str | None = None,
    ) -> SkillDetails:
        """Load one requested/active version with history and a bounded chunk page."""
        if version is None:
            skill = await self._skills.get_active(skill_id)
        else:
            skill = await self._skills.get(skill_id, version)
        if skill is None:
            raise SkillNotFoundError("skill or requested version was not found")
        versions = await self._skills.list_versions(skill_id)
        chunks = await self._chunks.list_for_version(
            skill.skill_id,
            skill.version,
            limit=chunk_limit,
            cursor=chunk_cursor,
        )
        return SkillDetails(skill=skill, versions=versions, chunks=chunks)

    async def search_skills(
        self,
        query: str,
        *,
        category: str | None = None,
        tags: Sequence[str] = (),
        trust_level: SkillTrustLevel | None = None,
        limit: int = 10,
    ) -> tuple[SkillLexicalMatch, ...]:
        """Search active skill metadata and chunks using escaped literal terms only."""
        normalized = " ".join(query.split())
        if not normalized:
            raise ValueError("search query must not be empty")
        return await self._skills.search_active_lexical(
            normalized,
            category=category,
            tags=tags,
            trust_level=trust_level,
            limit=limit,
        )
