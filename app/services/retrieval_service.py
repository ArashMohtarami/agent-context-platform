"""Vector, lexical, and hybrid retrieval over indexed skill chunks.

Every mode resolves the same metadata filters. Vector and lexical retrieval
return scored chunks; hybrid retrieval combines them through the ranking,
deduplication, and budgeting rules in `app.retrieval`, which know nothing about
MongoDB.
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Final, Protocol

from app.db.context_indexes import ContextQueryFilters
from app.db.memory_indexes import MemoryQueryFilters
from app.embeddings.errors import EmbeddingUnavailableError
from app.embeddings.types import EmbeddingVector
from app.memory.errors import MemoryRetrievalUnavailableError
from app.memory.types import MemoryMatch, MemorySearchResult, memory_key
from app.models.common import utc_now
from app.models.memory import MemoryDocument, MemoryScope
from app.models.project_context import ContextType, ProjectContextDocument
from app.models.skill import LifecycleStatus, SkillDocument, SkillTrustLevel
from app.models.skill_chunk import SkillChunkDocument
from app.projects.errors import ContextRetrievalUnavailableError
from app.projects.types import ContextSearchResult, ProjectContextMatch, context_key
from app.repositories.skill_chunk_repository import (
    DEFAULT_SEARCH_LIMIT,
    MAX_NUM_CANDIDATES,
    MAX_SEARCH_LIMIT,
)
from app.retrieval.context_builder import (
    DEFAULT_TOKEN_BUDGET,
    MAX_TOKEN_BUDGET,
    RetrievalContext,
    build_context,
    resolve_token_budget,
)
from app.retrieval.filters import RetrievalFilters
from app.retrieval.hybrid import (
    DEFAULT_DUPLICATE_OVERLAP,
    hybrid_rank,
)
from app.retrieval.query import normalize_query
from app.retrieval.ranking import (
    DEFAULT_RECENCY_HALF_LIFE_DAYS,
    DEFAULT_WEIGHTS,
    RankingWeights,
)
from app.retrieval.types import (
    ALL_SOURCES,
    NEUTRAL_IMPORTANCE,
    RETRIEVABLE_SOURCES,
    RetrievalCandidate,
    RetrievalMatch,
    SourceMatches,
    SourceType,
)
from app.security.tenant_service import tenant_service
from app.skills.chunker import estimate_tokens
from app.skills.types import (
    RetrievalMethod,
    SkillChunkMatch,
    SkillChunkSearchResult,
    SkillVersionKey,
)

DEFAULT_CANDIDATE_MULTIPLIER: Final = 10
DEFAULT_MAX_CANDIDATES: Final = 1_000
# Past this many matching versions the pre-filter stops being selective, so the
# query runs unfiltered and every result is still verified afterwards.
MAX_PREFILTER_VERSIONS: Final = 200
_FALLBACK_OVERFETCH: Final = 4
# Skills are the curated knowledge base, which the ranking model calls an
# active skill. They carry no project owner and no curator-assigned importance.
SKILL_SOURCE: Final[SourceType] = "active_skill"
# Project context outranks every other source, which is what makes
# project-specific knowledge beat generic knowledge at equal relevance.
CONTEXT_SOURCE: Final[SourceType] = "project_context"
# Memory outranks a conversation but not a project's own recorded context.
MEMORY_SOURCE: Final[SourceType] = "memory"
_CHUNK_KEY_PREFIX: Final = "skill_chunk"


class SkillMetadataRepository(Protocol):
    """Skill metadata reads required to scope and describe retrieval results."""

    async def list_version_keys(
        self,
        *,
        status: LifecycleStatus | None = "active",
        skill_id: str | None = None,
        version: str | None = None,
        category: str | None = None,
        tags: Sequence[str] = (),
        trust_level: SkillTrustLevel | None = None,
        limit: int = ...,
    ) -> tuple[SkillVersionKey, ...]: ...

    async def get_many(self, keys: Sequence[SkillVersionKey]) -> tuple[SkillDocument, ...]: ...


class ChunkSearchRepository(Protocol):
    """The chunk queries retrieval depends on."""

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = ...,
        num_candidates: int,
        skill_versions: Sequence[SkillVersionKey] = (),
    ) -> tuple[SkillChunkMatch, ...]: ...

    async def text_search(
        self,
        query: str,
        *,
        limit: int = ...,
        phrase: bool = False,
        skill_versions: Sequence[SkillVersionKey] = (),
    ) -> tuple[SkillChunkMatch, ...]: ...


class ContextSearchRepository(Protocol):
    """The project-scoped context queries retrieval depends on."""

    project_id: str

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = ...,
        num_candidates: int,
        filters: ContextQueryFilters,
    ) -> tuple[ProjectContextMatch, ...]: ...

    async def text_search(
        self,
        query: str,
        *,
        limit: int = ...,
        phrase: bool = False,
        filters: ContextQueryFilters,
    ) -> tuple[ProjectContextMatch, ...]: ...


# A context repository is bound to one project when it is built, so retrieval
# receives a factory rather than a repository: the project comes from the
# request, and no shared handle can be pointed at another project by mistake.
ContextSearchFactory = Callable[[str], ContextSearchRepository]


class MemorySearchRepository(Protocol):
    """The scope-bound memory queries retrieval depends on."""

    project_id: str | None
    client_id: str | None

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = ...,
        num_candidates: int,
        filters: MemoryQueryFilters,
    ) -> tuple[MemoryMatch, ...]: ...

    async def text_search(
        self,
        query: str,
        *,
        limit: int = ...,
        phrase: bool = False,
        filters: MemoryQueryFilters,
    ) -> tuple[MemoryMatch, ...]: ...


# A memory repository is bound to a scope the same way, so retrieval receives a
# factory taking the project a request named, or None for global memory.
MemorySearchFactory = Callable[[str | None, str | None], MemorySearchRepository]


class QueryEmbedder(Protocol):
    """Query embedding bound to the one active model."""

    async def embed_query(self, text: str) -> EmbeddingVector: ...


@tenant_service
class RetrievalService:
    """Turn a query into scored, filtered skill chunks, or budgeted context.

    Lexical retrieval needs no embedding model, so the embedder is optional;
    vector retrieval requires one and hybrid retrieval uses it when it exists.

    Metadata filters are resolved to skill versions first and applied as a
    pre-filter, so filtering never silently costs results. Requesting an
    explicit `skill_version` targets that exact version, including a superseded
    one.
    """

    def __init__(
        self,
        skills: SkillMetadataRepository,
        chunks: ChunkSearchRepository,
        embeddings: QueryEmbedder | None = None,
        *,
        contexts: ContextSearchFactory | None = None,
        memories: MemorySearchFactory | None = None,
        candidate_multiplier: int = DEFAULT_CANDIDATE_MULTIPLIER,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        weights: RankingWeights = DEFAULT_WEIGHTS,
        recency_half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS,
        duplicate_overlap: float = DEFAULT_DUPLICATE_OVERLAP,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        max_token_budget: int = MAX_TOKEN_BUDGET,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if candidate_multiplier < 1:
            raise ValueError("candidate_multiplier must be at least 1")
        if max_candidates < 1:
            raise ValueError("max_candidates must be at least 1")
        # Rejected here rather than on first use, so a misconfigured deployment
        # fails while it is being built instead of while answering a query.
        resolve_token_budget(None, default=token_budget, maximum=max_token_budget)
        self._skills = skills
        self._chunks = chunks
        self._embeddings = embeddings
        self._contexts = contexts
        self._memories = memories
        self._candidate_multiplier = candidate_multiplier
        self._max_candidates = max_candidates
        self._weights = weights
        self._recency_half_life_days = recency_half_life_days
        self._duplicate_overlap = duplicate_overlap
        self._token_budget = token_budget
        self._max_token_budget = max_token_budget
        self._clock = clock

    async def vector_search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        skill_id: str | None = None,
        skill_version: str | None = None,
        category: str | None = None,
        tags: Sequence[str] = (),
        trust_level: SkillTrustLevel | None = None,
        active_only: bool = True,
    ) -> tuple[SkillChunkSearchResult, ...]:
        """Rank chunks by embedding similarity to the query."""
        embedder = self._embeddings
        if embedder is None:
            raise EmbeddingUnavailableError("vector search requires an embedding model")
        normalized = normalize_query(query)
        self._validate_scope(limit, skill_id, skill_version)
        keys = await self._resolve_versions(
            skill_id=skill_id,
            skill_version=skill_version,
            category=category,
            tags=tags,
            trust_level=trust_level,
            active_only=active_only,
        )
        if not keys:
            return ()
        prefilter, fetch_limit = self._scope(keys, limit)
        vector = await embedder.embed_query(normalized)
        matches = await self._chunks.vector_search(
            vector.values,
            limit=fetch_limit,
            num_candidates=self._num_candidates(fetch_limit),
            skill_versions=prefilter,
        )
        return await self._results(matches, keys, limit, "vector")

    async def lexical_search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        phrase: bool = False,
        skill_id: str | None = None,
        skill_version: str | None = None,
        category: str | None = None,
        tags: Sequence[str] = (),
        trust_level: SkillTrustLevel | None = None,
        active_only: bool = True,
    ) -> tuple[SkillChunkSearchResult, ...]:
        """Rank chunks by lexical relevance, keeping identifiers exact.

        The query reaches the index as written apart from whitespace
        normalization, so `wallet:read`, `OAuth2`, and `/api/v1/payments` stay
        matchable. `phrase` requires the words to appear together in order.
        """
        normalized = normalize_query(query)
        self._validate_scope(limit, skill_id, skill_version)
        keys = await self._resolve_versions(
            skill_id=skill_id,
            skill_version=skill_version,
            category=category,
            tags=tags,
            trust_level=trust_level,
            active_only=active_only,
        )
        if not keys:
            return ()
        prefilter, fetch_limit = self._scope(keys, limit)
        matches = await self._chunks.text_search(
            normalized,
            limit=fetch_limit,
            phrase=phrase,
            skill_versions=prefilter,
        )
        return await self._results(matches, keys, limit, "lexical")

    async def hybrid_search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        token_budget: int | None = None,
        project_id: str | None = None,
        skill_id: str | None = None,
        skill_version: str | None = None,
        category: str | None = None,
        memory_client_id: str | None = None,
        tags: Sequence[str] = (),
        trust_level: SkillTrustLevel | None = None,
        active_only: bool = True,
        minimum_importance: float = 0.0,
    ) -> RetrievalContext:
        """Combine vector and lexical retrieval into one budgeted context.

        Both searches run concurrently over the same metadata scope, and their
        results are merged, ranked, deduplicated, and cut to the token budget.
        Without an embedding model the lexical half still answers, with the
        vector weight redistributed rather than silently scoring every result
        against a signal that cannot exist.
        """
        normalized = normalize_query(query)
        # Resolved before any search runs, so an impossible budget is refused
        # rather than discovered after the retrieval has been paid for.
        budget = self.resolve_budget(token_budget)
        matches = await self.gather_sources(
            normalized,
            limit=limit,
            project_id=project_id,
            skill_id=skill_id,
            skill_version=skill_version,
            category=category,
            memory_client_id=memory_client_id,
            tags=tags,
            trust_level=trust_level,
            active_only=active_only,
            minimum_importance=minimum_importance,
        )
        return self.rank_context(
            normalized,
            matches,
            filters=RetrievalFilters(
                project_id=project_id,
                memory_client_id=memory_client_id,
                category=category,
                tags=tuple(tags),
                trust_level=trust_level,
                minimum_importance=minimum_importance,
                # An explicitly requested version is returned even when
                # superseded, the rule every other skill lookup follows.
                require_active=active_only and skill_version is None,
            ),
            limit=limit,
            token_budget=budget,
            project_id=project_id,
        )

    async def gather_sources(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        sources: Sequence[SourceType] = RETRIEVABLE_SOURCES,
        project_id: str | None = None,
        skill_id: str | None = None,
        skill_version: str | None = None,
        category: str | None = None,
        context_types: Sequence[ContextType] = (),
        memory_types: Sequence[str] = (),
        memory_scopes: Sequence[MemoryScope] | None = None,
        memory_client_id: str | None = None,
        tags: Sequence[str] = (),
        trust_level: SkillTrustLevel | None = None,
        active_only: bool = True,
        minimum_importance: float = 0.0,
        phrase: bool = False,
        methods: Sequence[RetrievalMethod] | None = None,
    ) -> SourceMatches:
        """Search every requested source at once and return raw candidates.

        This is the retrieval half of the pipeline: it resolves each source's
        scope, runs both searches against each one concurrently, and describes
        what came back as source-agnostic candidates. Nothing here ranks,
        filters, or budgets, so one caller can combine several sources and
        another can rank them differently without either reaching into storage.

        A requested source is absent from `searched` when no query ran against
        it: it was not configured, it cannot be retrieved at all, or this
        request put nothing of it in reach — project context without a project
        being the case that matters most.
        """
        normalized = normalize_query(query)
        self._validate_scope(limit, skill_id, skill_version)
        wanted = frozenset(sources)
        selected_methods = self._requested_methods(methods)
        keys: tuple[SkillVersionKey, ...] = ()
        if SKILL_SOURCE in wanted:
            keys = await self._resolve_versions(
                skill_id=skill_id,
                skill_version=skill_version,
                category=category,
                tags=tags,
                trust_level=trust_level,
                active_only=active_only,
            )
        context_scope = (
            self._context_scope(
                project_id,
                tags=tags,
                active_only=active_only,
                minimum_importance=minimum_importance,
                context_types=context_types,
            )
            if CONTEXT_SOURCE in wanted
            else None
        )
        memory_scope = (
            self._memory_scope(
                project_id,
                tags=tags,
                active_only=active_only,
                minimum_importance=minimum_importance,
                memory_types=memory_types,
                scopes=memory_scopes,
                client_id=memory_client_id,
            )
            if MEMORY_SOURCE in wanted
            else None
        )
        prefilter, fetch_limit = self._scope(keys, limit)
        # Every source about to run a vector search wants the same vector for
        # the same query, and producing it costs more than all of the searches
        # together. One embedding, handed to each of them.
        searching = bool(keys) or context_scope is not None or memory_scope is not None
        embedded = (
            await self._embedded(normalized) if searching and "vector" in selected_methods else None
        )
        skill_matches, context_matches, memory_matches = await asyncio.gather(
            self._gather_skill_matches(
                normalized,
                embedded=embedded,
                prefilter=prefilter,
                fetch_limit=fetch_limit,
                enabled=bool(keys),
                methods=selected_methods,
            ),
            self._gather_context_matches(
                normalized,
                context_scope,
                embedded=embedded,
                fetch_limit=fetch_limit,
                phrase=phrase,
                methods=selected_methods,
            ),
            self._gather_memory_matches(
                normalized,
                memory_scope,
                embedded=embedded,
                fetch_limit=fetch_limit,
                phrase=phrase,
                methods=selected_methods,
            ),
        )
        chunk_vector, chunk_lexical = skill_matches
        context_vector, context_lexical = context_matches
        memory_vector, memory_lexical = memory_matches
        skills = await self._skill_index(chunk_vector + chunk_lexical, keys)
        return SourceMatches(
            vector=self._candidates(chunk_vector, skills)
            + _context_candidates(context_vector)
            + _memory_candidates(memory_vector),
            lexical=self._candidates(chunk_lexical, skills)
            + _context_candidates(context_lexical)
            + _memory_candidates(memory_lexical),
            searched=self._searched(
                wanted,
                skills=bool(keys),
                contexts=context_scope is not None,
                memories=memory_scope is not None,
            ),
        )

    def rank_context(
        self,
        query: str,
        matches: SourceMatches,
        *,
        filters: RetrievalFilters,
        limit: int = DEFAULT_SEARCH_LIMIT,
        token_budget: int | None = None,
        project_id: str | None = None,
    ) -> RetrievalContext:
        """Rank, deduplicate, and budget candidates the searches already found.

        This is the ranking half of the pipeline, and it performs no I/O: given
        the same candidates, filters, and reference time it always produces the
        same context, whichever sources were combined to reach it.
        """
        outcome = hybrid_rank(
            matches.vector,
            matches.lexical,
            methods=self.methods,
            now=self._clock(),
            filters=filters,
            weights=self._weights,
            half_life_days=self._recency_half_life_days,
            duplicate_overlap=self._duplicate_overlap,
            limit=limit,
        )
        return build_context(
            query,
            outcome.results,
            diagnostics=outcome.diagnostics,
            project_id=project_id,
            token_budget=self.resolve_budget(token_budget),
        )

    def resolve_budget(self, requested: int | None) -> int:
        """Clamp a requested token budget into this deployment's range."""
        return resolve_token_budget(
            requested,
            default=self._token_budget,
            maximum=self._max_token_budget,
        )

    @staticmethod
    def _searched(
        wanted: frozenset[SourceType],
        *,
        skills: bool,
        contexts: bool,
        memories: bool,
    ) -> tuple[SourceType, ...]:
        """Name the requested sources a query actually ran against."""
        ran = {
            CONTEXT_SOURCE: contexts,
            SKILL_SOURCE: skills,
            MEMORY_SOURCE: memories,
        }
        return tuple(
            source for source in ALL_SOURCES if source in wanted and ran.get(source, False)
        )

    async def memory_search(
        self,
        query: str,
        *,
        project_id: str | None = None,
        scopes: Sequence[MemoryScope] | None = None,
        client_id: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        phrase: bool = False,
        memory_types: Sequence[str] = (),
        tags: Sequence[str] = (),
        status: LifecycleStatus | None = "active",
        minimum_importance: float = 0.0,
    ) -> tuple[MemorySearchResult, ...]:
        """Search the memory a request is allowed to see.

        Naming no project searches global memory. Naming one searches that
        project's memory and global memory together, never another project's.
        """
        normalized = normalize_query(query)
        self._validate_scope(limit, None, None)
        factory = self._memories
        if factory is None:
            raise MemoryRetrievalUnavailableError("memory retrieval is not configured")
        resolved = _resolved_memory_scopes(project_id, client_id, scopes)
        scope = MemoryQueryFilters(
            scopes=resolved,
            project_id=project_id
            if any(value in {"project", "project_agent"} for value in resolved)
            else None,
            client_id=client_id
            if any(value in {"agent", "project_agent"} for value in resolved)
            else None,
            types=tuple(memory_types),
            tags=tuple(tags),
            status=status,
            minimum_importance=minimum_importance,
        )
        vector_matches, lexical_matches = await self._gather_memory_matches(
            normalized,
            scope,
            embedded=await self._embedded(normalized),
            fetch_limit=limit,
            phrase=phrase,
        )
        documents = {match.key: match.memory for match in (*vector_matches, *lexical_matches)}
        outcome = hybrid_rank(
            _memory_candidates(vector_matches),
            _memory_candidates(lexical_matches),
            methods=self.methods,
            now=self._clock(),
            filters=RetrievalFilters(
                project_id=project_id,
                memory_client_id=client_id,
                minimum_importance=minimum_importance,
                require_active=status == "active",
            ),
            weights=self._weights,
            half_life_days=self._recency_half_life_days,
            duplicate_overlap=self._duplicate_overlap,
            limit=limit,
        )
        return tuple(
            MemorySearchResult(
                memory=document,
                score=result.score,
                methods=result.breakdown.methods,
            )
            for result in outcome.results
            if (document := documents.get(result.candidate.key)) is not None
        )

    async def context_search(
        self,
        query: str,
        *,
        project_id: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        phrase: bool = False,
        context_types: Sequence[ContextType] = (),
        tags: Sequence[str] = (),
        status: LifecycleStatus | None = "active",
        minimum_importance: float = 0.0,
    ) -> tuple[ContextSearchResult, ...]:
        """Search one project's context with both retrieval methods.

        The project is required, not defaulted: there is no such thing as a
        context search across projects.
        """
        normalized = normalize_query(query)
        self._validate_scope(limit, None, None)
        factory = self._contexts
        if factory is None:
            raise ContextRetrievalUnavailableError("context retrieval is not configured")
        scope = ContextQueryFilters(
            project_id=project_id,
            types=tuple(context_types),
            tags=tuple(tags),
            status=status,
            minimum_importance=minimum_importance,
        )
        vector_matches, lexical_matches = await self._gather_context_matches(
            normalized,
            scope,
            embedded=await self._embedded(normalized),
            fetch_limit=limit,
            phrase=phrase,
        )
        documents = {match.key: match.context for match in (*vector_matches, *lexical_matches)}
        outcome = hybrid_rank(
            _context_candidates(vector_matches),
            _context_candidates(lexical_matches),
            methods=self.methods,
            now=self._clock(),
            filters=RetrievalFilters(
                project_id=project_id,
                minimum_importance=minimum_importance,
                require_active=status == "active",
            ),
            weights=self._weights,
            half_life_days=self._recency_half_life_days,
            duplicate_overlap=self._duplicate_overlap,
            limit=limit,
        )
        return tuple(
            ContextSearchResult(
                context=document,
                score=result.score,
                methods=result.breakdown.methods,
            )
            for result in outcome.results
            if (document := documents.get(result.candidate.key)) is not None
        )

    async def _embedded(self, query: str) -> EmbeddingVector | None:
        """Embed the query once, or report that this deployment cannot.

        Every source that can search vectors needs the same vector for the same
        query, and generating it is by a wide margin the most expensive thing a
        retrieval does — measured at hundreds of milliseconds under load against
        about four for an index query. Embedding once and handing the result to
        each source is therefore worth the parameter it costs.
        """
        embedder = self._embeddings
        return None if embedder is None else await embedder.embed_query(query)

    @property
    def methods(self) -> tuple[RetrievalMethod, ...]:
        """Report which searches this deployment can actually run."""
        if self._embeddings is None:
            return ("lexical",)
        return ("vector", "lexical")

    def _requested_methods(
        self, methods: Sequence[RetrievalMethod] | None
    ) -> tuple[RetrievalMethod, ...]:
        """Validate a caller's narrower method choice without changing defaults."""
        if methods is None:
            return self.methods
        selected = tuple(methods)
        if not selected or len(selected) != len(set(selected)):
            raise ValueError("retrieval methods must be a non-empty unique sequence")
        if any(method not in {"vector", "lexical"} for method in selected):
            raise ValueError("retrieval methods must be vector and/or lexical")
        if "vector" in selected and self._embeddings is None:
            raise EmbeddingUnavailableError("vector search requires an embedding model")
        return selected

    async def _gather_skill_matches(
        self,
        query: str,
        *,
        embedded: EmbeddingVector | None,
        prefilter: Sequence[SkillVersionKey],
        fetch_limit: int,
        enabled: bool = True,
        methods: Sequence[RetrievalMethod] | None = None,
    ) -> tuple[tuple[SkillChunkMatch, ...], tuple[SkillChunkMatch, ...]]:
        """Run both skill searches at once, since neither depends on the other."""
        if not enabled:
            return (), ()
        selected = self._requested_methods(methods)
        lexical = (
            self._chunks.text_search(
                query,
                limit=fetch_limit,
                skill_versions=prefilter,
            )
            if "lexical" in selected
            else None
        )
        vector = (
            self._vector_matches(embedded, prefilter=prefilter, fetch_limit=fetch_limit)
            if "vector" in selected and embedded is not None
            else None
        )
        if vector is None:
            return (), () if lexical is None else await lexical
        if lexical is None:
            return await vector, ()
        return await asyncio.gather(vector, lexical)

    def _context_scope(
        self,
        project_id: str | None,
        *,
        tags: Sequence[str] = (),
        active_only: bool = True,
        minimum_importance: float = 0.0,
        context_types: Sequence[ContextType] = (),
    ) -> ContextQueryFilters | None:
        """Build the context scope, or refuse to search context at all.

        Context belongs to exactly one project, so a request naming no project
        has no context to search: returning none is the isolation rule, not a
        missing feature.
        """
        if project_id is None or self._contexts is None:
            return None
        return ContextQueryFilters(
            project_id=project_id,
            types=tuple(context_types),
            tags=tuple(tags),
            status="active" if active_only else None,
            minimum_importance=minimum_importance,
        )

    async def _gather_context_matches(
        self,
        query: str,
        scope: ContextQueryFilters | None,
        *,
        embedded: EmbeddingVector | None,
        fetch_limit: int,
        phrase: bool = False,
        methods: Sequence[RetrievalMethod] | None = None,
    ) -> tuple[tuple[ProjectContextMatch, ...], tuple[ProjectContextMatch, ...]]:
        """Run both context searches at once inside one project."""
        factory = self._contexts
        if scope is None or factory is None:
            return (), ()
        repository = factory(scope.project_id)
        selected = self._requested_methods(methods)
        lexical = (
            repository.text_search(
                query,
                limit=fetch_limit,
                phrase=phrase,
                filters=scope,
            )
            if "lexical" in selected
            else None
        )
        vector = (
            self._context_vector_matches(
                embedded,
                repository,
                scope=scope,
                fetch_limit=fetch_limit,
            )
            if "vector" in selected and embedded is not None
            else None
        )
        if vector is None:
            return (), () if lexical is None else await lexical
        if lexical is None:
            return await vector, ()
        return await asyncio.gather(vector, lexical)

    def _memory_scope(
        self,
        project_id: str | None,
        *,
        tags: Sequence[str] = (),
        active_only: bool = True,
        minimum_importance: float = 0.0,
        memory_types: Sequence[str] = (),
        scopes: Sequence[MemoryScope] | None = None,
        client_id: str | None = None,
    ) -> MemoryQueryFilters | None:
        """Build the memory scope this request may see.

        Global memory belongs to every request. Project memory belongs only to
        the project that owns it, so a request naming a project sees both and a
        request naming none sees global memory alone.

        Naming the scopes explicitly narrows that default; it can never widen
        it, because the project a request named is the only project memory this
        query can reach whatever is asked for.
        """
        if self._memories is None:
            return None
        resolved = _resolved_memory_scopes(project_id, client_id, scopes)
        return MemoryQueryFilters(
            scopes=resolved,
            project_id=(
                project_id
                if any(value in {"project", "project_agent"} for value in resolved)
                else None
            ),
            client_id=(
                client_id
                if any(value in {"agent", "project_agent"} for value in resolved)
                else None
            ),
            types=tuple(memory_types),
            tags=tuple(tags),
            status="active" if active_only else None,
            minimum_importance=minimum_importance,
        )

    async def _gather_memory_matches(
        self,
        query: str,
        scope: MemoryQueryFilters | None,
        *,
        embedded: EmbeddingVector | None,
        fetch_limit: int,
        phrase: bool = False,
        methods: Sequence[RetrievalMethod] | None = None,
    ) -> tuple[tuple[MemoryMatch, ...], tuple[MemoryMatch, ...]]:
        """Run both memory searches at once over the scopes in reach."""
        factory = self._memories
        if scope is None or factory is None:
            return (), ()
        repository = factory(scope.project_id, scope.client_id)
        selected = self._requested_methods(methods)
        lexical = (
            repository.text_search(
                query,
                limit=fetch_limit,
                phrase=phrase,
                filters=scope,
            )
            if "lexical" in selected
            else None
        )
        vector = (
            self._memory_vector_matches(
                embedded,
                repository,
                scope=scope,
                fetch_limit=fetch_limit,
            )
            if "vector" in selected and embedded is not None
            else None
        )
        if vector is None:
            return (), () if lexical is None else await lexical
        if lexical is None:
            return await vector, ()
        return await asyncio.gather(vector, lexical)

    async def _memory_vector_matches(
        self,
        embedded: EmbeddingVector,
        repository: MemorySearchRepository,
        *,
        scope: MemoryQueryFilters,
        fetch_limit: int,
    ) -> tuple[MemoryMatch, ...]:
        """Search the memory vectors in reach with the query's vector."""
        return await repository.vector_search(
            embedded.values,
            limit=fetch_limit,
            num_candidates=self._num_candidates(fetch_limit),
            filters=scope,
        )

    async def _context_vector_matches(
        self,
        embedded: EmbeddingVector,
        repository: ContextSearchRepository,
        *,
        scope: ContextQueryFilters,
        fetch_limit: int,
    ) -> tuple[ProjectContextMatch, ...]:
        """Search one project's context vectors with the query's vector."""
        return await repository.vector_search(
            embedded.values,
            limit=fetch_limit,
            num_candidates=self._num_candidates(fetch_limit),
            filters=scope,
        )

    async def _vector_matches(
        self,
        embedded: EmbeddingVector,
        *,
        prefilter: Sequence[SkillVersionKey],
        fetch_limit: int,
    ) -> tuple[SkillChunkMatch, ...]:
        """Search the skill chunk vector index with the query's vector."""
        return await self._chunks.vector_search(
            embedded.values,
            limit=fetch_limit,
            num_candidates=self._num_candidates(fetch_limit),
            skill_versions=prefilter,
        )

    @staticmethod
    def _candidates(
        matches: Sequence[SkillChunkMatch],
        skills: Mapping[SkillVersionKey, SkillDocument],
    ) -> tuple[RetrievalMatch, ...]:
        """Describe scored chunks as candidates the ranking layer understands.

        A match whose skill version is missing from the resolved scope is
        dropped here, so a result outside the requested filters can never reach
        the ranking.
        """
        return tuple(
            RetrievalMatch(candidate=skill_candidate(match.chunk, skill), score=match.score)
            for match in matches
            if (skill := skills.get(match.key)) is not None
        )

    @staticmethod
    def _validate_scope(limit: int, skill_id: str | None, skill_version: str | None) -> None:
        if isinstance(limit, bool) or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        if skill_version is not None and skill_id is None:
            raise ValueError("skill_version requires skill_id")

    async def _resolve_versions(
        self,
        *,
        skill_id: str | None,
        skill_version: str | None,
        category: str | None,
        tags: Sequence[str],
        trust_level: SkillTrustLevel | None,
        active_only: bool,
    ) -> tuple[SkillVersionKey, ...]:
        """Translate skill metadata filters into chunk-level version keys."""
        status: LifecycleStatus | None = None
        if skill_version is None and active_only:
            status = "active"
        return await self._skills.list_version_keys(
            status=status,
            skill_id=skill_id,
            version=skill_version,
            category=category,
            tags=tags,
            trust_level=trust_level,
        )

    @staticmethod
    def _scope(
        keys: tuple[SkillVersionKey, ...],
        limit: int,
    ) -> tuple[tuple[SkillVersionKey, ...], int]:
        """Choose between pre-filtering and over-fetching with verification."""
        if len(keys) <= MAX_PREFILTER_VERSIONS:
            return keys, limit
        return (), min(limit * _FALLBACK_OVERFETCH, MAX_SEARCH_LIMIT)

    async def _results(
        self,
        matches: Sequence[SkillChunkMatch],
        keys: Sequence[SkillVersionKey],
        limit: int,
        method: RetrievalMethod,
    ) -> tuple[SkillChunkSearchResult, ...]:
        """Verify the requested scope and attach the source skill metadata."""
        allowed = set(keys)
        selected = tuple(match for match in matches if match.key in allowed)[:limit]
        if not selected:
            return ()
        by_key = await self._skill_index(selected, keys)
        return tuple(
            SkillChunkSearchResult(
                skill=skill,
                chunk=match.chunk,
                score=match.score,
                method=method,
            )
            for match in selected
            if (skill := by_key.get(match.key)) is not None
        )

    async def _skill_index(
        self,
        matches: Sequence[SkillChunkMatch],
        keys: Sequence[SkillVersionKey],
    ) -> Mapping[SkillVersionKey, SkillDocument]:
        """Load the skill metadata behind a set of matches in one query.

        Only versions inside the resolved scope are looked up, which is what
        makes the absence of a skill here a rejection rather than an omission.
        """
        allowed = set(keys)
        wanted = tuple(dict.fromkeys(match.key for match in matches if match.key in allowed))
        if not wanted:
            return {}
        documents = await self._skills.get_many(wanted)
        return {SkillVersionKey(item.skill_id, item.version): item for item in documents}

    def _num_candidates(self, limit: int) -> int:
        """Size the approximate-search candidate pool within MongoDB's bounds."""
        bounded = min(limit * self._candidate_multiplier, self._max_candidates, MAX_NUM_CANDIDATES)
        return max(bounded, limit)


def context_candidate(document: ProjectContextDocument) -> RetrievalCandidate:
    """Describe one context document as a source-agnostic candidate.

    Context carries its own project, importance, lifecycle, and tags, so it
    arrives at the ranking already describing itself; the token count is
    estimated with the same counter that sized skill chunks, so one budget
    means one thing across both sources.
    """
    return RetrievalCandidate(
        key=context_key(document.project_id, document.id),
        source=CONTEXT_SOURCE,
        title=document.title,
        content=document.content,
        tokens=estimate_tokens(document.content),
        updated_at=document.updated_at,
        importance=document.importance,
        project_id=document.project_id,
        is_active=document.status == "active",
        category=None,
        tags=tuple(document.tags),
        trust_level=None,
        metadata={
            "context_id": str(document.id),
            "project_id": document.project_id,
            "type": document.type,
            "status": document.status,
            "source": document.source,
            "importance": document.importance,
            "tags": list(document.tags),
            "external_id": document.external_id,
        },
    )


def memory_candidate(document: MemoryDocument) -> RetrievalCandidate:
    """Describe one memory as a source-agnostic candidate.

    A global memory has no project owner, so it is visible to every request; a
    project memory carries its project and is visible only there. The summary
    becomes the title when one was written, because a memory has no other short
    label to show a reader.
    """
    return RetrievalCandidate(
        key=memory_key(document.scope, document.project_id, document.client_id, document.id),
        source=MEMORY_SOURCE,
        title=document.summary or f"{document.type} memory",
        content=document.content,
        tokens=estimate_tokens(document.content),
        updated_at=document.updated_at,
        importance=document.importance,
        project_id=document.project_id,
        client_id=document.client_id,
        is_active=document.status == "active",
        category=None,
        tags=tuple(document.tags),
        trust_level=None,
        metadata={
            "memory_id": str(document.id),
            "scope": document.scope,
            "project_id": document.project_id,
            "client_id": document.client_id,
            "type": document.type,
            "status": document.status,
            "source": document.source,
            "importance": document.importance,
            "confidence": document.confidence,
            "tags": list(document.tags),
            "external_id": document.external_id,
            "supersedes": None if document.supersedes is None else str(document.supersedes),
            "superseded_by": (
                None if document.superseded_by is None else str(document.superseded_by)
            ),
        },
    )


def _memory_candidates(matches: Sequence[MemoryMatch]) -> tuple[RetrievalMatch, ...]:
    """Turn scored memories into ranking candidates."""
    return tuple(
        RetrievalMatch(candidate=memory_candidate(match.memory), score=match.score)
        for match in matches
    )


def _resolved_memory_scopes(
    project_id: str | None,
    client_id: str | None,
    requested: Sequence[MemoryScope] | None,
) -> tuple[MemoryScope, ...]:
    """Return the only scopes an authenticated client may search.

    Legacy callers keep the V1 global/project union. A client-linked request
    additionally sees only its own operational scopes, never a caller-supplied
    identity or another client's state.
    """
    if requested is not None:
        return tuple(requested)
    scopes: list[MemoryScope] = ["global"]
    if project_id is not None:
        scopes.append("project")
    if client_id is not None:
        scopes.append("agent")
        if project_id is not None:
            scopes.append("project_agent")
    return tuple(scopes)


def _context_candidates(matches: Sequence[ProjectContextMatch]) -> tuple[RetrievalMatch, ...]:
    """Turn scored context documents into ranking candidates."""
    return tuple(
        RetrievalMatch(candidate=context_candidate(match.context), score=match.score)
        for match in matches
    )


def skill_candidate(chunk: SkillChunkDocument, skill: SkillDocument) -> RetrievalCandidate:
    """Describe one stored skill chunk as a source-agnostic candidate.

    Skills are global knowledge: they have no project owner, so they are
    visible to every project, and they carry no curator-assigned importance, so
    they score neutrally on it. Project context and memory bring a real
    importance value with them when their phases add them as sources.
    """
    section = chunk.section
    return RetrievalCandidate(
        key=f"{_CHUNK_KEY_PREFIX}:{chunk.skill_id}:{chunk.skill_version}:{chunk.chunk_id}",
        source=SKILL_SOURCE,
        title=f"{skill.name}: {section}" if section else skill.name,
        content=chunk.content,
        tokens=chunk.tokens,
        updated_at=chunk.updated_at,
        importance=NEUTRAL_IMPORTANCE,
        project_id=None,
        is_active=skill.status == "active",
        category=skill.category,
        tags=tuple(skill.tags),
        trust_level=skill.trust_level,
        metadata={
            "skill_id": chunk.skill_id,
            "skill_version": chunk.skill_version,
            "chunk_id": chunk.chunk_id,
            "chunk_index": chunk.chunk_index,
            "section": section,
            "category": skill.category,
            "tags": list(skill.tags),
            "trust_level": skill.trust_level,
            "status": skill.status,
            "source_path": skill.source_path,
        },
    )
