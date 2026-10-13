"""Merge, filter, rank, and deduplication tests for hybrid retrieval."""

from datetime import timedelta

import pytest

from app.retrieval.filters import RetrievalFilters
from app.retrieval.hybrid import (
    MIN_DUPLICATE_WORDS,
    deduplicate,
    hybrid_rank,
    merge_matches,
)
from app.retrieval.ranking import DEFAULT_WEIGHTS, rank_candidates
from app.retrieval.types import ScoredCandidate
from tests.retrieval_doubles import NOW, candidate, match, words

BOTH = ("vector", "lexical")


def ranked(*scored: ScoredCandidate) -> tuple[object, ...]:
    """Rank candidates with the default weighting at a fixed time."""
    return rank_candidates(scored, methods=BOTH, now=NOW)


def test_a_candidate_both_searches_found_keeps_both_scores() -> None:
    merged = merge_matches([match(0.9, key="shared")], [match(7.5, key="shared")])

    assert len(merged) == 1
    assert merged[0].vector_score == 0.9
    assert merged[0].lexical_score == 7.5
    assert merged[0].methods == ("vector", "lexical")


def test_a_candidate_one_search_found_keeps_only_that_score() -> None:
    merged = merge_matches([match(0.9, key="vector-only")], [match(7.5, key="lexical-only")])

    assert [item.candidate.key for item in merged] == ["vector-only", "lexical-only"]
    assert merged[0].lexical_score is None
    assert merged[1].vector_score is None


def test_a_candidate_repeated_inside_one_result_set_keeps_its_best_score() -> None:
    merged = merge_matches([match(0.4, key="repeated"), match(0.8, key="repeated")])

    assert len(merged) == 1
    assert merged[0].vector_score == 0.8


def test_merging_nothing_returns_nothing() -> None:
    assert merge_matches() == ()


def test_identical_content_is_returned_once() -> None:
    duplicated = words(40)
    kept, removed = deduplicate(
        ranked(  # type: ignore[arg-type]
            ScoredCandidate(candidate("best", content=duplicated), vector_score=0.9),
            ScoredCandidate(candidate("copy", content=duplicated), vector_score=0.4),
        )
    )

    assert [result.candidate.key for result in kept] == ["best"]
    assert removed == 1


def test_content_already_covered_by_a_better_result_is_dropped() -> None:
    long_chunk = words(40)
    contained = " ".join(long_chunk.split()[:20])
    kept, removed = deduplicate(
        ranked(  # type: ignore[arg-type]
            ScoredCandidate(candidate("long", content=long_chunk), vector_score=0.9),
            ScoredCandidate(candidate("contained", content=contained), vector_score=0.4),
        )
    )

    assert [result.candidate.key for result in kept] == ["long"]
    assert removed == 1


def test_neighbouring_chunks_that_merely_overlap_are_both_kept() -> None:
    # The chunker deliberately repeats a small tail between adjacent chunks.
    first = words(300)
    second = " ".join(first.split()[-40:]) + " " + words(260, prefix="next")
    kept, removed = deduplicate(
        ranked(  # type: ignore[arg-type]
            ScoredCandidate(candidate("first", content=first), vector_score=0.9),
            ScoredCandidate(candidate("second", content=second), vector_score=0.8),
        )
    )

    assert [result.candidate.key for result in kept] == ["first", "second"]
    assert removed == 0


def test_a_short_fragment_is_only_dropped_when_it_is_an_exact_repeat() -> None:
    short = words(MIN_DUPLICATE_WORDS - 1)
    kept, _ = deduplicate(
        ranked(  # type: ignore[arg-type]
            ScoredCandidate(candidate("long", content=words(60)), vector_score=0.9),
            ScoredCandidate(candidate("short", content=short), vector_score=0.4),
        )
    )
    repeated, removed = deduplicate(
        ranked(  # type: ignore[arg-type]
            ScoredCandidate(candidate("a", content=short), vector_score=0.9),
            ScoredCandidate(candidate("b", content=short), vector_score=0.4),
        )
    )

    assert [result.candidate.key for result in kept] == ["long", "short"]
    assert [result.candidate.key for result in repeated] == ["a"]
    assert removed == 1


def test_the_duplicate_threshold_decides_how_much_overlap_is_too_much() -> None:
    base = words(40)
    mostly = " ".join(base.split()[:36]) + " " + words(4, prefix="extra")
    results = ranked(  # type: ignore[arg-type]
        ScoredCandidate(candidate("base", content=base), vector_score=0.9),
        ScoredCandidate(candidate("mostly", content=mostly), vector_score=0.4),
    )

    strict, strict_removed = deduplicate(results, overlap=1.0)
    lenient, lenient_removed = deduplicate(results, overlap=0.85)

    assert [result.candidate.key for result in strict] == ["base", "mostly"]
    assert strict_removed == 0
    assert [result.candidate.key for result in lenient] == ["base"]
    assert lenient_removed == 1


@pytest.mark.parametrize("overlap", [0.0, -0.5, 1.5, True])
def test_an_unusable_duplicate_threshold_is_rejected(overlap: float) -> None:
    with pytest.raises(ValueError, match="overlap"):
        deduplicate((), overlap=overlap)


def test_hybrid_retrieval_merges_ranks_and_explains_both_searches() -> None:
    outcome = hybrid_rank(
        [match(0.9, key="shared"), match(0.4, key="vector-only", content=words(30))],
        [match(8.0, key="shared"), match(2.0, key="lexical-only", content=words(30, prefix="l"))],
        methods=BOTH,
        now=NOW,
    )

    # Agreement wins; between single-method hits the heavier vector weight
    # decides, which is what the 0.45/0.25 split means.
    assert [result.candidate.key for result in outcome.results] == [
        "shared",
        "vector-only",
        "lexical-only",
    ]
    assert outcome.results[0].score == pytest.approx(0.8775)
    assert outcome.diagnostics.vector_matches == 2
    assert outcome.diagnostics.lexical_matches == 2
    assert outcome.diagnostics.merged == 3
    assert outcome.diagnostics.ranked == 3
    assert outcome.diagnostics.methods == BOTH
    assert outcome.diagnostics.weights["vector"] == pytest.approx(0.45)


def test_filtering_happens_before_scores_are_normalized() -> None:
    outcome = hybrid_rank(
        [],
        [
            match(100.0, key="forbidden", project_id="sampleco-wallet", content=words(30)),
            match(10.0, key="allowed", content=words(30, prefix="b")),
        ],
        methods=BOTH,
        now=NOW,
        filters=RetrievalFilters(project_id="sampleco-payment"),
    )

    assert [result.candidate.key for result in outcome.results] == ["allowed"]
    # Normalizing against the excluded result would have scored this 0.1.
    assert outcome.results[0].breakdown.lexical == 1.0
    assert outcome.diagnostics.filtered_out == 1


def test_one_project_cannot_reach_another_project_s_context() -> None:
    outcome = hybrid_rank(
        [match(0.99, key="foreign", project_id="sampleco-wallet")],
        [match(50.0, key="foreign", project_id="sampleco-wallet")],
        methods=BOTH,
        now=NOW,
        filters=RetrievalFilters(project_id="sampleco-payment"),
    )

    assert outcome.results == ()
    assert outcome.diagnostics.filtered_out == 1


def test_results_are_bounded_by_the_requested_limit() -> None:
    outcome = hybrid_rank(
        [
            match(0.9 - index / 100, key=f"key-{index}", content=words(30, prefix=f"c{index}"))
            for index in range(8)
        ],
        methods=BOTH,
        now=NOW,
        limit=3,
    )

    assert len(outcome.results) == 3
    # Everything that survived ranking is still counted, so the cut is visible.
    assert outcome.diagnostics.ranked == 8


@pytest.mark.parametrize("limit", [0, 51, True])
def test_a_limit_outside_the_supported_range_is_rejected(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        hybrid_rank(methods=BOTH, now=NOW, limit=limit)


def test_hybrid_retrieval_is_deterministic_for_equivalent_inputs() -> None:
    vector = [match(0.7, key="a"), match(0.7, key="b", content=words(30))]
    lexical = [match(5.0, key="b", content=words(30)), match(5.0, key="a")]

    first = hybrid_rank(vector, lexical, methods=BOTH, now=NOW)
    second = hybrid_rank(vector[::-1], lexical[::-1], methods=BOTH, now=NOW)

    assert [result.candidate.key for result in first.results] == [
        result.candidate.key for result in second.results
    ]
    assert [result.score for result in first.results] == [result.score for result in second.results]


def test_lexical_only_retrieval_reports_the_weights_it_actually_applied() -> None:
    outcome = hybrid_rank(
        [],
        [match(4.0, key="only")],
        methods=("lexical",),
        now=NOW,
    )

    assert outcome.diagnostics.methods == ("lexical",)
    assert outcome.diagnostics.weights["vector"] == 0.0
    assert sum(outcome.diagnostics.weights.values()) == pytest.approx(1.0)


def test_a_superseded_version_loses_to_the_active_one_it_duplicates() -> None:
    shared = words(40)
    outcome = hybrid_rank(
        [
            match(0.8, key="v2", content=shared, updated_at=NOW),
            match(
                0.8,
                key="v1",
                content=shared,
                is_active=False,
                updated_at=NOW - timedelta(days=1),
            ),
        ],
        methods=BOTH,
        now=NOW,
        filters=RetrievalFilters(require_active=False),
    )

    assert [result.candidate.key for result in outcome.results] == ["v2"]
    assert outcome.diagnostics.duplicates_removed == 1


def test_custom_weights_change_the_ranking() -> None:
    lexical_first = DEFAULT_WEIGHTS.__class__(
        vector=0.05,
        lexical=0.90,
        importance=0.0,
        recency=0.0,
        source_priority=0.05,
    )
    matches = (
        [match(1.0, key="vector-strong", content=words(30))],
        [match(9.0, key="lexical-strong", content=words(30, prefix="l"))],
    )

    default = hybrid_rank(*matches, methods=BOTH, now=NOW)
    reweighted = hybrid_rank(*matches, methods=BOTH, now=NOW, weights=lexical_first)

    assert default.results[0].candidate.key == "vector-strong"
    assert reweighted.results[0].candidate.key == "lexical-strong"


def test_retrieving_nothing_still_reports_what_was_searched() -> None:
    outcome = hybrid_rank(methods=BOTH, now=NOW)

    assert outcome.results == ()
    assert outcome.diagnostics.merged == 0
    assert outcome.diagnostics.methods == BOTH
