"""Hybrid retrieval: merge vector and lexical matches, filter, rank, deduplicate.

This module receives results that a caller has already fetched, so the fusion
algorithm can be exercised without MongoDB and the storage layer never learns
how results are ranked.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from app.retrieval.filters import RetrievalFilters
from app.retrieval.ranking import (
    DEFAULT_RECENCY_HALF_LIFE_DAYS,
    DEFAULT_WEIGHTS,
    RankingWeights,
    rank_candidates,
)
from app.retrieval.types import (
    RankedResult,
    RetrievalDiagnostics,
    RetrievalMatch,
    ScoredCandidate,
    SourceType,
)
from app.skills.types import RetrievalMethod

DEFAULT_LIMIT: Final = 10
MAX_LIMIT: Final = 50
# Content is treated as already covered when this share of the shorter result
# also appears in a better-ranked one.
DEFAULT_DUPLICATE_OVERLAP: Final = 0.85
# Short fragments share common words by chance, so containment is only trusted
# once a result carries enough distinct words to be distinctive.
MIN_DUPLICATE_WORDS: Final = 12
_WORD_PATTERN: Final = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class HybridOutcome:
    """Ranked results plus the metadata explaining how they were produced."""

    results: tuple[RankedResult, ...]
    diagnostics: RetrievalDiagnostics


def merge_matches(
    vector_matches: Sequence[RetrievalMatch] = (),
    lexical_matches: Sequence[RetrievalMatch] = (),
) -> tuple[ScoredCandidate, ...]:
    """Combine both result sets into one entry per candidate.

    A candidate both searches found keeps both raw scores; one a single search
    found keeps only that score. A candidate repeated inside one result set
    keeps its best score rather than being counted twice.
    """
    order: list[str] = []
    vectors: dict[str, float] = {}
    lexicals: dict[str, float] = {}
    candidates: dict[str, RetrievalMatch] = {}
    for found, scores in ((vector_matches, vectors), (lexical_matches, lexicals)):
        for match in found:
            key = match.candidate.key
            if key not in candidates:
                candidates[key] = match
                order.append(key)
            scores[key] = max(scores[key], match.score) if key in scores else match.score
    return tuple(
        ScoredCandidate(
            candidate=candidates[key].candidate,
            vector_score=vectors.get(key),
            lexical_score=lexicals.get(key),
        )
        for key in order
    )


def deduplicate(
    results: Sequence[RankedResult],
    *,
    overlap: float = DEFAULT_DUPLICATE_OVERLAP,
) -> tuple[tuple[RankedResult, ...], int]:
    """Drop results whose content a better-ranked result already covers.

    Overlapping chunks are compared by how much of the shorter result appears
    in the longer one, so a chunk contained in a neighbour, or the same section
    reachable through two skill versions, is returned once. The 50-100 token
    overlap the chunker deliberately leaves between adjacent chunks is far
    below the threshold, so genuinely different neighbours both survive.

    Results are expected in rank order: the first occurrence wins, which is how
    the active and newer version is the copy that is kept.
    """
    if isinstance(overlap, bool) or not 0.0 < overlap <= 1.0:
        raise ValueError("the duplicate overlap threshold must be above 0 and at most 1")
    kept: list[RankedResult] = []
    seen: list[frozenset[str]] = []
    removed = 0
    for result in results:
        words = _words(result.candidate.content)
        if any(_covers(words, earlier, overlap) for earlier in seen):
            removed += 1
            continue
        kept.append(result)
        seen.append(words)
    return tuple(kept), removed


def hybrid_rank(
    vector_matches: Sequence[RetrievalMatch] = (),
    lexical_matches: Sequence[RetrievalMatch] = (),
    *,
    methods: Sequence[RetrievalMethod],
    now: datetime,
    filters: RetrievalFilters | None = None,
    weights: RankingWeights = DEFAULT_WEIGHTS,
    half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS,
    duplicate_overlap: float = DEFAULT_DUPLICATE_OVERLAP,
    limit: int = DEFAULT_LIMIT,
    source_priorities: Mapping[SourceType, float] | None = None,
) -> HybridOutcome:
    """Run the merge, filter, rank, and deduplicate pipeline.

    Filtering happens before ranking so that scores are normalized against the
    results a caller may actually see, and so that a forbidden result is gone
    before anything else touches it.

    `methods` states which searches ran, which is not the same as which
    returned matches: a search that ran and found nothing still means its
    weight applies, while a search that never ran has its weight redistributed.
    """
    if isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    applied_filters = RetrievalFilters() if filters is None else filters
    merged = merge_matches(vector_matches, lexical_matches)
    allowed = tuple(item for item in merged if applied_filters.allows(item.candidate))
    ranked = rank_candidates(
        allowed,
        methods=methods,
        now=now,
        weights=weights,
        half_life_days=half_life_days,
        source_priorities=source_priorities,
    )
    deduplicated, duplicates = deduplicate(ranked, overlap=duplicate_overlap)
    diagnostics = RetrievalDiagnostics(
        methods=tuple(methods),
        weights=weights.for_methods(methods).as_mapping(),
        vector_matches=len(vector_matches),
        lexical_matches=len(lexical_matches),
        merged=len(merged),
        filtered_out=len(merged) - len(allowed),
        duplicates_removed=duplicates,
        ranked=len(deduplicated),
    )
    return HybridOutcome(results=deduplicated[:limit], diagnostics=diagnostics)


def _words(content: str) -> frozenset[str]:
    """Return the distinct lowercase words of one result."""
    return frozenset(_WORD_PATTERN.findall(content.casefold()))


def _covers(words: frozenset[str], earlier: frozenset[str], overlap: float) -> bool:
    """Report whether an earlier result already contains this one's content."""
    if not words or not earlier:
        return False
    smaller = min(len(words), len(earlier))
    if smaller < MIN_DUPLICATE_WORDS:
        return words == earlier
    return len(words & earlier) / smaller >= overlap
