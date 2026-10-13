"""Deterministic hybrid ranking, isolated from storage and transport.

Every function here is pure: the same candidates, weights, and reference time
always produce the same order. Nothing in this module reads a clock, a database,
or a request.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from types import MappingProxyType
from typing import Final

from app.retrieval.types import (
    RankedResult,
    ScoreBreakdown,
    ScoredCandidate,
    SourceType,
    validate_fraction,
)
from app.skills.types import RetrievalMethod

# Project-specific knowledge should normally beat generic knowledge, so the
# source a result came from is itself a weak ranking signal. These are starting
# values for retrieval evaluation, not measured optima.
SOURCE_PRIORITY: Final[Mapping[SourceType, float]] = MappingProxyType(
    {
        "project_context": 1.00,
        "active_skill": 0.95,
        "memory": 0.80,
        "conversation": 0.50,
    }
)
DEFAULT_RECENCY_HALF_LIFE_DAYS: Final = 90.0
# Scores are rounded before they are compared so that equivalent inputs cannot
# be ordered differently by floating-point noise.
SCORE_PRECISION: Final = 6
_SECONDS_PER_DAY: Final = 86_400.0
_WEIGHT_NAMES: Final = ("vector", "lexical", "importance", "recency", "source_priority")


@dataclass(frozen=True, slots=True)
class RankingWeights:
    """Relative influence of each ranking signal.

    Weights express relative importance and are normalized before use, so any
    configuration keeps the final score inside 0..1 and comparable between
    queries.
    """

    vector: float = 0.45
    lexical: float = 0.25
    importance: float = 0.15
    recency: float = 0.10
    source_priority: float = 0.05

    def __post_init__(self) -> None:
        """Reject weights that could not produce a meaningful ranking."""
        for name in _WEIGHT_NAMES:
            value: float = getattr(self, name)
            if isinstance(value, bool) or not isfinite(value) or value < 0.0:
                raise ValueError(f"the {name} weight must be a finite, non-negative number")
        if self.total <= 0.0:
            raise ValueError("at least one ranking weight must be greater than zero")

    @property
    def total(self) -> float:
        """Return the sum of every weight."""
        return self.vector + self.lexical + self.importance + self.recency + self.source_priority

    def normalized(self) -> "RankingWeights":
        """Rescale the weights so they sum to one."""
        total = self.total
        return RankingWeights(
            vector=self.vector / total,
            lexical=self.lexical / total,
            importance=self.importance / total,
            recency=self.recency / total,
            source_priority=self.source_priority / total,
        )

    def for_methods(self, methods: Sequence[RetrievalMethod]) -> "RankingWeights":
        """Drop the weight of a search that did not run and rebalance.

        Without this, a lexical-only retrieval (no embedding model) would score
        every result against a vector component that could never contribute,
        compressing every score toward zero for no reason.
        """
        selected = set(methods)
        return RankingWeights(
            vector=self.vector if "vector" in selected else 0.0,
            lexical=self.lexical if "lexical" in selected else 0.0,
            importance=self.importance,
            recency=self.recency,
            source_priority=self.source_priority,
        ).normalized()

    def as_mapping(self) -> Mapping[str, float]:
        """Return a log-safe view of the applied weights."""
        return MappingProxyType({name: getattr(self, name) for name in _WEIGHT_NAMES})


DEFAULT_WEIGHTS: Final = RankingWeights()


def normalize_bounded(scores: Sequence[float]) -> tuple[float, ...]:
    """Clamp scores that already carry their own 0..1 meaning.

    Cosine similarity is reported on a fixed scale, so rescaling it against the
    result set would throw away the absolute quality it already expresses.
    """
    return tuple(min(max(_finite(score), 0.0), 1.0) for score in scores)


def normalize_relative(scores: Sequence[float]) -> tuple[float, ...]:
    """Scale unbounded relevance against the best score in the same result set.

    Lexical relevance has no upper bound and is only comparable within one
    query. Dividing by the best score preserves the ratios between results,
    where min-max scaling would flatten the weakest result to zero and magnify
    differences that are not meaningful.
    """
    values = [_finite(score) for score in scores]
    largest = max(values, default=0.0)
    if largest <= 0.0:
        return tuple(0.0 for _ in values)
    return tuple(min(max(value / largest, 0.0), 1.0) for value in values)


def recency_score(
    updated_at: datetime,
    *,
    now: datetime,
    half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS,
) -> float:
    """Decay a timestamp by half every `half_life_days`.

    `now` is an argument rather than a clock read so that ranking stays
    reproducible.
    """
    if isinstance(half_life_days, bool) or not isfinite(half_life_days) or half_life_days <= 0.0:
        raise ValueError("half_life_days must be a positive number")
    if updated_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("recency requires timezone-aware timestamps")
    age_days = (now - updated_at).total_seconds() / _SECONDS_PER_DAY
    if age_days <= 0.0:
        # Content written in the same instant, or a clock skewed backwards, is
        # as recent as content can be rather than unboundedly recent.
        return 1.0
    decayed: float = 0.5 ** (age_days / half_life_days)
    return round(decayed, SCORE_PRECISION)


def source_priority(
    source: SourceType,
    priorities: Mapping[SourceType, float] | None = None,
) -> float:
    """Return one source's configured priority.

    V2 retrieval supplies a profile-local mapping here.  Leaving it absent is
    deliberately the exact V1 behavior and keeps engineering ranking stable.
    """
    if priorities is None:
        return SOURCE_PRIORITY[source]
    try:
        return validate_fraction(priorities[source], "source priority")
    except KeyError:
        return SOURCE_PRIORITY.get(source, 0.5)


def rank_candidates(
    scored: Sequence[ScoredCandidate],
    *,
    methods: Sequence[RetrievalMethod],
    now: datetime,
    weights: RankingWeights = DEFAULT_WEIGHTS,
    half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS,
    source_priorities: Mapping[SourceType, float] | None = None,
) -> tuple[RankedResult, ...]:
    """Normalize every signal, combine them, and order the results.

    A candidate only one search found contributes nothing from the other
    method, so agreement between vector and lexical retrieval ranks higher than
    a single strong hit.
    """
    if not scored:
        return ()
    applied = weights.for_methods(methods)
    vectors = normalize_bounded(
        [0.0 if item.vector_score is None else item.vector_score for item in scored]
    )
    lexicals = _normalized_lexical(scored)
    results = [
        _score_one(
            item,
            vector,
            lexical,
            applied,
            now=now,
            half_life_days=half_life_days,
            source_priorities=source_priorities,
        )
        for item, vector, lexical in zip(scored, vectors, lexicals, strict=True)
    ]
    return tuple(sorted(results, key=_order_key))


def _normalized_lexical(scored: Sequence[ScoredCandidate]) -> tuple[float, ...]:
    """Scale lexical relevance within each source separately.

    A lexical score reflects the statistics of the index it came from, so a
    skill chunk's relevance and a context entry's relevance are only comparable
    after each is scaled against the best result of its own search. Scaling
    them together would rank one collection's index against another's.
    """
    positions: dict[SourceType, list[int]] = defaultdict(list)
    for index, item in enumerate(scored):
        positions[item.candidate.source].append(index)
    normalized = [0.0] * len(scored)
    for indexes in positions.values():
        values = normalize_relative([_lexical(scored[index]) for index in indexes])
        for offset, index in enumerate(indexes):
            normalized[index] = values[offset]
    return tuple(normalized)


def _lexical(item: ScoredCandidate) -> float:
    """Return one candidate's raw lexical score, absent meaning no match."""
    return 0.0 if item.lexical_score is None else item.lexical_score


def _score_one(
    item: ScoredCandidate,
    vector: float,
    lexical: float,
    weights: RankingWeights,
    *,
    now: datetime,
    half_life_days: float,
    source_priorities: Mapping[SourceType, float] | None,
) -> RankedResult:
    """Combine one candidate's normalized signals into a final score."""
    candidate = item.candidate
    importance = validate_fraction(candidate.importance, "importance")
    recency = recency_score(candidate.updated_at, now=now, half_life_days=half_life_days)
    priority = source_priority(candidate.source, source_priorities)
    # Summed in a fixed order so the result cannot depend on iteration order.
    final = round(
        weights.vector * vector
        + weights.lexical * lexical
        + weights.importance * importance
        + weights.recency * recency
        + weights.source_priority * priority,
        SCORE_PRECISION,
    )
    breakdown = ScoreBreakdown(
        vector=vector,
        lexical=lexical,
        importance=importance,
        recency=recency,
        source_priority=priority,
        final=final,
        methods=item.methods,
        raw_vector=item.vector_score,
        raw_lexical=item.lexical_score,
    )
    return RankedResult(candidate=candidate, score=final, breakdown=breakdown)


def _order_key(result: RankedResult) -> tuple[float, float, int, float, str]:
    """Order by score, then by the preferences ties should resolve to.

    Equal scores prefer the higher-priority source, then an active version over
    a superseded one, then newer content, and finally the stable candidate key
    so the order never depends on how the candidates arrived.
    """
    candidate = result.candidate
    return (
        -result.score,
        -result.breakdown.source_priority,
        0 if candidate.is_active else 1,
        -candidate.updated_at.timestamp(),
        candidate.key,
    )


def _finite(score: float) -> float:
    """Reject a score that would poison normalization."""
    if isinstance(score, bool) or not isfinite(score):
        raise ValueError("retrieval scores must be finite numbers")
    return float(score)
