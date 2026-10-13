"""V2 content retrieval over current ideas, briefs, and content items.

This service composes the existing embedding provider and pure hybrid ranking
primitives.  It does not generate content, publish content, or reach MongoDB
directly; repositories execute storage queries and the API enforces grants.
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Literal, Protocol, cast

from app.content.errors import ContentRetrievalUnavailableError
from app.db.content_indexes import ContentQueryFilters
from app.db.errors import SearchUnavailableError
from app.embeddings.errors import EmbeddingUnavailableError
from app.embeddings.types import EmbeddingVector
from app.models.common import utc_now
from app.models.content import (
    ContentBriefDocument,
    ContentIdeaDocument,
    ContentStatus,
)
from app.repositories.content_repository import ContentSearchMatch
from app.retrieval.context_builder import (
    DEFAULT_TOKEN_BUDGET,
    MAX_TOKEN_BUDGET,
    resolve_token_budget,
)
from app.retrieval.hybrid import DEFAULT_DUPLICATE_OVERLAP, hybrid_rank
from app.retrieval.query import normalize_query
from app.retrieval.ranking import DEFAULT_RECENCY_HALF_LIFE_DAYS, DEFAULT_WEIGHTS, RankingWeights
from app.retrieval.types import RetrievalCandidate, RetrievalMatch, SourceType
from app.services.content_service import (
    brief_embedding_text,
    idea_embedding_text,
    item_embedding_text,
)
from app.skills.chunker import estimate_tokens
from app.skills.types import RetrievalMethod

ContentRetrievalProfileName = Literal[
    "content_creation",
    "content_history",
    "content_ideation",
    "content_repurposing",
]
ContentSearchMethod = Literal["vector", "lexical", "hybrid"]
ContentSearchKind = Literal["idea", "brief", "content"]


@dataclass(frozen=True, slots=True)
class ContentRetrievalProfile:
    """Retrieval policy for one content use case, independent of V1 search."""

    name: ContentRetrievalProfileName
    source_priorities: Mapping[str, float]
    default_token_budget: int = DEFAULT_TOKEN_BUDGET

    def __post_init__(self) -> None:
        if not self.source_priorities:
            raise ValueError("a content retrieval profile requires source priorities")
        resolve_token_budget(
            self.default_token_budget,
            default=DEFAULT_TOKEN_BUDGET,
            maximum=MAX_TOKEN_BUDGET,
        )
        for value in self.source_priorities.values():
            if not 0.0 <= value <= 1.0:
                raise ValueError("content source priorities must be between 0 and 1")

    def ranking_priorities(self) -> Mapping[SourceType, float]:
        """Return just the content source names accepted by generic ranking."""
        return MappingProxyType(
            {
                "content_idea": self.source_priorities.get("content_idea", 0.5),
                "content_brief": self.source_priorities.get("content_brief", 0.5),
                "content_item": self.source_priorities.get("content_item", 0.5),
                "published_content": self.source_priorities.get(
                    "published_content", self.source_priorities.get("content_item", 0.5)
                ),
            }
        )


def default_content_retrieval_profiles(
    *,
    creation_priorities: Mapping[str, float] | None = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> Mapping[ContentRetrievalProfileName, ContentRetrievalProfile]:
    """Build V2-only retrieval policies; V1 ranking is never replaced.

    The creation profile includes the planned cross-source priorities even
    though this phase only queries content collections.  Later composition can
    use the same named policy for project context, memory, and skills without
    changing the interpretation of the existing values.
    """
    creation = {
        "project_context": 1.00,
        "memory": 0.95,
        "technical_skill": 0.85,
        "content_skill": 0.85,
        "published_content": 0.70,
        "conversation": 0.40,
        "content_item": 0.68,
        "content_brief": 0.62,
        "content_idea": 0.58,
    }
    if creation_priorities is not None:
        creation.update(creation_priorities)
    profiles: dict[ContentRetrievalProfileName, ContentRetrievalProfile] = {
        "content_creation": ContentRetrievalProfile(
            "content_creation", MappingProxyType(creation), token_budget
        ),
        "content_history": ContentRetrievalProfile(
            "content_history",
            MappingProxyType(
                {
                    "published_content": 1.00,
                    "content_item": 0.95,
                    "content_brief": 0.70,
                    "content_idea": 0.55,
                }
            ),
            token_budget,
        ),
        "content_ideation": ContentRetrievalProfile(
            "content_ideation",
            MappingProxyType(
                {
                    "content_idea": 1.00,
                    "content_brief": 0.85,
                    "content_item": 0.70,
                    "published_content": 0.70,
                }
            ),
            token_budget,
        ),
        "content_repurposing": ContentRetrievalProfile(
            "content_repurposing",
            MappingProxyType(
                {
                    "published_content": 1.00,
                    "content_item": 0.90,
                    "content_brief": 0.70,
                    "content_idea": 0.55,
                }
            ),
            token_budget,
        ),
    }
    return MappingProxyType(profiles)


class ContentSearchRepository(Protocol):
    """Profile-bound Mongo search contract for one current content collection."""

    profile_id: str

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = ...,
        num_candidates: int,
        filters: ContentQueryFilters,
    ) -> tuple[ContentSearchMatch, ...]: ...

    async def text_search(
        self,
        query: str,
        *,
        limit: int = ...,
        phrase: bool = False,
        filters: ContentQueryFilters,
    ) -> tuple[ContentSearchMatch, ...]: ...


class QueryEmbedder(Protocol):
    """The existing local query-embedding surface."""

    async def embed_query(self, text: str) -> EmbeddingVector: ...


@dataclass(frozen=True, slots=True)
class ContentSearchItem:
    """A safe, provenance-preserving content retrieval result."""

    kind: Literal["idea", "brief", "content"]
    identifier: str
    title: str
    content: str
    status: str
    platforms: tuple[str, ...]
    tags: tuple[str, ...]
    pillar_id: str | None
    project_ids: tuple[str, ...]
    source_refs: tuple[dict[str, str | None], ...]
    score: float
    methods: tuple[RetrievalMethod, ...]
    tokens: int
    updated_at: datetime

    @property
    def private_project_ids(self) -> tuple[str, ...]:
        """Return every project that must be authorized before returning this item."""
        identifiers = set(self.project_ids)
        identifiers.update(ref["project_id"] for ref in self.source_refs if ref["project_id"])
        return tuple(sorted(identifier for identifier in identifiers if identifier is not None))


@dataclass(frozen=True, slots=True)
class ContentSearchResult:
    """One budgeted answer from the V2 content corpus."""

    profile_id: str
    retrieval_profile: ContentRetrievalProfileName
    method: ContentSearchMethod
    items: tuple[ContentSearchItem, ...]
    token_budget: int
    tokens_used: int
    diagnostics: Mapping[str, object]


ContentRepositoryFactory = Callable[[str], ContentSearchRepository]


class ContentRetrievalService:
    """Rank current content records without changing V1 engineering retrieval."""

    def __init__(
        self,
        ideas: ContentRepositoryFactory,
        briefs: ContentRepositoryFactory,
        items: ContentRepositoryFactory,
        embeddings: QueryEmbedder | None = None,
        *,
        profiles: Mapping[ContentRetrievalProfileName, ContentRetrievalProfile] | None = None,
        candidate_multiplier: int = 10,
        max_candidates: int = 1_000,
        weights: RankingWeights = DEFAULT_WEIGHTS,
        recency_half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS,
        duplicate_overlap: float = DEFAULT_DUPLICATE_OVERLAP,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        max_token_budget: int = MAX_TOKEN_BUDGET,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if candidate_multiplier < 1 or max_candidates < 1:
            raise ValueError("content retrieval candidate limits must be positive")
        resolve_token_budget(None, default=token_budget, maximum=max_token_budget)
        self._ideas = ideas
        self._briefs = briefs
        self._items = items
        self._embeddings = embeddings
        self._profiles = profiles or default_content_retrieval_profiles(token_budget=token_budget)
        self._candidate_multiplier = candidate_multiplier
        self._max_candidates = max_candidates
        self._weights = weights
        self._recency_half_life_days = recency_half_life_days
        self._duplicate_overlap = duplicate_overlap
        self._token_budget = token_budget
        self._max_token_budget = max_token_budget
        self._clock = clock

    async def search(
        self,
        *,
        profile_id: str,
        query: str,
        filters: ContentQueryFilters,
        retrieval_profile: ContentRetrievalProfileName = "content_history",
        method: ContentSearchMethod = "hybrid",
        kinds: tuple[ContentSearchKind, ...] = (),
        limit: int = 10,
        token_budget: int | None = None,
    ) -> ContentSearchResult:
        """Search only current V2 content in one explicitly named profile."""
        if filters.profile_id != profile_id:
            raise ValueError("content search filters must use the requested profile")
        if retrieval_profile not in self._profiles:
            raise ValueError("unknown content retrieval profile")
        if not 1 <= limit <= 50:
            raise ValueError("content search limit must be between 1 and 50")
        if len(kinds) != len(set(kinds)):
            raise ValueError("content search kinds must be unique")
        normalized = normalize_query(query)
        profile = self._profiles[retrieval_profile]
        budget = resolve_token_budget(
            token_budget,
            default=profile.default_token_budget,
            maximum=self._max_token_budget,
        )
        requested = min(self._max_candidates, max(limit, limit * self._candidate_multiplier))
        try:
            vector, lexical, methods = await self._search_sources(
                profile_id=profile_id,
                query=normalized,
                filters=filters,
                method=method,
                kinds=kinds,
                limit=limit,
                num_candidates=requested,
            )
        except SearchUnavailableError as error:
            # Repositories deliberately hide MongoDB Search internals. Keep
            # that boundary at the content-service layer too, so every V2
            # caller can turn an index warm-up into a safe 503.
            raise ContentRetrievalUnavailableError(
                "content retrieval is temporarily unavailable"
            ) from error
        outcome = hybrid_rank(
            vector,
            lexical,
            methods=methods,
            now=self._clock(),
            # Content status is its own lifecycle.  Archived content must stay
            # searchable, so V1's active-only filtering is not applicable.
            filters=None,
            weights=self._weights,
            half_life_days=self._recency_half_life_days,
            duplicate_overlap=self._duplicate_overlap,
            limit=limit,
            source_priorities=profile.ranking_priorities(),
        )
        selected: list[ContentSearchItem] = []
        used = 0
        for ranked in outcome.results:
            item = _result_item(ranked.candidate, ranked.score, ranked.breakdown.methods)
            if item.tokens > budget - used:
                continue
            selected.append(item)
            used += item.tokens
        diagnostics = dict(outcome.diagnostics.as_mapping())
        diagnostics.update({"retrieval_profile": retrieval_profile, "tokens_used": used})
        return ContentSearchResult(
            profile_id=profile_id,
            retrieval_profile=retrieval_profile,
            method=method,
            items=tuple(selected),
            token_budget=budget,
            tokens_used=used,
            diagnostics=MappingProxyType(diagnostics),
        )

    async def _search_sources(
        self,
        *,
        profile_id: str,
        query: str,
        filters: ContentQueryFilters,
        method: ContentSearchMethod,
        kinds: tuple[ContentSearchKind, ...],
        limit: int,
        num_candidates: int,
    ) -> tuple[tuple[RetrievalMatch, ...], tuple[RetrievalMatch, ...], tuple[RetrievalMethod, ...]]:
        repositories_by_kind = (
            ("idea", self._ideas(profile_id)),
            ("brief", self._briefs(profile_id)),
            ("content", self._items(profile_id)),
        )
        repositories = tuple(
            repository for kind, repository in repositories_by_kind if not kinds or kind in kinds
        )
        vector: tuple[RetrievalMatch, ...] = ()
        lexical: tuple[RetrievalMatch, ...] = ()
        methods: tuple[RetrievalMethod, ...]
        if method in {"vector", "hybrid"}:
            embedding = await self._query_embedding(query)
            matches = await asyncio.gather(
                *(
                    repository.vector_search(
                        embedding.values,
                        limit=limit,
                        num_candidates=num_candidates,
                        filters=filters,
                    )
                    for repository in repositories
                )
            )
            vector = tuple(_retrieval_match(match) for group in matches for match in group)
        if method in {"lexical", "hybrid"}:
            matches = await asyncio.gather(
                *(
                    repository.text_search(query, limit=limit, filters=filters)
                    for repository in repositories
                )
            )
            lexical = tuple(_retrieval_match(match) for group in matches for match in group)
        methods = ("vector", "lexical") if method == "hybrid" else (method,)
        return vector, lexical, methods

    async def _query_embedding(self, query: str) -> EmbeddingVector:
        if self._embeddings is None:
            raise ContentRetrievalUnavailableError("content vector retrieval is unavailable")
        try:
            return await self._embeddings.embed_query(query)
        except EmbeddingUnavailableError as error:
            raise ContentRetrievalUnavailableError(
                "content vector retrieval is unavailable"
            ) from error


def _retrieval_match(match: ContentSearchMatch) -> RetrievalMatch:
    document = match.document
    project_ids: tuple[str, ...]
    if isinstance(document, ContentIdeaDocument):
        source: SourceType = "content_idea"
        identifier = document.idea_id
        content = idea_embedding_text(document)
        project_ids = () if document.origin.project_id is None else (document.origin.project_id,)
        source_refs: tuple[dict[str, str | None], ...] = ()
        platforms = tuple(platform.value for platform in document.platforms)
        title = document.title
        pillar_id = document.pillar_id
    elif isinstance(document, ContentBriefDocument):
        source = "content_brief"
        identifier = document.brief_id
        content = brief_embedding_text(document)
        project_ids = ()
        source_refs = _source_refs(document.source_refs)
        platforms = (document.platform.value,)
        title = document.objective
        pillar_id = None
    else:
        source = (
            "published_content" if document.status is ContentStatus.PUBLISHED else "content_item"
        )
        identifier = document.content_id
        content = item_embedding_text(document)
        project_ids = tuple(document.project_ids)
        source_refs = _source_refs(document.source_refs)
        platforms = (document.platform.value,)
        title = document.title
        pillar_id = document.pillar_id
    return RetrievalMatch(
        candidate=RetrievalCandidate(
            key=f"{source}:{document.profile_id}:{identifier}",
            source=source,
            title=title,
            content=content,
            tokens=estimate_tokens(content),
            updated_at=document.updated_at,
            # Content priority is meaningful for ideas; neutral is appropriate
            # for briefs/items whose lifecycle has no curator importance field.
            importance=document.priority if isinstance(document, ContentIdeaDocument) else 0.5,
            tags=tuple(document.tags),
            is_active=True,
            metadata={
                "kind": "idea"
                if isinstance(document, ContentIdeaDocument)
                else "brief"
                if isinstance(document, ContentBriefDocument)
                else "content",
                "identifier": identifier,
                "status": document.status.value,
                "platforms": platforms,
                "pillar_id": pillar_id,
                "project_ids": project_ids,
                "source_refs": source_refs,
            },
        ),
        score=match.score,
    )


def _result_item(
    candidate: RetrievalCandidate,
    score: float,
    methods: tuple[RetrievalMethod, ...],
) -> ContentSearchItem:
    metadata = candidate.metadata
    kind = metadata["kind"]
    identifier = metadata["identifier"]
    status = metadata["status"]
    platforms = metadata["platforms"]
    project_ids = metadata["project_ids"]
    source_refs = metadata["source_refs"]
    if not isinstance(kind, str) or not isinstance(identifier, str) or not isinstance(status, str):
        raise ValueError("content retrieval candidate metadata is invalid")
    if kind not in {"idea", "brief", "content"}:
        raise ValueError("content retrieval candidate kind is invalid")
    result_kind = cast(Literal["idea", "brief", "content"], kind)
    pillar = metadata.get("pillar_id")
    pillar_id = pillar if isinstance(pillar, str) else None
    return ContentSearchItem(
        kind=result_kind,
        identifier=identifier,
        title=candidate.title,
        content=candidate.content,
        status=status,
        platforms=tuple(str(value) for value in platforms) if isinstance(platforms, tuple) else (),
        tags=candidate.tags,
        pillar_id=pillar_id,
        project_ids=tuple(str(value) for value in project_ids)
        if isinstance(project_ids, tuple)
        else (),
        source_refs=source_refs if isinstance(source_refs, tuple) else (),
        score=score,
        methods=methods,
        tokens=candidate.tokens,
        updated_at=candidate.updated_at,
    )


def _source_refs(refs: Sequence[object]) -> tuple[dict[str, str | None], ...]:
    return tuple(
        {
            "source_type": str(ref.source_type),
            "source_id": str(ref.source_id),
            "project_id": None if ref.project_id is None else str(ref.project_id),
        }
        for ref in refs
        if hasattr(ref, "source_type") and hasattr(ref, "source_id") and hasattr(ref, "project_id")
    )
