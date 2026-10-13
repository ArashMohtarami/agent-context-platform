"""Hybrid ranking, score normalization, and deterministic ordering tests."""

from datetime import timedelta

import pytest

from app.retrieval.ranking import (
    DEFAULT_WEIGHTS,
    SOURCE_PRIORITY,
    RankingWeights,
    normalize_bounded,
    normalize_relative,
    rank_candidates,
    recency_score,
    source_priority,
)
from app.retrieval.types import ScoredCandidate
from tests.retrieval_doubles import NOW, candidate

HALF_LIFE = 90.0


def scored(
    key: str,
    *,
    vector: float | None = None,
    lexical: float | None = None,
    **overrides: object,
) -> ScoredCandidate:
    """Build one merged candidate carrying raw method scores."""
    return ScoredCandidate(
        candidate=candidate(key, **overrides),  # type: ignore[arg-type]
        vector_score=vector,
        lexical_score=lexical,
    )


def test_the_default_weights_are_the_documented_starting_formula() -> None:
    assert (DEFAULT_WEIGHTS.vector, DEFAULT_WEIGHTS.lexical) == (0.45, 0.25)
    assert (DEFAULT_WEIGHTS.importance, DEFAULT_WEIGHTS.recency) == (0.15, 0.10)
    assert DEFAULT_WEIGHTS.source_priority == 0.05
    assert DEFAULT_WEIGHTS.total == pytest.approx(1.0)


@pytest.mark.parametrize(
    "override",
    [
        {"vector": -0.1},
        {"lexical": float("nan")},
        {"importance": float("inf")},
        {"recency": True},
    ],
)
def test_unusable_weights_are_rejected(override: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="weight"):
        RankingWeights(**override)  # type: ignore[arg-type]


def test_a_weighting_with_no_signal_is_rejected() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        RankingWeights(vector=0.0, lexical=0.0, importance=0.0, recency=0.0, source_priority=0.0)


def test_weights_are_rescaled_without_changing_their_proportions() -> None:
    normalized = RankingWeights(
        vector=9.0,
        lexical=5.0,
        importance=3.0,
        recency=2.0,
        source_priority=1.0,
    ).normalized()

    assert normalized.total == pytest.approx(1.0)
    assert normalized.vector == pytest.approx(0.45)
    assert normalized.lexical == pytest.approx(0.25)


def test_a_search_that_did_not_run_has_its_weight_redistributed() -> None:
    lexical_only = DEFAULT_WEIGHTS.for_methods(("lexical",))

    assert lexical_only.vector == 0.0
    assert lexical_only.total == pytest.approx(1.0)
    # The remaining signals keep their proportions to one another.
    assert lexical_only.lexical == pytest.approx(0.25 / 0.55)
    assert lexical_only.importance == pytest.approx(0.15 / 0.55)


def test_running_both_searches_applies_the_configured_weights() -> None:
    applied = DEFAULT_WEIGHTS.for_methods(("vector", "lexical"))

    assert applied.as_mapping() == {
        "vector": pytest.approx(0.45),
        "lexical": pytest.approx(0.25),
        "importance": pytest.approx(0.15),
        "recency": pytest.approx(0.10),
        "source_priority": pytest.approx(0.05),
    }


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        ((0.0, 0.5, 1.0), (0.0, 0.5, 1.0)),
        ((-0.4, 1.7), (0.0, 1.0)),
        ((), ()),
    ],
)
def test_similarity_scores_keep_their_own_scale(
    scores: tuple[float, ...],
    expected: tuple[float, ...],
) -> None:
    assert normalize_bounded(scores) == expected


def test_unbounded_relevance_is_scaled_against_the_best_result() -> None:
    assert normalize_relative((10.0, 5.0, 2.5)) == (1.0, 0.5, 0.25)


def test_relative_scaling_keeps_the_weakest_result_above_zero() -> None:
    normalized = normalize_relative((4.0, 3.9))

    # Min-max scaling would report the second result as a total mismatch.
    assert normalized[1] == pytest.approx(0.975)


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        ((), ()),
        ((7.5,), (1.0,)),
        ((0.0, 0.0), (0.0, 0.0)),
        ((-2.0, -1.0), (0.0, 0.0)),
    ],
)
def test_relative_scaling_handles_degenerate_result_sets(
    scores: tuple[float, ...],
    expected: tuple[float, ...],
) -> None:
    assert normalize_relative(scores) == expected


@pytest.mark.parametrize("score", [float("nan"), float("inf"), True])
def test_a_score_that_is_not_a_finite_number_is_refused(score: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        normalize_relative([score])


def test_content_written_now_is_fully_recent() -> None:
    assert recency_score(NOW, now=NOW, half_life_days=HALF_LIFE) == 1.0


@pytest.mark.parametrize(("age_days", "expected"), [(90.0, 0.5), (180.0, 0.25), (270.0, 0.125)])
def test_recency_halves_every_half_life(age_days: float, expected: float) -> None:
    written = NOW - timedelta(days=age_days)

    assert recency_score(written, now=NOW, half_life_days=HALF_LIFE) == pytest.approx(expected)


def test_a_timestamp_ahead_of_the_clock_is_not_scored_above_one() -> None:
    ahead = NOW + timedelta(days=5)

    assert recency_score(ahead, now=NOW, half_life_days=HALF_LIFE) == 1.0


def test_recency_requires_comparable_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        recency_score(NOW.replace(tzinfo=None), now=NOW)


@pytest.mark.parametrize("half_life", [0.0, -1.0, float("nan")])
def test_an_unusable_half_life_is_rejected(half_life: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        recency_score(NOW, now=NOW, half_life_days=half_life)


def test_project_knowledge_outranks_generic_knowledge_by_source() -> None:
    assert source_priority("project_context") == 1.00
    assert source_priority("active_skill") == 0.95
    assert source_priority("memory") == 0.80
    assert source_priority("conversation") == 0.50
    assert sorted(SOURCE_PRIORITY, key=SOURCE_PRIORITY.__getitem__, reverse=True)[0] == (
        "project_context"
    )


def test_the_final_score_combines_every_weighted_signal() -> None:
    ranked = rank_candidates(
        [scored("a", vector=1.0, lexical=10.0), scored("b", vector=0.5, lexical=5.0)],
        methods=("vector", "lexical"),
        now=NOW,
    )

    # 0.45*1.0 + 0.25*1.0 + 0.15*0.5 + 0.10*1.0 + 0.05*0.95
    assert ranked[0].score == pytest.approx(0.9225)
    # 0.45*0.5 + 0.25*0.5 + 0.15*0.5 + 0.10*1.0 + 0.05*0.95
    assert ranked[1].score == pytest.approx(0.5725)


def test_a_result_only_one_search_found_contributes_nothing_from_the_other() -> None:
    ranked = rank_candidates(
        [scored("lexical-only", lexical=10.0)],
        methods=("vector", "lexical"),
        now=NOW,
    )

    assert ranked[0].breakdown.vector == 0.0
    assert ranked[0].breakdown.lexical == 1.0
    assert ranked[0].breakdown.methods == ("lexical",)
    assert ranked[0].breakdown.raw_vector is None
    assert ranked[0].breakdown.raw_lexical == 10.0


def test_agreement_between_both_searches_outranks_a_single_strong_hit() -> None:
    ranked = rank_candidates(
        [
            scored("vector-only", vector=1.0),
            scored("both", vector=0.9, lexical=10.0),
        ],
        methods=("vector", "lexical"),
        now=NOW,
    )

    assert [result.candidate.key for result in ranked] == ["both", "vector-only"]


def test_importance_separates_otherwise_identical_results() -> None:
    ranked = rank_candidates(
        [
            scored("unrated", vector=0.8, lexical=5.0, importance=0.1),
            scored("curated", vector=0.8, lexical=5.0, importance=0.9),
        ],
        methods=("vector", "lexical"),
        now=NOW,
    )

    assert [result.candidate.key for result in ranked] == ["curated", "unrated"]


def test_recency_separates_otherwise_identical_results() -> None:
    ranked = rank_candidates(
        [
            scored("stale", vector=0.8, lexical=5.0, updated_at=NOW - timedelta(days=720)),
            scored("fresh", vector=0.8, lexical=5.0, updated_at=NOW),
        ],
        methods=("vector", "lexical"),
        now=NOW,
    )

    assert [result.candidate.key for result in ranked] == ["fresh", "stale"]


def test_source_priority_separates_otherwise_identical_results() -> None:
    ranked = rank_candidates(
        [
            scored("chat", vector=0.8, lexical=5.0, source="conversation"),
            scored("context", vector=0.8, lexical=5.0, source="project_context"),
            scored("remembered", vector=0.8, lexical=5.0, source="memory"),
        ],
        methods=("vector", "lexical"),
        now=NOW,
    )

    assert [result.candidate.key for result in ranked] == ["context", "remembered", "chat"]


def test_an_active_version_wins_a_tie_against_a_superseded_one() -> None:
    ranked = rank_candidates(
        [
            scored("superseded", vector=0.8, lexical=5.0, is_active=False),
            scored("active", vector=0.8, lexical=5.0, is_active=True),
        ],
        methods=("vector", "lexical"),
        now=NOW,
    )

    assert [result.candidate.key for result in ranked] == ["active", "superseded"]


def test_newer_content_wins_a_tie_against_older_content() -> None:
    older = NOW - timedelta(seconds=1)
    ranked = rank_candidates(
        [
            scored("older", vector=0.8, lexical=5.0, updated_at=older),
            scored("newer", vector=0.8, lexical=5.0, updated_at=NOW),
        ],
        methods=("vector", "lexical"),
        now=NOW,
    )

    assert [result.candidate.key for result in ranked] == ["newer", "older"]


def test_ranking_does_not_depend_on_the_order_candidates_arrive_in() -> None:
    candidates = [
        scored("a", vector=0.4, lexical=8.0),
        scored("b", vector=0.9, lexical=1.0),
        scored("c", vector=0.7, lexical=4.0),
        scored("d", vector=0.7, lexical=4.0),
    ]

    forwards = rank_candidates(candidates, methods=("vector", "lexical"), now=NOW)
    backwards = rank_candidates(candidates[::-1], methods=("vector", "lexical"), now=NOW)

    assert [result.candidate.key for result in forwards] == [
        result.candidate.key for result in backwards
    ]
    assert [result.score for result in forwards] == [result.score for result in backwards]


def test_repeating_a_ranking_produces_identical_scores() -> None:
    candidates = [scored("a", vector=0.31, lexical=7.7), scored("b", vector=0.29, lexical=9.1)]

    first = rank_candidates(candidates, methods=("vector", "lexical"), now=NOW)
    second = rank_candidates(candidates, methods=("vector", "lexical"), now=NOW)

    assert first == second


def test_lexical_only_retrieval_is_not_penalised_for_the_missing_vector() -> None:
    ranked = rank_candidates(
        [scored("only", lexical=6.0)],
        methods=("lexical",),
        now=NOW,
    )

    # 0.25*1.0 + 0.15*0.5 + 0.10*1.0 + 0.05*0.95 over a total weight of 0.55.
    assert ranked[0].score == pytest.approx((0.25 + 0.075 + 0.10 + 0.0475) / 0.55)
    assert ranked[0].score > 0.5


def test_ranking_nothing_returns_nothing() -> None:
    assert rank_candidates([], methods=("vector", "lexical"), now=NOW) == ()


def test_lexical_relevance_is_scaled_within_each_source() -> None:
    # 40.0 from one index and 4.0 from another are each the best of their own
    # search, so both normalize to 1.0 rather than one burying the other.
    ranked = rank_candidates(
        [
            scored("skill", lexical=40.0, source="active_skill"),
            scored("context", lexical=4.0, source="project_context"),
            scored("weaker-skill", lexical=20.0, source="active_skill"),
        ],
        methods=("vector", "lexical"),
        now=NOW,
    )
    by_key = {result.candidate.key: result.breakdown.lexical for result in ranked}

    assert by_key["skill"] == 1.0
    assert by_key["context"] == 1.0
    assert by_key["weaker-skill"] == 0.5


def test_scaling_lexical_scores_together_would_have_buried_the_smaller_source() -> None:
    ranked = rank_candidates(
        [
            scored("skill", lexical=40.0, source="active_skill"),
            scored("context", lexical=4.0, source="project_context"),
        ],
        methods=("vector", "lexical"),
        now=NOW,
    )
    context_result = next(result for result in ranked if result.candidate.key == "context")

    # A single global scaling would have made this 0.1 for no defensible reason.
    assert context_result.breakdown.lexical == 1.0
