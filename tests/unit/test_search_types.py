"""Query normalization, source selection, and the retrieval trace."""

import pytest

from app.retrieval.query import normalize_query
from app.retrieval.types import RETRIEVABLE_SOURCES, SourceMatches
from app.search.types import RetrievalTrace, resolve_sources, retrieval_method


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("  payment   idempotency  ", "payment idempotency"),
        ("payment\n\tidempotency", "payment idempotency"),
        ("wallet:read", "wallet:read"),
        ("/api/v1/payments", "/api/v1/payments"),
        ("INSUFFICIENT_SCOPE", "INSUFFICIENT_SCOPE"),
        ("payment-service.Client", "payment-service.Client"),
    ],
)
def test_normalization_collapses_whitespace_and_nothing_else(query: str, expected: str) -> None:
    assert normalize_query(query) == expected


@pytest.mark.parametrize("query", ["", "   ", "\n\t", "  \r\n "])
def test_a_query_of_only_whitespace_is_rejected(query: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_query(query)


def test_normalization_is_idempotent() -> None:
    once = normalize_query("  How do we    prevent duplicate payments? ")

    assert normalize_query(once) == once


def test_naming_no_source_searches_everything_retrievable() -> None:
    assert resolve_sources(None) == RETRIEVABLE_SOURCES


def test_sources_are_deduplicated_and_ordered_so_equivalent_requests_match() -> None:
    one = resolve_sources(["memory", "active_skill", "memory"])
    other = resolve_sources(["active_skill", "memory"])

    assert one == other == ("active_skill", "memory")


def test_an_empty_source_list_is_rejected_rather_than_treated_as_everything() -> None:
    with pytest.raises(ValueError, match="at least one source"):
        resolve_sources([])


def test_a_conversation_can_be_asked_for_even_though_nothing_retrieves_one() -> None:
    assert resolve_sources(["conversation"]) == ("conversation",)


@pytest.mark.parametrize(
    ("methods", "expected"),
    [
        (("vector", "lexical"), "hybrid"),
        (("lexical", "vector"), "hybrid"),
        (("vector",), "vector"),
        (("lexical",), "lexical"),
    ],
)
def test_the_retrieval_method_names_what_actually_ran(
    methods: tuple[str, ...],
    expected: str,
) -> None:
    assert retrieval_method(methods) == expected  # type: ignore[arg-type]


def test_a_search_that_ran_nothing_cannot_be_named() -> None:
    with pytest.raises(ValueError, match="at least one retrieval method"):
        retrieval_method(())


def test_gathered_matches_name_the_sources_a_query_ran_against() -> None:
    gathered = SourceMatches(searched=("active_skill", "memory"))

    assert gathered.vector == ()
    assert gathered.lexical == ()
    assert gathered.searched == ("active_skill", "memory")


def test_a_trace_requires_an_identifier_and_a_non_negative_latency() -> None:
    with pytest.raises(ValueError, match="query identifier"):
        trace("")
    with pytest.raises(ValueError, match="latency"):
        trace("abc", latency_ms=-1.0)


def test_a_trace_reports_itself_without_the_query_text() -> None:
    reported = trace("abc").as_mapping()

    assert reported["query_id"] == "abc"
    assert reported["retrieval_method"] == "hybrid"
    assert reported["searched_sources"] == ["active_skill"]
    assert "query" not in reported


def trace(query_id: str, *, latency_ms: float = 1.5) -> RetrievalTrace:
    """Build one retrieval trace."""
    return RetrievalTrace(
        query_id=query_id,
        retrieval_method="hybrid",
        requested_sources=("active_skill", "memory"),
        searched_sources=("active_skill",),
        skipped_sources=("memory",),
        latency_ms=latency_ms,
    )
