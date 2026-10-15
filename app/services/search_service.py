"""Unified knowledge search: one query, every source that can answer it.

This is the retrieval interface the platform is for. A caller asks a question
once and receives compact, budgeted context drawn from skills, a project's own
recorded context, and memory, ranked together rather than concatenated.

The service owns the pipeline and nothing else: `RetrievalService` runs the
searches, `app.retrieval` ranks and budgets what they return, and the hooks in
`app.search.logging` record what happened. Every stage is the same one the
individual searches already use, so the unified answer cannot rank differently
from the endpoint it replaces.
"""

from collections.abc import Sequence
from time import perf_counter
from typing import Final
from uuid import uuid4

from app.models.memory import MemoryScope
from app.models.project_context import ContextType
from app.models.skill import SkillTrustLevel
from app.observability import context as correlation
from app.observability.instruments import record_retrieval
from app.repositories.skill_chunk_repository import DEFAULT_SEARCH_LIMIT
from app.retrieval.filters import RetrievalFilters
from app.retrieval.query import normalize_query
from app.retrieval.types import SourceType
from app.search.logging import RetrievalLogEntry, RetrievalLogger
from app.search.types import (
    KnowledgeSearchResult,
    RetrievalTrace,
    resolve_sources,
    retrieval_method,
)
from app.security.tenant_service import tenant_service
from app.services.retrieval_service import SKILL_SOURCE, RetrievalService

# Latency is reported to the nearest microsecond: finer resolution would be
# noise, and coarser would hide the difference between a fast and a slow query.
LATENCY_PRECISION: Final = 3
# What this service does, as one name, so a metric series and a log line agree.
OPERATION: Final = "search_knowledge"


@tenant_service
class SearchService:
    """Answer one question from every source a request is allowed to see.

    Project isolation is not this layer's invention: it is stated once in
    `RetrievalFilters`, applied by the searches as a pre-filter, and applied
    again here on every candidate before ranking. A unified search reaches more
    sources than any single one, which is exactly why it re-states the rule
    rather than assuming the sources enforced it.
    """

    def __init__(
        self,
        retrieval: RetrievalService,
        *,
        logger: RetrievalLogger | None = None,
    ) -> None:
        self._retrieval = retrieval
        self._logger = logger

    async def search_knowledge(
        self,
        query: str,
        *,
        project_id: str | None = None,
        sources: Sequence[SourceType] | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        token_budget: int | None = None,
        skill_id: str | None = None,
        skill_version: str | None = None,
        category: str | None = None,
        context_types: Sequence[ContextType] = (),
        memory_types: Sequence[str] = (),
        memory_scopes: Sequence[MemoryScope] | None = None,
        memory_client_id: str | None = None,
        tags: Sequence[str] = (),
        trust_level: SkillTrustLevel | None = None,
        minimum_importance: float = 0.0,
        active_only: bool = True,
        phrase: bool = False,
    ) -> KnowledgeSearchResult:
        """Run the whole retrieval pipeline and return budgeted context.

        The stages are: normalize the query, embed it, search every requested
        source both ways at once, filter what the request may see, rank the
        combined candidates, drop what a better result already covers, fill the
        token budget, and report what happened.

        Restricting `sources` does two things, deliberately: a source that was
        not asked for is never searched, and is also refused at the filter, so
        one of the two failing cannot put it in an answer.
        """
        started = perf_counter()
        normalized = normalize_query(query)
        requested = resolve_sources(sources)
        searchable = _searchable(
            requested,
            skill_only=any(
                value is not None for value in (skill_id, skill_version, category, trust_level)
            ),
        )
        # Refused before any search runs, so an impossible budget costs nothing.
        budget = self._retrieval.resolve_budget(token_budget)
        matches = await self._retrieval.gather_sources(
            normalized,
            limit=limit,
            sources=searchable,
            project_id=project_id,
            skill_id=skill_id,
            skill_version=skill_version,
            category=category,
            context_types=context_types,
            memory_types=memory_types,
            memory_scopes=memory_scopes,
            memory_client_id=memory_client_id,
            tags=tags,
            trust_level=trust_level,
            active_only=active_only,
            minimum_importance=minimum_importance,
            phrase=phrase,
        )
        context = self._retrieval.rank_context(
            normalized,
            matches,
            filters=RetrievalFilters(
                project_id=project_id,
                memory_client_id=memory_client_id,
                sources=requested,
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
        elapsed = perf_counter() - started
        result = KnowledgeSearchResult(
            context=context,
            trace=RetrievalTrace(
                query_id=uuid4().hex,
                retrieval_method=retrieval_method(self._retrieval.methods),
                requested_sources=requested,
                searched_sources=matches.searched,
                skipped_sources=tuple(
                    source for source in requested if source not in matches.searched
                ),
                latency_ms=round(elapsed * 1_000, LATENCY_PRECISION),
            ),
        )
        # The identifiers this search produced belong to whatever call is
        # running it, so an operator reading one HTTP access line can see the
        # query it ran and how much it found without joining two lines.
        # Outside a correlated call this does nothing.
        correlation.annotate(
            operation=OPERATION,
            project_id=project_id,
            query_id=result.trace.query_id,
            result_count=len(result.result_keys),
        )
        record_retrieval(
            operation=OPERATION,
            seconds=elapsed,
            result_count=len(result.result_keys),
        )
        await self._record(result, query=normalized, project_id=project_id)
        return result

    async def _record(
        self,
        result: KnowledgeSearchResult,
        *,
        query: str,
        project_id: str | None,
    ) -> None:
        """Report the finished search to the configured retrieval hook."""
        if self._logger is None:
            return
        await self._logger.record(
            RetrievalLogEntry.from_trace(
                result.trace,
                query=query,
                project_id=project_id,
                result_keys=result.result_keys,
            )
        )


def _searchable(
    requested: Sequence[SourceType],
    *,
    skill_only: bool,
) -> tuple[SourceType, ...]:
    """Drop the sources a skill-only filter makes unanswerable.

    `skill_id`, `skill_version`, `category`, and `trust_level` describe a
    skill. No context entry and no memory carries one, so a request naming any
    of them is a request about skills: searching the other sources would return
    candidates that the same filter then discards, which reads exactly like
    those sources being empty. Reporting them as skipped says what happened
    instead.
    """
    if not skill_only:
        return tuple(requested)
    return tuple(source for source in requested if source == SKILL_SOURCE)
