"""Retrieval filter tests, including the project isolation rule."""

import pytest

from app.retrieval.filters import MAX_FILTER_TAGS, RetrievalFilters, visible_to_project
from tests.retrieval_doubles import candidate


def test_global_knowledge_is_visible_to_every_request() -> None:
    assert visible_to_project(None, "sampleco-payment") is True
    assert visible_to_project(None, None) is True


def test_project_content_is_visible_to_its_own_project() -> None:
    assert visible_to_project("sampleco-payment", "sampleco-payment") is True


def test_one_project_never_sees_another_project_s_context() -> None:
    assert visible_to_project("sampleco-payment", "sampleco-wallet") is False


def test_an_unscoped_request_sees_only_global_knowledge() -> None:
    assert visible_to_project("sampleco-payment", None) is False


def test_project_scoped_retrieval_returns_global_and_own_content_only() -> None:
    filters = RetrievalFilters(project_id="sampleco-payment")
    candidates = [
        candidate("global", project_id=None),
        candidate("own", project_id="sampleco-payment"),
        candidate("foreign", project_id="sampleco-wallet"),
    ]

    assert [item.key for item in filters.apply(candidates)] == ["global", "own"]


def test_a_forbidden_candidate_is_rejected_whatever_else_matches() -> None:
    filters = RetrievalFilters(project_id="sampleco-wallet", category="backend")
    leaked = candidate("foreign", project_id="sampleco-payment", category="backend")

    assert filters.allows(leaked) is False


def test_no_filters_allow_every_global_candidate() -> None:
    assert RetrievalFilters().allows(candidate()) is True


def test_sources_restrict_which_kinds_of_content_come_back() -> None:
    filters = RetrievalFilters(sources=("memory",))

    assert filters.allows(candidate("remembered", source="memory")) is True
    assert filters.allows(candidate("skill", source="active_skill")) is False


def test_superseded_versions_are_excluded_by_default() -> None:
    superseded = candidate("old", is_active=False)

    assert RetrievalFilters().allows(superseded) is False
    assert RetrievalFilters(require_active=False).allows(superseded) is True


def test_category_must_match_exactly() -> None:
    filters = RetrievalFilters(category="security")

    assert filters.allows(candidate(category="security")) is True
    assert filters.allows(candidate(category="backend")) is False
    assert filters.allows(candidate(category=None)) is False


def test_every_requested_tag_must_be_present() -> None:
    filters = RetrievalFilters(tags=("python", "fastapi"))

    assert filters.allows(candidate(tags=("python", "fastapi", "async"))) is True
    assert filters.allows(candidate(tags=("python",))) is False


def test_trust_level_must_match_exactly() -> None:
    filters = RetrievalFilters(trust_level="trusted")

    assert filters.allows(candidate(trust_level="trusted")) is True
    assert filters.allows(candidate(trust_level="external")) is False


@pytest.mark.parametrize(
    ("minimum", "importance", "allowed"),
    [(0.0, 0.0, True), (0.5, 0.5, True), (0.5, 0.49, False), (0.9, 1.0, True)],
)
def test_minimum_importance_is_an_inclusive_floor(
    minimum: float,
    importance: float,
    allowed: bool,
) -> None:
    filters = RetrievalFilters(minimum_importance=minimum)

    assert filters.allows(candidate(importance=importance)) is allowed


@pytest.mark.parametrize("minimum", [-0.1, 1.1, float("nan")])
def test_an_unusable_importance_floor_is_rejected(minimum: float) -> None:
    with pytest.raises(ValueError, match="minimum_importance"):
        RetrievalFilters(minimum_importance=minimum)


def test_repeated_tag_filters_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        RetrievalFilters(tags=("python", "python"))


def test_an_unbounded_tag_filter_is_rejected() -> None:
    with pytest.raises(ValueError, match="tag filters"):
        RetrievalFilters(tags=tuple(f"tag-{index}" for index in range(MAX_FILTER_TAGS + 1)))


def test_filtering_preserves_the_order_it_was_given() -> None:
    filters = RetrievalFilters(category="backend")
    candidates = [candidate("c"), candidate("a"), candidate("b")]

    assert [item.key for item in filters.apply(candidates)] == ["c", "a", "b"]


def test_every_restriction_applies_together() -> None:
    filters = RetrievalFilters(
        project_id="sampleco-payment",
        sources=("active_skill",),
        category="backend",
        tags=("python",),
        trust_level="trusted",
        minimum_importance=0.4,
    )

    assert filters.allows(candidate()) is True
    assert filters.allows(candidate(trust_level="external")) is False
