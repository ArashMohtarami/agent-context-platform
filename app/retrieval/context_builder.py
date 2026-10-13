"""Compact, budgeted context assembled from ranked retrieval results.

The platform stores a lot and retrieves little: this module is where "little"
is enforced, by filling a token budget in rank order and stopping.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from app.retrieval.types import (
    DEFAULT_TOKEN_BUDGET,
    MAX_TOKEN_BUDGET,
    MIN_TOKEN_BUDGET,
    RankedResult,
    RetrievalDiagnostics,
    ScoreBreakdown,
    SourceType,
)

__all__ = [
    "DEFAULT_TOKEN_BUDGET",
    "MAX_TOKEN_BUDGET",
    "MIN_TOKEN_BUDGET",
    "ContextItem",
    "RetrievalContext",
    "build_context",
    "resolve_token_budget",
]


@dataclass(frozen=True, slots=True)
class ContextItem:
    """One piece of retrieved context with the source it came from.

    `key` is the candidate's stable identity, carried through so a returned
    result can be referred to afterwards — by a caller asking for it again, or
    by a retrieval log recording what a query actually selected.
    """

    key: str
    source: SourceType
    title: str
    content: str
    score: float
    tokens: int
    metadata: Mapping[str, object]
    explanation: ScoreBreakdown


@dataclass(frozen=True, slots=True)
class RetrievalContext:
    """Everything one query produced, within its budget."""

    query: str
    project_id: str | None
    items: tuple[ContextItem, ...]
    token_count: int
    token_budget: int
    diagnostics: RetrievalDiagnostics


def resolve_token_budget(
    requested: int | None,
    *,
    default: int = DEFAULT_TOKEN_BUDGET,
    maximum: int = MAX_TOKEN_BUDGET,
) -> int:
    """Clamp a requested budget into the configured range.

    A caller may ask for less context than the default, but never for more than
    the deployment allows, which is what stops one request from pulling the
    whole database into a prompt.
    """
    if isinstance(maximum, bool) or not MIN_TOKEN_BUDGET <= maximum <= MAX_TOKEN_BUDGET:
        raise ValueError(
            f"the maximum token budget must be between {MIN_TOKEN_BUDGET} and {MAX_TOKEN_BUDGET}"
        )
    if isinstance(default, bool) or not MIN_TOKEN_BUDGET <= default <= maximum:
        raise ValueError("the default token budget must not exceed the maximum")
    if requested is None:
        return default
    if isinstance(requested, bool) or requested < MIN_TOKEN_BUDGET:
        raise ValueError(f"a token budget must be at least {MIN_TOKEN_BUDGET}")
    return min(requested, maximum)


def build_context(
    query: str,
    results: Sequence[RankedResult],
    *,
    diagnostics: RetrievalDiagnostics,
    project_id: str | None = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> RetrievalContext:
    """Fill the token budget in rank order without truncating any result.

    A result that does not fit is skipped and a smaller later one may still be
    included, so a single oversized result cannot waste the whole budget.
    Content is never cut short: half a technical rule reads like a whole one,
    and that is worse than omitting it.
    """
    if isinstance(token_budget, bool) or token_budget < MIN_TOKEN_BUDGET:
        raise ValueError(f"a token budget must be at least {MIN_TOKEN_BUDGET}")
    items: list[ContextItem] = []
    used = 0
    dropped = 0
    for result in results:
        tokens = result.candidate.tokens
        if used + tokens > token_budget:
            dropped += 1
            continue
        items.append(_item(result))
        used += tokens
    return RetrievalContext(
        query=query,
        project_id=project_id,
        items=tuple(items),
        token_count=used,
        token_budget=token_budget,
        diagnostics=replace(
            diagnostics,
            token_budget=token_budget,
            dropped_for_budget=dropped,
        ),
    )


def _item(result: RankedResult) -> ContextItem:
    """Build one context item, preserving where the content came from."""
    candidate = result.candidate
    return ContextItem(
        key=candidate.key,
        source=candidate.source,
        title=candidate.title,
        content=candidate.content,
        score=result.score,
        tokens=candidate.tokens,
        metadata=candidate.metadata,
        explanation=result.breakdown,
    )
