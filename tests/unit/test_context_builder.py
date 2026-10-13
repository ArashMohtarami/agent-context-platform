"""Token budgeting and compact context assembly tests."""

import pytest

from app.retrieval.context_builder import (
    DEFAULT_TOKEN_BUDGET,
    MAX_TOKEN_BUDGET,
    MIN_TOKEN_BUDGET,
    build_context,
    resolve_token_budget,
)
from app.retrieval.ranking import DEFAULT_WEIGHTS, rank_candidates
from app.retrieval.types import RankedResult, RetrievalDiagnostics, ScoredCandidate
from tests.retrieval_doubles import NOW, candidate

DIAGNOSTICS = RetrievalDiagnostics(
    methods=("vector", "lexical"),
    weights=DEFAULT_WEIGHTS.as_mapping(),
    vector_matches=3,
    lexical_matches=3,
    merged=3,
    ranked=3,
)


def results(*sizes: int, **overrides: object) -> tuple[RankedResult, ...]:
    """Rank one candidate per requested token size, largest score first."""
    scored = [
        ScoredCandidate(
            candidate=candidate(  # type: ignore[arg-type]
                f"chunk-{index}",
                tokens=size,
                content=f"content {index}",
                **overrides,
            ),
            vector_score=1.0 - index / 100,
        )
        for index, size in enumerate(sizes)
    ]
    return rank_candidates(scored, methods=("vector", "lexical"), now=NOW)


def test_the_default_budget_is_about_four_thousand_tokens() -> None:
    assert DEFAULT_TOKEN_BUDGET == 4_000


def test_everything_within_the_budget_is_returned() -> None:
    context = build_context("q", results(600, 600, 600), diagnostics=DIAGNOSTICS)

    assert len(context.items) == 3
    assert context.token_count == 1_800
    assert context.token_budget == DEFAULT_TOKEN_BUDGET
    assert context.diagnostics.dropped_for_budget == 0


def test_the_budget_cuts_the_results_that_do_not_fit() -> None:
    context = build_context("q", results(400, 400, 400), diagnostics=DIAGNOSTICS, token_budget=900)

    assert [item.tokens for item in context.items] == [400, 400]
    assert context.token_count == 800
    assert context.token_count <= context.token_budget
    assert context.diagnostics.dropped_for_budget == 1


def test_a_result_too_large_to_fit_does_not_waste_the_remaining_budget() -> None:
    context = build_context("q", results(300, 900, 200), diagnostics=DIAGNOSTICS, token_budget=600)

    # The oversized second result is skipped, the smaller third still fits.
    assert [item.tokens for item in context.items] == [300, 200]
    assert context.diagnostics.dropped_for_budget == 1


def test_results_stay_in_rank_order_inside_the_budget() -> None:
    context = build_context("q", results(100, 100, 100), diagnostics=DIAGNOSTICS)

    assert [item.title for item in context.items] == ["FastAPI Production: Rules"] * 3
    assert [item.score for item in context.items] == sorted(
        (item.score for item in context.items), reverse=True
    )


def test_a_budget_no_result_fits_returns_nothing_rather_than_half_a_rule() -> None:
    context = build_context("q", results(900, 800), diagnostics=DIAGNOSTICS, token_budget=100)

    assert context.items == ()
    assert context.token_count == 0
    assert context.diagnostics.dropped_for_budget == 2


def test_retrieving_nothing_produces_an_empty_context() -> None:
    context = build_context("q", (), diagnostics=DIAGNOSTICS, project_id="sampleco-payment")

    assert context.items == ()
    assert context.token_count == 0
    assert context.project_id == "sampleco-payment"
    assert context.query == "q"


def test_every_item_preserves_where_its_content_came_from() -> None:
    context = build_context(
        "q",
        results(100, source="memory", metadata={"skill_id": "fastapi-production"}),
        diagnostics=DIAGNOSTICS,
    )
    item = context.items[0]

    assert item.source == "memory"
    assert item.title == "FastAPI Production: Rules"
    assert item.content == "content 0"
    assert item.metadata == {"skill_id": "fastapi-production"}


def test_every_item_carries_the_reasoning_behind_its_score() -> None:
    context = build_context("q", results(100), diagnostics=DIAGNOSTICS)
    explanation = context.items[0].explanation

    assert explanation.final == context.items[0].score
    assert explanation.vector == 1.0
    assert explanation.source_priority == 0.95
    assert explanation.methods == ("vector",)


def test_the_diagnostics_record_the_budget_that_was_applied() -> None:
    context = build_context("q", results(400, 400), diagnostics=DIAGNOSTICS, token_budget=500)

    assert context.diagnostics.token_budget == 500
    assert context.diagnostics.merged == 3
    assert context.diagnostics.weights == DEFAULT_WEIGHTS.as_mapping()


@pytest.mark.parametrize("budget", [0, -1, MIN_TOKEN_BUDGET - 1, True])
def test_an_unusable_budget_is_rejected(budget: int) -> None:
    with pytest.raises(ValueError, match="token budget"):
        build_context("q", (), diagnostics=DIAGNOSTICS, token_budget=budget)


def test_an_unspecified_budget_falls_back_to_the_configured_default() -> None:
    assert resolve_token_budget(None, default=2_000, maximum=8_000) == 2_000


def test_a_smaller_budget_than_the_default_is_honoured() -> None:
    assert resolve_token_budget(500, default=4_000, maximum=8_000) == 500


def test_a_request_can_never_exceed_the_configured_maximum() -> None:
    assert resolve_token_budget(999_999, default=4_000, maximum=8_000) == 8_000


@pytest.mark.parametrize("requested", [0, 99, -5, True])
def test_a_budget_below_the_supported_minimum_is_rejected(requested: int) -> None:
    with pytest.raises(ValueError, match="token budget"):
        resolve_token_budget(requested, default=4_000, maximum=8_000)


@pytest.mark.parametrize(
    ("default", "maximum"),
    [(8_001, 8_000), (4_000, MAX_TOKEN_BUDGET + 1), (4_000, 10), (50, 4_000)],
)
def test_an_incoherent_budget_configuration_is_rejected(default: int, maximum: int) -> None:
    with pytest.raises(ValueError, match="budget"):
        resolve_token_budget(None, default=default, maximum=maximum)
