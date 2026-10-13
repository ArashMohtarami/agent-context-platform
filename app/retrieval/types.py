"""Source-agnostic values passed through the hybrid retrieval pipeline.

Nothing here knows how a candidate was stored or how it will be transported, so
ranking, filtering, and budgeting stay independent of MongoDB and of FastAPI.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from types import MappingProxyType
from typing import Final, Literal

from app.skills.types import RetrievalMethod

# The retrievable sources named by the ranking model.
SourceType = Literal[
    "project_context",
    "active_skill",
    "memory",
    "conversation",
    # V2 content sources are intentionally not added to ``ALL_SOURCES`` or
    # ``RETRIEVABLE_SOURCES`` below.  Those constants define V1's engineering
    # search surface and must stay stable; V2's content retriever opts into
    # these source names explicitly.
    "content_idea",
    "content_brief",
    "content_item",
    "published_content",
    # V3 research candidates use these names without changing the stable V1
    # ``ALL_SOURCES`` and ``RETRIEVABLE_SOURCES`` constants below.
    "research_primary",
    "research_official_documentation",
    "research_source_code",
    "research_standard",
    "research_academic",
    "research_secondary",
    "research_community",
    "research_generated",
    "research_unknown",
    "research_note",
    "research_claim",
]
# Canonical order, used wherever a set of sources is reported so that a trace or
# a log line reads the same way for equivalent requests.
ALL_SOURCES: Final[tuple[SourceType, ...]] = (
    "project_context",
    "active_skill",
    "memory",
    "conversation",
)
# What a search can actually reach today. A conversation is stored but carries
# no vector and no search index, so nothing can retrieve one yet; it is named
# here rather than omitted, so asking for it is answered instead of rejected.
RETRIEVABLE_SOURCES: Final[tuple[SourceType, ...]] = (
    "project_context",
    "active_skill",
    "memory",
)

# Importance is a curator-assigned value carried by context and memory. Sources
# that do not rate their content score neutrally, which leaves ordering to the
# signals that do discriminate.
NEUTRAL_IMPORTANCE: Final = 0.5
NO_METADATA: Final[Mapping[str, object]] = MappingProxyType({})

# Token budget bounds have one definition, shared by the context builder that
# enforces them and the settings that configure them.
DEFAULT_TOKEN_BUDGET: Final = 4_000
# A budget below this cannot hold a single skill chunk, and one above the
# maximum stops being a budget at all.
MIN_TOKEN_BUDGET: Final = 100
MAX_TOKEN_BUDGET: Final = 16_000


def validate_fraction(value: float, name: str) -> float:
    """Require a finite number in the closed unit interval."""
    if isinstance(value, bool) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return float(value)


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    """One retrievable unit of context, independent of where it is stored.

    `key` is the stable identity used to merge the same candidate found by two
    searches and to break ranking ties, so it must be unique across sources.
    """

    key: str
    source: SourceType
    title: str
    content: str
    tokens: int
    updated_at: datetime
    importance: float = NEUTRAL_IMPORTANCE
    project_id: str | None = None
    client_id: str | None = None
    is_active: bool = True
    category: str | None = None
    tags: tuple[str, ...] = ()
    trust_level: str | None = None
    metadata: Mapping[str, object] = NO_METADATA

    def __post_init__(self) -> None:
        """Reject candidates that could not be ranked, deduplicated, or budgeted."""
        if not self.key:
            raise ValueError("a retrieval candidate requires a stable key")
        if not self.content:
            raise ValueError("a retrieval candidate requires content")
        if isinstance(self.tokens, bool) or self.tokens < 0:
            raise ValueError("tokens must not be negative")
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone aware")
        validate_fraction(self.importance, "importance")


@dataclass(frozen=True, slots=True)
class RetrievalMatch:
    """One candidate as a single search method returned it, with its raw score."""

    candidate: RetrievalCandidate
    score: float

    def __post_init__(self) -> None:
        """Reject scores that would silently corrupt normalization."""
        if isinstance(self.score, bool) or not isfinite(self.score):
            raise ValueError("a retrieval match requires a finite score")


@dataclass(frozen=True, slots=True)
class SourceMatches:
    """Everything the searches returned, before anything is ranked.

    `searched` names the sources a query actually ran against, which is what
    turns an empty answer into an explanation: a source contributes nothing
    either because it found nothing or because it was never asked.
    """

    vector: tuple[RetrievalMatch, ...] = ()
    lexical: tuple[RetrievalMatch, ...] = ()
    searched: tuple[SourceType, ...] = ()


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """One merged candidate carrying whichever raw method scores found it."""

    candidate: RetrievalCandidate
    vector_score: float | None = None
    lexical_score: float | None = None

    @property
    def methods(self) -> tuple[RetrievalMethod, ...]:
        """Return the searches that found this candidate."""
        found: list[RetrievalMethod] = []
        if self.vector_score is not None:
            found.append("vector")
        if self.lexical_score is not None:
            found.append("lexical")
        return tuple(found)


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """Why one result scored what it did, for evaluation and debugging."""

    vector: float
    lexical: float
    importance: float
    recency: float
    source_priority: float
    final: float
    methods: tuple[RetrievalMethod, ...]
    raw_vector: float | None = None
    raw_lexical: float | None = None

    def as_mapping(self) -> Mapping[str, object]:
        """Return a flat, log-safe view of the contributing signals."""
        return MappingProxyType(
            {
                "vector": self.vector,
                "lexical": self.lexical,
                "importance": self.importance,
                "recency": self.recency,
                "source_priority": self.source_priority,
                "final": self.final,
                "methods": list(self.methods),
                "raw_vector": self.raw_vector,
                "raw_lexical": self.raw_lexical,
            }
        )


@dataclass(frozen=True, slots=True)
class RankedResult:
    """One candidate with its combined score and the reasoning behind it."""

    candidate: RetrievalCandidate
    score: float
    breakdown: ScoreBreakdown


@dataclass(frozen=True, slots=True)
class RetrievalDiagnostics:
    """Explainable retrieval metadata, kept inside the application.

    Callers receive results; operators and retrieval evaluation receive this,
    which is what makes a surprising ranking reproducible instead of a guess.
    """

    methods: tuple[RetrievalMethod, ...]
    weights: Mapping[str, float]
    vector_matches: int = 0
    lexical_matches: int = 0
    merged: int = 0
    filtered_out: int = 0
    duplicates_removed: int = 0
    ranked: int = 0
    token_budget: int = 0
    dropped_for_budget: int = 0

    def as_mapping(self) -> Mapping[str, object]:
        """Return a flat, log-safe view of the pipeline counters."""
        return MappingProxyType(
            {
                "methods": list(self.methods),
                "weights": dict(self.weights),
                "vector_matches": self.vector_matches,
                "lexical_matches": self.lexical_matches,
                "merged": self.merged,
                "filtered_out": self.filtered_out,
                "duplicates_removed": self.duplicates_removed,
                "ranked": self.ranked,
                "token_budget": self.token_budget,
                "dropped_for_budget": self.dropped_for_budget,
            }
        )
