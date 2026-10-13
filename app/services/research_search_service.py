"""V3 Phase 5 research retrieval over preserved evidence and existing V1 knowledge.

This service composes project-bound MongoDB search adapters with the existing
local embedding and hybrid-ranking primitives.  It never acquires content,
evaluates claims, changes research records, or promotes anything into durable
memory, context, or skills.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final, Literal, Protocol, cast
from urllib.parse import urlsplit

from app.db.errors import SearchUnavailableError
from app.db.research_search import RESEARCH_SEARCH_LIMIT, ResearchSearchScope
from app.embeddings.errors import EmbeddingUnavailableError
from app.embeddings.types import EmbeddingVector
from app.models.common import utc_now
from app.models.research import (
    ResearchAuthorization,
    ResearchClaimDocument,
    ResearchNoteDocument,
    ResearchProjectDocument,
    ResearchSourceDocument,
    ResearchSourceType,
    SourceClass,
    SourceSnapshotDocument,
)
from app.repositories.research_repository import (
    ResearchChunkSearchMatch,
    ResearchClaimSearchMatch,
    ResearchNoteSearchMatch,
)
from app.retrieval.context_builder import MAX_TOKEN_BUDGET, resolve_token_budget
from app.retrieval.hybrid import DEFAULT_DUPLICATE_OVERLAP, hybrid_rank
from app.retrieval.query import normalize_query
from app.retrieval.ranking import DEFAULT_RECENCY_HALF_LIFE_DAYS, DEFAULT_WEIGHTS, RankingWeights
from app.retrieval.types import (
    RankedResult,
    RetrievalCandidate,
    RetrievalMatch,
    SourceMatches,
    SourceType,
)
from app.services.retrieval_service import RetrievalService
from app.skills.chunker import estimate_tokens
from app.skills.types import RetrievalMethod

ResearchRetrievalProfileName = Literal[
    "research_general",
    "research_primary_sources",
    "research_recent",
    "research_content",
    "research_architecture",
    "research_code",
]
ResearchSearchMethod = Literal["vector", "lexical", "hybrid"]
ResearchSearchKind = Literal["chunk", "note", "claim", "skill", "project_context", "memory"]

# ``research_code`` reuses the same project-bound chunk adapter, local
# embedding provider, and mongot indexes. Repository provenance is carried by
# the persisted chunk locator/metadata, not by a second retrieval system.


class ResearchSearchError(ValueError):
    """Base error for bounded, non-mutating research search input."""


class ResearchSearchUnavailableError(ResearchSearchError):
    """The configured local embedding or mongot search index cannot serve a request."""


@dataclass(frozen=True, slots=True)
class ResearchSearchFilters:
    """Validated source and temporal constraints applied before a result is returned.

    Source class remains descriptive provenance metadata.  It is accepted as a
    retrieval preference/filter, never converted into a confidence assertion.
    """

    source_classes: tuple[SourceClass, ...] = ()
    source_types: tuple[ResearchSourceType, ...] = ()
    publisher: str | None = None
    domain: str | None = None
    published_after: datetime | None = None
    published_before: datetime | None = None
    retrieved_after: datetime | None = None
    as_of: datetime | None = None
    related_project_id: str | None = None

    def __post_init__(self) -> None:
        if len(self.source_classes) != len(set(self.source_classes)):
            raise ResearchSearchError("research source classes must be unique")
        if len(self.source_types) != len(set(self.source_types)):
            raise ResearchSearchError("research source types must be unique")
        for name, value in (
            ("published_after", self.published_after),
            ("published_before", self.published_before),
            ("retrieved_after", self.retrieved_after),
            ("as_of", self.as_of),
        ):
            if value is not None and value.tzinfo is None:
                raise ResearchSearchError(f"{name} must be timezone aware")
        if (
            self.published_after is not None
            and self.published_before is not None
            and self.published_after > self.published_before
        ):
            raise ResearchSearchError("published_after must not be after published_before")

    @property
    def has_external_source_filter(self) -> bool:
        """Whether a result must have registered external-source provenance."""
        return bool(
            self.source_classes
            or self.source_types
            or self.publisher is not None
            or self.domain is not None
            or self.published_after is not None
            or self.published_before is not None
            or self.retrieved_after is not None
        )


@dataclass(frozen=True, slots=True)
class ResearchRetrievalProfile:
    """A named ranking policy; weights are retrieval preferences, not truth scores."""

    name: ResearchRetrievalProfileName
    source_priorities: Mapping[SourceType, float]
    source_classes: tuple[SourceClass, ...] = ()
    include_v1: bool = True
    recency_half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS
    default_token_budget: int = 8_000

    def __post_init__(self) -> None:
        if not self.source_priorities:
            raise ValueError("a research retrieval profile requires source priorities")
        if self.recency_half_life_days <= 0:
            raise ValueError("research recency half life must be positive")
        resolve_token_budget(
            self.default_token_budget,
            default=self.default_token_budget,
            maximum=MAX_TOKEN_BUDGET,
        )
        for value in self.source_priorities.values():
            if not 0.0 <= value <= 1.0:
                raise ValueError("research source priorities must be between 0 and 1")


_BASE_PRIORITIES: Final[Mapping[SourceType, float]] = MappingProxyType(
    {
        # These are deliberately weak ranking preferences.  They do not say a
        # primary or official source is true, current, complete, or applicable.
        "research_primary": 1.00,
        "research_official_documentation": 1.00,
        "research_source_code": 0.95,
        "research_standard": 0.95,
        "research_academic": 0.90,
        "project_context": 0.90,
        "active_skill": 0.85,
        "memory": 0.80,
        "research_secondary": 0.75,
        "research_community": 0.60,
        "research_generated": 0.40,
        "research_unknown": 0.50,
        "research_note": 0.55,
        "research_claim": 0.55,
        "conversation": 0.50,
    }
)
_SOURCE_CLASS_TO_SOURCE: Final[Mapping[SourceClass, SourceType]] = MappingProxyType(
    {
        SourceClass.PRIMARY: "research_primary",
        SourceClass.OFFICIAL_DOCUMENTATION: "research_official_documentation",
        SourceClass.SOURCE_CODE: "research_source_code",
        SourceClass.STANDARD: "research_standard",
        SourceClass.ACADEMIC: "research_academic",
        SourceClass.SECONDARY: "research_secondary",
        SourceClass.COMMUNITY: "research_community",
        SourceClass.GENERATED: "research_generated",
        SourceClass.USER_PROVIDED: "research_unknown",
        SourceClass.UNKNOWN: "research_unknown",
    }
)
_PRIMARY_CLASSES: Final[tuple[SourceClass, ...]] = (
    SourceClass.PRIMARY,
    SourceClass.OFFICIAL_DOCUMENTATION,
    SourceClass.SOURCE_CODE,
    SourceClass.STANDARD,
    SourceClass.ACADEMIC,
)


def default_research_retrieval_profiles(
    *, token_budget: int = 8_000
) -> Mapping[ResearchRetrievalProfileName, ResearchRetrievalProfile]:
    """Return V3-only profiles without changing the established V1 ranking policy."""
    content_priorities = dict(_BASE_PRIORITIES)
    content_priorities.update({"research_secondary": 0.85, "research_community": 0.70})
    architecture_priorities = dict(_BASE_PRIORITIES)
    architecture_priorities.update(
        {
            "research_official_documentation": 1.00,
            "research_source_code": 1.00,
            "research_standard": 1.00,
            "research_primary": 0.95,
        }
    )
    code_priorities = dict(_BASE_PRIORITIES)
    code_priorities.update(
        {
            "research_source_code": 1.00,
            "research_official_documentation": 0.85,
            "research_primary": 0.80,
        }
    )
    return MappingProxyType(
        {
            "research_general": ResearchRetrievalProfile(
                "research_general", _BASE_PRIORITIES, default_token_budget=token_budget
            ),
            "research_primary_sources": ResearchRetrievalProfile(
                "research_primary_sources",
                _BASE_PRIORITIES,
                source_classes=_PRIMARY_CLASSES,
                include_v1=False,
                default_token_budget=token_budget,
            ),
            "research_recent": ResearchRetrievalProfile(
                "research_recent",
                _BASE_PRIORITIES,
                recency_half_life_days=21.0,
                default_token_budget=token_budget,
            ),
            "research_content": ResearchRetrievalProfile(
                "research_content",
                MappingProxyType(content_priorities),
                default_token_budget=token_budget,
            ),
            "research_architecture": ResearchRetrievalProfile(
                "research_architecture",
                MappingProxyType(architecture_priorities),
                default_token_budget=token_budget,
            ),
            "research_code": ResearchRetrievalProfile(
                "research_code",
                MappingProxyType(code_priorities),
                source_classes=(SourceClass.SOURCE_CODE,),
                include_v1=False,
                default_token_budget=token_budget,
            ),
        }
    )


class QueryEmbedder(Protocol):
    """The existing local embedding surface used by V1 and V3."""

    async def embed_query(self, text: str) -> EmbeddingVector: ...


class ResearchChunkSearchRepository(Protocol):
    research_project_id: str

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        num_candidates: int,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchChunkSearchMatch, ...]: ...

    async def text_search(
        self,
        query: str,
        *,
        limit: int,
        phrase: bool,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchChunkSearchMatch, ...]: ...


class ResearchNoteSearchRepository(Protocol):
    research_project_id: str

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        num_candidates: int,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchNoteSearchMatch, ...]: ...

    async def text_search(
        self,
        query: str,
        *,
        limit: int,
        phrase: bool,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchNoteSearchMatch, ...]: ...


class ResearchClaimSearchRepository(Protocol):
    research_project_id: str

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        num_candidates: int,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchClaimSearchMatch, ...]: ...

    async def text_search(
        self,
        query: str,
        *,
        limit: int,
        phrase: bool,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchClaimSearchMatch, ...]: ...


class ResearchSourceLookup(Protocol):
    async def get_many(self, source_ids: Sequence[str]) -> tuple[ResearchSourceDocument, ...]: ...


class SourceSnapshotLookup(Protocol):
    async def get_many(
        self, source_snapshot_pairs: Sequence[tuple[str, str]]
    ) -> tuple[SourceSnapshotDocument, ...]: ...


ResearchRepositoryFactory = Callable[[str], ResearchChunkSearchRepository]
ResearchNoteRepositoryFactory = Callable[[str], ResearchNoteSearchRepository]
ResearchClaimRepositoryFactory = Callable[[str], ResearchClaimSearchRepository]
ResearchSourceRepositoryFactory = Callable[[str], ResearchSourceLookup]
SourceSnapshotRepositoryFactory = Callable[[str], SourceSnapshotLookup]


@dataclass(frozen=True, slots=True)
class ResearchSearchItem:
    """One budgeted retrieval result with immutable evidence provenance where available."""

    kind: ResearchSearchKind
    identifier: str
    source: SourceType
    title: str
    content: str
    status: str | None
    source_id: str | None
    snapshot_id: str | None
    document_id: str | None
    source_type: ResearchSourceType | None
    source_class: SourceClass | None
    publisher: str | None
    domain: str | None
    published_at: datetime | None
    retrieved_at: datetime | None
    locator: Mapping[str, object] | None
    related_project_ids: tuple[str, ...]
    retrieval_score: float
    methods: tuple[RetrievalMethod, ...]
    tokens: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ResearchSearchResult:
    """A read-only, budgeted research packet; scores are relevance only."""

    research_project_id: str
    retrieval_profile: ResearchRetrievalProfileName
    method: ResearchSearchMethod
    items: tuple[ResearchSearchItem, ...]
    token_budget: int
    tokens_used: int
    next_cursor: str | None
    diagnostics: Mapping[str, object]


class ResearchSearchService:
    """Search V3 evidence first, then authorized V1 knowledge through its public service."""

    def __init__(
        self,
        chunks: ResearchRepositoryFactory,
        notes: ResearchNoteRepositoryFactory,
        claims: ResearchClaimRepositoryFactory,
        sources: ResearchSourceRepositoryFactory,
        snapshots: SourceSnapshotRepositoryFactory,
        v1_retrieval: RetrievalService,
        embeddings: QueryEmbedder | None = None,
        *,
        profiles: Mapping[ResearchRetrievalProfileName, ResearchRetrievalProfile] | None = None,
        candidate_multiplier: int = 10,
        max_candidates: int = 1_000,
        weights: RankingWeights = DEFAULT_WEIGHTS,
        duplicate_overlap: float = DEFAULT_DUPLICATE_OVERLAP,
        token_budget: int = 8_000,
        max_token_budget: int = MAX_TOKEN_BUDGET,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if candidate_multiplier < 1 or max_candidates < 1:
            raise ValueError("research retrieval candidate limits must be positive")
        resolve_token_budget(None, default=token_budget, maximum=max_token_budget)
        self._chunks = chunks
        self._notes = notes
        self._claims = claims
        self._sources = sources
        self._snapshots = snapshots
        self._v1_retrieval = v1_retrieval
        self._embeddings = embeddings
        self._profiles = profiles or default_research_retrieval_profiles(token_budget=token_budget)
        self._candidate_multiplier = candidate_multiplier
        self._max_candidates = max_candidates
        self._weights = weights
        self._duplicate_overlap = duplicate_overlap
        self._token_budget = token_budget
        self._max_token_budget = max_token_budget
        self._clock = clock

    async def search(
        self,
        *,
        project: ResearchProjectDocument,
        query: str,
        filters: ResearchSearchFilters | None = None,
        retrieval_profile: ResearchRetrievalProfileName = "research_general",
        method: ResearchSearchMethod = "hybrid",
        phrase: bool = False,
        limit: int = 10,
        token_budget: int | None = None,
        cursor: str | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
    ) -> ResearchSearchResult:
        """Return only authorized evidence, ranked for relevance and bounded by tokens."""
        filters = ResearchSearchFilters() if filters is None else filters
        if retrieval_profile not in self._profiles:
            raise ResearchSearchError("unknown research retrieval profile")
        if not 1 <= limit <= RESEARCH_SEARCH_LIMIT:
            raise ResearchSearchError(
                f"research search limit must be between 1 and {RESEARCH_SEARCH_LIMIT}"
            )
        if (
            filters.related_project_id is not None
            and filters.related_project_id not in project.related_project_ids
        ):
            raise ResearchSearchError("related_project_id is not linked to this research project")
        profile = self._profiles[retrieval_profile]
        normalized = normalize_query(query)
        budget = resolve_token_budget(
            token_budget,
            default=profile.default_token_budget,
            maximum=self._max_token_budget,
        )
        effective_classes = _effective_source_classes(profile, filters)
        fetch_limit = min(
            RESEARCH_SEARCH_LIMIT,
            max(limit, limit * self._candidate_multiplier),
        )
        num_candidates = min(
            self._max_candidates,
            max(fetch_limit, fetch_limit * self._candidate_multiplier),
        )
        methods = _selected_methods(method)
        try:
            vector, lexical = await self._search_all(
                project=project,
                query=normalized,
                filters=filters,
                effective_classes=effective_classes,
                profile=profile,
                methods=methods,
                phrase=phrase,
                fetch_limit=fetch_limit,
                num_candidates=num_candidates,
                allowed_project_ids=allowed_project_ids,
            )
        except SearchUnavailableError as error:
            raise ResearchSearchUnavailableError(
                "research retrieval is temporarily unavailable"
            ) from error
        outcome = hybrid_rank(
            vector,
            lexical,
            methods=methods,
            now=self._clock(),
            filters=None,
            weights=self._weights,
            half_life_days=profile.recency_half_life_days,
            duplicate_overlap=self._duplicate_overlap,
            limit=RESEARCH_SEARCH_LIMIT,
            source_priorities=profile.source_priorities,
        )
        diversified, deferred = _diversify(outcome.results)
        remaining = _after_cursor(diversified, cursor)
        selected: list[ResearchSearchItem] = []
        used = 0
        examined = 0
        for ranked in remaining:
            examined += 1
            item = _result_item(ranked.candidate, ranked.score, ranked.breakdown.methods)
            if item.tokens <= budget - used:
                selected.append(item)
                used += item.tokens
            if len(selected) == limit:
                break
        # The cursor advances over an oversized candidate too.  Otherwise a
        # caller with a small packet budget would receive the same unfit record
        # forever and never reach evidence after it.
        next_cursor = (
            _encode_cursor(remaining[examined - 1])
            if examined and examined < len(remaining)
            else None
        )
        diagnostics = dict(outcome.diagnostics.as_mapping())
        diagnostics.update(
            {
                "retrieval_profile": retrieval_profile,
                "tokens_used": used,
                "diversified_results": len(diversified),
                "deferred_same_source_or_domain": deferred,
            }
        )
        return ResearchSearchResult(
            research_project_id=project.research_project_id,
            retrieval_profile=retrieval_profile,
            method=method,
            items=tuple(selected),
            token_budget=budget,
            tokens_used=used,
            next_cursor=next_cursor,
            diagnostics=MappingProxyType(diagnostics),
        )

    async def _search_all(
        self,
        *,
        project: ResearchProjectDocument,
        query: str,
        filters: ResearchSearchFilters,
        effective_classes: tuple[SourceClass, ...] | None,
        profile: ResearchRetrievalProfile,
        methods: tuple[RetrievalMethod, ...],
        phrase: bool,
        fetch_limit: int,
        num_candidates: int,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> tuple[tuple[RetrievalMatch, ...], tuple[RetrievalMatch, ...]]:
        vector: tuple[RetrievalMatch, ...] = ()
        lexical: tuple[RetrievalMatch, ...] = ()
        scope = (
            None
            if effective_classes is None
            else ResearchSearchScope(project.research_project_id, effective_classes)
        )
        include_auxiliary = (
            scope is not None
            and not filters.has_external_source_filter
            and not profile.source_classes
        )
        research_vector: tuple[
            tuple[ResearchChunkSearchMatch, ...],
            tuple[ResearchNoteSearchMatch, ...],
            tuple[ResearchClaimSearchMatch, ...],
        ] = ((), (), ())
        research_lexical: tuple[
            tuple[ResearchChunkSearchMatch, ...],
            tuple[ResearchNoteSearchMatch, ...],
            tuple[ResearchClaimSearchMatch, ...],
        ] = ((), (), ())
        if scope is not None:
            if "vector" in methods:
                embedding = await self._query_embedding(query)
                research_vector = await self._vector_matches(
                    project.research_project_id,
                    embedding,
                    scope=scope,
                    include_auxiliary=include_auxiliary,
                    limit=fetch_limit,
                    num_candidates=num_candidates,
                )
            if "lexical" in methods:
                research_lexical = await self._lexical_matches(
                    project.research_project_id,
                    query,
                    scope=scope,
                    include_auxiliary=include_auxiliary,
                    limit=fetch_limit,
                    phrase=phrase,
                )
        vector_chunks, vector_notes, vector_claims = research_vector
        lexical_chunks, lexical_notes, lexical_claims = research_lexical
        research_vector_candidates, research_lexical_candidates = await self._hydrate_chunks(
            research_project_id=project.research_project_id,
            vector=vector_chunks,
            lexical=lexical_chunks,
            filters=filters,
            allowed_project_ids=allowed_project_ids,
        )
        vector = (
            research_vector_candidates
            + _note_candidates(vector_notes, filters, allowed_project_ids)
            + _claim_candidates(vector_claims, filters, allowed_project_ids)
        )
        lexical = (
            research_lexical_candidates
            + _note_candidates(lexical_notes, filters, allowed_project_ids)
            + _claim_candidates(lexical_claims, filters, allowed_project_ids)
        )
        if profile.include_v1 and not filters.has_external_source_filter:
            v1_vector, v1_lexical = await self._v1_matches(
                project=project,
                query=query,
                filters=filters,
                fetch_limit=fetch_limit,
                methods=methods,
            )
            vector += v1_vector
            lexical += v1_lexical
        return vector, lexical

    async def _vector_matches(
        self,
        research_project_id: str,
        embedding: EmbeddingVector,
        *,
        scope: ResearchSearchScope,
        include_auxiliary: bool,
        limit: int,
        num_candidates: int,
    ) -> tuple[
        tuple[ResearchChunkSearchMatch, ...],
        tuple[ResearchNoteSearchMatch, ...],
        tuple[ResearchClaimSearchMatch, ...],
    ]:
        chunk_repository = self._chunks(research_project_id)
        if not include_auxiliary:
            chunks = await chunk_repository.vector_search(
                embedding.values, limit=limit, num_candidates=num_candidates, scope=scope
            )
            return chunks, (), ()
        chunks, notes, claims = await asyncio.gather(
            chunk_repository.vector_search(
                embedding.values, limit=limit, num_candidates=num_candidates, scope=scope
            ),
            self._notes(research_project_id).vector_search(
                embedding.values, limit=limit, num_candidates=num_candidates, scope=scope
            ),
            self._claims(research_project_id).vector_search(
                embedding.values, limit=limit, num_candidates=num_candidates, scope=scope
            ),
        )
        return chunks, notes, claims

    async def _lexical_matches(
        self,
        research_project_id: str,
        query: str,
        *,
        scope: ResearchSearchScope,
        include_auxiliary: bool,
        limit: int,
        phrase: bool,
    ) -> tuple[
        tuple[ResearchChunkSearchMatch, ...],
        tuple[ResearchNoteSearchMatch, ...],
        tuple[ResearchClaimSearchMatch, ...],
    ]:
        chunk_repository = self._chunks(research_project_id)
        if not include_auxiliary:
            chunks = await chunk_repository.text_search(
                query, limit=limit, phrase=phrase, scope=scope
            )
            return chunks, (), ()
        chunks, notes, claims = await asyncio.gather(
            chunk_repository.text_search(query, limit=limit, phrase=phrase, scope=scope),
            self._notes(research_project_id).text_search(
                query, limit=limit, phrase=phrase, scope=scope
            ),
            self._claims(research_project_id).text_search(
                query, limit=limit, phrase=phrase, scope=scope
            ),
        )
        return chunks, notes, claims

    async def _hydrate_chunks(
        self,
        *,
        research_project_id: str,
        vector: Sequence[ResearchChunkSearchMatch],
        lexical: Sequence[ResearchChunkSearchMatch],
        filters: ResearchSearchFilters,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> tuple[tuple[RetrievalMatch, ...], tuple[RetrievalMatch, ...]]:
        all_matches = tuple(vector) + tuple(lexical)
        source_ids = tuple(match.chunk.source_id for match in all_matches)
        pairs = tuple((match.chunk.source_id, match.chunk.snapshot_id) for match in all_matches)
        source_documents, snapshots = await asyncio.gather(
            self._sources(research_project_id).get_many(source_ids),
            self._snapshots(research_project_id).get_many(pairs),
        )
        sources = {item.source_id: item for item in source_documents}
        snapshot_index = {(item.source_id, item.snapshot_id): item for item in snapshots}

        def convert(match: ResearchChunkSearchMatch) -> RetrievalMatch | None:
            source = sources.get(match.chunk.source_id)
            snapshot = snapshot_index.get((match.chunk.source_id, match.chunk.snapshot_id))
            if source is None or snapshot is None:
                # A retained chunk without its immutable provenance is not safe
                # to present as research evidence, so it is silently excluded.
                return None
            if not _allowed(match.chunk.authorization, allowed_project_ids):
                return None
            if not _allowed(source.authorization, allowed_project_ids):
                return None
            if not _allowed(snapshot.authorization, allowed_project_ids):
                return None
            if not _source_matches_filters(source, snapshot, filters):
                return None
            return _chunk_candidate(match, source, snapshot)

        return (
            tuple(item for match in vector if (item := convert(match)) is not None),
            tuple(item for match in lexical if (item := convert(match)) is not None),
        )

    async def _v1_matches(
        self,
        *,
        project: ResearchProjectDocument,
        query: str,
        filters: ResearchSearchFilters,
        fetch_limit: int,
        methods: tuple[RetrievalMethod, ...],
    ) -> tuple[tuple[RetrievalMatch, ...], tuple[RetrievalMatch, ...]]:
        """Reach V1 only through its retrieval service, never its collections directly."""
        scopes: list[SourceMatches] = []
        if filters.related_project_id is None:
            scopes.append(
                await self._v1_retrieval.gather_sources(
                    query,
                    limit=fetch_limit,
                    sources=("active_skill", "memory"),
                    memory_scopes=("global",),
                    methods=methods,
                )
            )
            related = tuple(project.related_project_ids)
        else:
            related = (filters.related_project_id,)
        if related:
            scoped = await asyncio.gather(
                *(
                    self._v1_retrieval.gather_sources(
                        query,
                        limit=fetch_limit,
                        sources=("project_context", "memory"),
                        project_id=project_id,
                        memory_scopes=("project",),
                        methods=methods,
                    )
                    for project_id in related
                )
            )
            scopes.extend(scoped)
        vector = tuple(match for scope in scopes for match in scope.vector)
        lexical = tuple(match for scope in scopes for match in scope.lexical)
        return (
            _v1_candidates(vector, filters, related_project_ids=tuple(project.related_project_ids)),
            _v1_candidates(
                lexical, filters, related_project_ids=tuple(project.related_project_ids)
            ),
        )

    async def _query_embedding(self, query: str) -> EmbeddingVector:
        if self._embeddings is None:
            raise ResearchSearchUnavailableError("research vector retrieval is unavailable")
        try:
            return await self._embeddings.embed_query(query)
        except EmbeddingUnavailableError as error:
            raise ResearchSearchUnavailableError(
                "research vector retrieval is unavailable"
            ) from error


def _selected_methods(method: ResearchSearchMethod) -> tuple[RetrievalMethod, ...]:
    return ("vector", "lexical") if method == "hybrid" else (method,)


def _effective_source_classes(
    profile: ResearchRetrievalProfile, filters: ResearchSearchFilters
) -> tuple[SourceClass, ...] | None:
    """Intersect profile preference and explicit source filter before querying mongot."""
    requested = tuple(filters.source_classes)
    if not profile.source_classes:
        return requested
    if not requested:
        return profile.source_classes
    profile_set = set(profile.source_classes)
    effective = tuple(item for item in requested if item in profile_set)
    return effective if effective else None


def _allowed(
    authorization: ResearchAuthorization, allowed_project_ids: tuple[str, ...] | None
) -> bool:
    """Apply V1 grants to every historical V3 source record before exposing it."""
    return allowed_project_ids is None or set(authorization.project_ids).issubset(
        allowed_project_ids
    )


def _source_matches_filters(
    source: ResearchSourceDocument,
    snapshot: SourceSnapshotDocument,
    filters: ResearchSearchFilters,
) -> bool:
    if (
        filters.related_project_id is not None
        and filters.related_project_id not in source.authorization.project_ids
    ):
        return False
    if filters.source_classes and source.source_class not in filters.source_classes:
        return False
    if filters.source_types and source.source_type not in filters.source_types:
        return False
    if filters.publisher is not None and _normalized(source.publisher) != _normalized(
        filters.publisher
    ):
        return False
    if filters.domain is not None and _source_domain(source) != _normalized(filters.domain):
        return False
    published = source.published_at
    if filters.published_after is not None and (
        published is None or published < filters.published_after
    ):
        return False
    if filters.published_before is not None and (
        published is None or published > filters.published_before
    ):
        return False
    if filters.retrieved_after is not None and snapshot.retrieved_at < filters.retrieved_after:
        return False
    if filters.as_of is not None:
        if snapshot.retrieved_at > filters.as_of:
            return False
        if published is not None and published > filters.as_of:
            return False
    return True


def _normalized(value: str | None) -> str | None:
    return None if value is None else value.strip().casefold()


def _source_domain(source: ResearchSourceDocument) -> str | None:
    parsed = urlsplit(source.canonical_uri or source.uri)
    return None if parsed.hostname is None else parsed.hostname.casefold()


def _chunk_candidate(
    match: ResearchChunkSearchMatch,
    source: ResearchSourceDocument,
    snapshot: SourceSnapshotDocument,
) -> RetrievalMatch:
    chunk = match.chunk
    domain = _source_domain(source)
    locator = chunk.locator.model_dump(mode="json")
    candidate = RetrievalCandidate(
        key=f"research_chunk:{chunk.document_id}:{chunk.chunk_id}",
        source=_SOURCE_CLASS_TO_SOURCE[source.source_class],
        title=source.title if chunk.section is None else f"{source.title} — {chunk.section}",
        content=chunk.content,
        tokens=estimate_tokens(chunk.content),
        # The snapshot is the evidence version the chunk actually came from;
        # use its retrieval time rather than a mutable source registration time.
        updated_at=snapshot.retrieved_at,
        # Authorization was enforced from immutable V3/V1 provenance before
        # ranking.  Do not pass a research-project id into V1's single-project
        # generic filter, which would discard mixed authorized source results.
        project_id=None,
        tags=tuple(chunk.tags),
        metadata={
            "kind": "chunk",
            "identifier": chunk.chunk_id,
            "status": chunk.status.value,
            "source_id": source.source_id,
            "snapshot_id": snapshot.snapshot_id,
            "document_id": chunk.document_id,
            "source_type": source.source_type.value,
            "source_class": source.source_class.value,
            "publisher": source.publisher,
            "domain": domain,
            "published_at": source.published_at,
            "retrieved_at": snapshot.retrieved_at,
            "locator": locator,
            "related_project_ids": tuple(source.authorization.project_ids),
            "diversity_key": f"domain:{domain}" if domain else f"source:{source.source_id}",
        },
    )
    return RetrievalMatch(candidate=candidate, score=match.score)


def _note_candidates(
    matches: Sequence[ResearchNoteSearchMatch],
    filters: ResearchSearchFilters,
    allowed_project_ids: tuple[str, ...] | None,
) -> tuple[RetrievalMatch, ...]:
    if filters.has_external_source_filter:
        return ()
    results: list[RetrievalMatch] = []
    for match in matches:
        note = match.note
        if not _record_matches_filters(note, filters, allowed_project_ids):
            continue
        results.append(
            RetrievalMatch(
                candidate=RetrievalCandidate(
                    key=f"research_note:{note.research_project_id}:{note.note_id}",
                    source="research_note",
                    title=note.title,
                    content=note.body,
                    tokens=estimate_tokens(note.body),
                    updated_at=note.updated_at,
                    project_id=None,
                    tags=tuple(note.tags),
                    metadata={
                        "kind": "note",
                        "identifier": note.note_id,
                        "status": note.status.value,
                        "related_project_ids": tuple(note.authorization.project_ids),
                        "diversity_key": f"note:{note.note_id}",
                    },
                ),
                score=match.score,
            )
        )
    return tuple(results)


def _claim_candidates(
    matches: Sequence[ResearchClaimSearchMatch],
    filters: ResearchSearchFilters,
    allowed_project_ids: tuple[str, ...] | None,
) -> tuple[RetrievalMatch, ...]:
    if filters.has_external_source_filter:
        return ()
    results: list[RetrievalMatch] = []
    for match in matches:
        claim = match.claim
        if not _record_matches_filters(claim, filters, allowed_project_ids):
            continue
        results.append(
            RetrievalMatch(
                candidate=RetrievalCandidate(
                    key=f"research_claim:{claim.research_project_id}:{claim.claim_id}",
                    source="research_claim",
                    title="Research claim",
                    content=claim.statement,
                    tokens=estimate_tokens(claim.statement),
                    updated_at=claim.updated_at,
                    project_id=None,
                    tags=tuple(claim.tags),
                    metadata={
                        "kind": "claim",
                        "identifier": claim.claim_id,
                        # Claim state is deliberately separate from relevance;
                        # returning it surfaces contested evidence without
                        # making a search score look like factual confidence.
                        "status": claim.status.value,
                        "related_project_ids": tuple(claim.authorization.project_ids),
                        "diversity_key": f"claim:{claim.claim_id}",
                    },
                ),
                score=match.score,
            )
        )
    return tuple(results)


def _record_matches_filters(
    record: ResearchNoteDocument | ResearchClaimDocument,
    filters: ResearchSearchFilters,
    allowed_project_ids: tuple[str, ...] | None,
) -> bool:
    if not _allowed(record.authorization, allowed_project_ids):
        return False
    if (
        filters.related_project_id is not None
        and filters.related_project_id not in record.authorization.project_ids
    ):
        return False
    return filters.as_of is None or record.updated_at <= filters.as_of


def _v1_candidates(
    matches: Sequence[RetrievalMatch],
    filters: ResearchSearchFilters,
    *,
    related_project_ids: tuple[str, ...],
) -> tuple[RetrievalMatch, ...]:
    results: list[RetrievalMatch] = []
    for match in matches:
        candidate = match.candidate
        if candidate.source not in {"active_skill", "project_context", "memory"}:
            continue
        if (
            filters.related_project_id is not None
            and candidate.project_id != filters.related_project_id
        ):
            continue
        if candidate.project_id is not None and candidate.project_id not in related_project_ids:
            continue
        if filters.as_of is not None and candidate.updated_at > filters.as_of:
            continue
        kind = cast(
            ResearchSearchKind, "skill" if candidate.source == "active_skill" else candidate.source
        )
        metadata = dict(candidate.metadata)
        metadata.update(
            {
                "kind": kind,
                "identifier": candidate.key,
                "status": "active" if candidate.is_active else "inactive",
                "related_project_ids": ()
                if candidate.project_id is None
                else (candidate.project_id,),
                "diversity_key": f"{candidate.source}:{candidate.project_id or candidate.key}",
            }
        )
        results.append(
            RetrievalMatch(
                candidate=RetrievalCandidate(
                    key=candidate.key,
                    source=candidate.source,
                    title=candidate.title,
                    content=candidate.content,
                    tokens=candidate.tokens,
                    updated_at=candidate.updated_at,
                    importance=candidate.importance,
                    # The original V1 project is checked above and retained in
                    # related_project_ids.  Ranking this multi-project packet must
                    # not reapply V1's one-project-only generic filter.
                    project_id=None,
                    is_active=candidate.is_active,
                    category=candidate.category,
                    tags=candidate.tags,
                    trust_level=candidate.trust_level,
                    metadata=metadata,
                ),
                score=match.score,
            )
        )
    return tuple(results)


def _diversify(results: Sequence[RankedResult]) -> tuple[tuple[RankedResult, ...], int]:
    """Place one source/domain result first while retaining all evidence for later pages."""
    # ``RankedResult`` is intentionally not re-declared here: this function is
    # pure ordering policy and needs only the generic candidate metadata.
    preferred: list[RankedResult] = []
    deferred: list[RankedResult] = []
    seen: set[str] = set()
    for result in results:
        candidate = result.candidate
        metadata = candidate.metadata
        key = metadata.get("diversity_key") if isinstance(metadata, Mapping) else None
        if isinstance(key, str) and key in seen:
            deferred.append(result)
        else:
            if isinstance(key, str):
                seen.add(key)
            preferred.append(result)
    return tuple(preferred + deferred), len(deferred)


def _encode_cursor(result: RankedResult) -> str:
    candidate = result.candidate
    payload = {"key": candidate.key}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _after_cursor(results: Sequence[RankedResult], cursor: str | None) -> tuple[RankedResult, ...]:
    if cursor is None:
        return tuple(results)
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
        key = value["key"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ResearchSearchError("research search cursor is invalid") from error
    if not isinstance(key, str):
        raise ResearchSearchError("research search cursor is invalid")
    for index, result in enumerate(results):
        if result.candidate.key == key:
            return tuple(results[index + 1 :])
    raise ResearchSearchError("research search cursor is no longer valid")


def _result_item(
    candidate: RetrievalCandidate,
    score: float,
    methods: tuple[RetrievalMethod, ...],
) -> ResearchSearchItem:
    metadata = candidate.metadata
    kind = metadata.get("kind")
    identifier = metadata.get("identifier")
    if kind not in {"chunk", "note", "claim", "skill", "project_context", "memory"}:
        raise ResearchSearchError("research candidate kind is invalid")
    if not isinstance(identifier, str):
        raise ResearchSearchError("research candidate identifier is invalid")
    source_type = metadata.get("source_type")
    source_class = metadata.get("source_class")
    locator = metadata.get("locator")
    related = metadata.get("related_project_ids", ())
    return ResearchSearchItem(
        kind=cast(ResearchSearchKind, kind),
        identifier=identifier,
        source=candidate.source,
        title=candidate.title,
        content=candidate.content,
        status=_optional_string(metadata.get("status")),
        source_id=_optional_string(metadata.get("source_id")),
        snapshot_id=_optional_string(metadata.get("snapshot_id")),
        document_id=_optional_string(metadata.get("document_id")),
        source_type=ResearchSourceType(source_type) if isinstance(source_type, str) else None,
        source_class=SourceClass(source_class) if isinstance(source_class, str) else None,
        publisher=_optional_string(metadata.get("publisher")),
        domain=_optional_string(metadata.get("domain")),
        published_at=_optional_datetime(metadata.get("published_at")),
        retrieved_at=_optional_datetime(metadata.get("retrieved_at")),
        locator=cast(Mapping[str, object] | None, locator)
        if isinstance(locator, Mapping)
        else None,
        related_project_ids=tuple(str(value) for value in related)
        if isinstance(related, tuple)
        else (),
        retrieval_score=score,
        methods=methods,
        tokens=candidate.tokens,
        updated_at=candidate.updated_at,
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


__all__ = [
    "ResearchRetrievalProfile",
    "ResearchRetrievalProfileName",
    "ResearchSearchError",
    "ResearchSearchFilters",
    "ResearchSearchItem",
    "ResearchSearchKind",
    "ResearchSearchMethod",
    "ResearchSearchResult",
    "ResearchSearchService",
    "ResearchSearchUnavailableError",
    "default_research_retrieval_profiles",
]
