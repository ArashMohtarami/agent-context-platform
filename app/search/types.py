"""Values produced by one unified knowledge search.

Nothing here knows how a result was stored or how it will be transported, so
the search service can be exercised, and its retrieval metadata asserted,
without MongoDB and without FastAPI.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.models.retrieval_log import RetrievalMethod as LoggedMethod
from app.retrieval.context_builder import RetrievalContext
from app.retrieval.types import ALL_SOURCES, RETRIEVABLE_SOURCES, SourceType
from app.skills.types import RetrievalMethod

# A request that names no source searches everything that can be reached, which
# is what makes this the one interface a caller needs.
DEFAULT_SOURCES: Final[tuple[SourceType, ...]] = RETRIEVABLE_SOURCES
MAX_SOURCES: Final = len(ALL_SOURCES)


def resolve_sources(requested: Sequence[SourceType] | None) -> tuple[SourceType, ...]:
    """Canonicalize the sources one request asked for.

    Duplicates are collapsed and the result is ordered by source priority, so
    two equivalent requests produce the same trace and the same log entry.
    """
    if requested is None:
        return DEFAULT_SOURCES
    wanted = set(requested)
    if not wanted:
        raise ValueError("provide at least one source to search")
    return tuple(source for source in ALL_SOURCES if source in wanted)


def retrieval_method(methods: Sequence[RetrievalMethod]) -> LoggedMethod:
    """Name the retrieval a search actually performed.

    Both searches running is hybrid retrieval; either alone is named for
    itself, which is what makes a degraded deployment visible in its own logs
    rather than indistinguishable from a healthy one.
    """
    selected = set(methods)
    if selected == {"vector", "lexical"}:
        return "hybrid"
    if selected == {"vector"}:
        return "vector"
    if selected == {"lexical"}:
        return "lexical"
    raise ValueError("a search must run at least one retrieval method")


@dataclass(frozen=True, slots=True)
class RetrievalTrace:
    """Retrieval metadata for one search, useful for debugging its results.

    This answers "why did I get this" without the caller having to guess: which
    sources were asked for, which were actually searched, which were skipped,
    what the pipeline did with the candidates, and how long it took.
    """

    query_id: str
    retrieval_method: LoggedMethod
    requested_sources: tuple[SourceType, ...]
    searched_sources: tuple[SourceType, ...]
    skipped_sources: tuple[SourceType, ...]
    latency_ms: float

    def __post_init__(self) -> None:
        """Reject a trace that could not describe a real search."""
        if not self.query_id:
            raise ValueError("a retrieval trace requires a query identifier")
        if isinstance(self.latency_ms, bool) or self.latency_ms < 0.0:
            raise ValueError("latency must not be negative")

    def as_mapping(self) -> Mapping[str, object]:
        """Return a flat, log-safe view that excludes the query text."""
        return MappingProxyType(
            {
                "query_id": self.query_id,
                "retrieval_method": self.retrieval_method,
                "requested_sources": list(self.requested_sources),
                "searched_sources": list(self.searched_sources),
                "skipped_sources": list(self.skipped_sources),
                "latency_ms": self.latency_ms,
            }
        )


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    """One unified search: the budgeted context plus how it was produced."""

    context: RetrievalContext
    trace: RetrievalTrace

    @property
    def result_keys(self) -> tuple[str, ...]:
        """Return the stable identity of every returned result, in rank order."""
        return tuple(item.key for item in self.context.items)
