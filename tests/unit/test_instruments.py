"""The metric catalogue: that it covers what was asked for, and nothing else."""

from collections.abc import Iterator

import pytest

from app.observability import instruments
from app.observability.instruments import REGISTRY, SPECS
from app.observability.metrics import MetricSpec

# What Phase 17 requires this platform to be able to report, named as the
# requirement names it. Written out rather than derived from `SPECS`, so a
# metric that is deleted or renamed fails here instead of quietly disappearing
# from whatever dashboard was built on it.
REQUIRED: dict[str, str] = {
    "API requests": instruments.HTTP_REQUESTS,
    "request latency": instruments.HTTP_DURATION,
    "MongoDB latency": instruments.MONGODB_DURATION,
    "embedding latency": instruments.EMBEDDING_DURATION,
    "retrieval latency": instruments.RETRIEVAL_DURATION,
    "search failures": instruments.SEARCH_FAILURES,
    "MCP calls": instruments.MCP_CALLS,
    "memory operations": instruments.MEMORY_OPERATIONS,
    "skill imports": instruments.SKILL_IMPORTS,
}


@pytest.fixture(autouse=True)
def empty_registry() -> Iterator[None]:
    """Record into a registry no other test has already written to."""
    REGISTRY.reset()
    yield
    REGISTRY.reset()


def spec(name: str) -> MetricSpec:
    """Return one declared metric."""
    return next(declared for declared in SPECS if declared.name == name)


def test_every_required_metric_is_declared() -> None:
    declared = {declared.name for declared in SPECS}

    for requirement, name in REQUIRED.items():
        assert name in declared, requirement


def test_latency_is_reported_at_the_three_quantiles_that_were_asked_for() -> None:
    from app.observability.metrics import QUANTILES

    assert QUANTILES == (0.5, 0.95, 0.99)


def test_vector_and_lexical_failures_are_countable_apart() -> None:
    # They fail apart: a rebuilt vector index takes semantic search down while
    # lexical search keeps answering. One number covering both would go red for
    # a half-outage and say nothing about which half.
    instruments.record_search(method="vector", collection="memories", seconds=0.1, failed=True)
    instruments.record_search(method="lexical", collection="memories", seconds=0.1)

    assert REGISTRY.value(instruments.SEARCH_FAILURES, method="vector", collection="memories") == 1
    assert (
        REGISTRY.value(instruments.SEARCH_FAILURES, method="lexical", collection="memories") is None
    )


def test_a_successful_operation_is_timed_without_being_counted_as_a_failure() -> None:
    instruments.record_embedding(operation="query", seconds=0.25)

    measured = REGISTRY.summary(instruments.EMBEDDING_DURATION, operation="query")
    assert measured is not None and measured[0] == 1
    assert REGISTRY.value(instruments.EMBEDDING_FAILURES, operation="query") is None


def test_a_failed_operation_is_timed_as_well_as_counted() -> None:
    # A slow failure and an immediate one are different problems, and the
    # duration is what tells a loaded model apart from a missing one.
    instruments.record_embedding(operation="query", seconds=9.0, failed=True)

    measured = REGISTRY.summary(instruments.EMBEDDING_DURATION, operation="query")
    assert measured is not None and measured[2] == (9.0,)
    assert REGISTRY.value(instruments.EMBEDDING_FAILURES, operation="query") == 1


def test_a_retrieval_records_both_how_long_it_took_and_how_much_it_found() -> None:
    instruments.record_retrieval(operation="search_knowledge", seconds=0.4, result_count=7)

    duration = REGISTRY.summary(instruments.RETRIEVAL_DURATION, operation="search_knowledge")
    results = REGISTRY.summary(instruments.RETRIEVAL_RESULTS, operation="search_knowledge")
    assert duration is not None and duration[2] == (0.4,)
    assert results is not None and results[2] == (7.0,)


def test_an_unrecognized_request_method_is_not_given_a_series_of_its_own() -> None:
    # A method arrives on the request line, so it is the one HTTP label a
    # caller chooses. Anything unknown is folded into `other`.
    instruments.record_http_request(method="BREW", route="/search", status_code=405, seconds=0.01)

    assert (
        REGISTRY.value(instruments.HTTP_REQUESTS, method="other", route="/search", status="405")
        == 1
    )


def test_a_known_request_method_is_recorded_as_itself() -> None:
    instruments.record_http_request(method="post", route="/search", status_code=200, seconds=0.01)

    assert (
        REGISTRY.value(instruments.HTTP_REQUESTS, method="POST", route="/search", status="200") == 1
    )


def test_importing_nothing_records_nothing() -> None:
    instruments.record_skill_import(outcome="new", count=0)

    assert REGISTRY.value(instruments.SKILL_IMPORTS, outcome="new") is None


def test_readiness_is_published_as_a_number_an_alert_can_fire_on() -> None:
    instruments.record_readiness(dependency="mongot", ready=False)
    instruments.record_readiness(dependency="mongodb", ready=True)

    assert REGISTRY.value(instruments.DEPENDENCY_READY, dependency="mongot") == 0.0
    assert REGISTRY.value(instruments.DEPENDENCY_READY, dependency="mongodb") == 1.0


def test_a_dependency_that_recovers_stops_reading_as_failing() -> None:
    instruments.record_readiness(dependency="mongot", ready=False)
    instruments.record_readiness(dependency="mongot", ready=True)

    assert REGISTRY.value(instruments.DEPENDENCY_READY, dependency="mongot") == 1.0


def test_the_snapshot_says_how_long_this_process_has_been_up() -> None:
    rendered = instruments.snapshot()

    assert f"# TYPE {instruments.UPTIME} gauge" in rendered
    assert REGISTRY.value(instruments.UPTIME) is not None
    assert instruments.uptime_seconds() >= 0.0


def test_the_build_is_published_as_a_label_on_a_constant() -> None:
    instruments.record_build_info("9.9.9")

    assert REGISTRY.value(instruments.BUILD_INFO, version="9.9.9") == 1.0


def test_no_metric_carries_a_project_a_query_or_a_document() -> None:
    # Metrics are kept for a long time and read by more people than a log, so
    # a series may say how much and how long, never about whom or about what.
    forbidden = {
        "project",
        "project_id",
        "query",
        "query_id",
        "content",
        "document",
        "document_id",
        "memory_id",
        "skill_id",
        "key",
        "key_id",
        "token",
        "user",
        "path",
    }

    for declared in SPECS:
        assert not forbidden.intersection(declared.labels), declared.name


def test_every_declared_metric_explains_itself() -> None:
    for declared in SPECS:
        assert declared.help.endswith("."), declared.name
        assert len(declared.help) > 20, declared.name


def test_every_declared_metric_is_named_for_this_platform() -> None:
    # One prefix means a scraper's metric namespace stays sorted together and
    # cannot collide with anything else exporting from the same host.
    for declared in SPECS:
        assert declared.name.startswith("acp_"), declared.name


def test_a_counter_is_named_as_a_total_and_a_duration_in_seconds() -> None:
    for declared in SPECS:
        if declared.kind == "counter":
            assert declared.name.endswith("_total"), declared.name
        if "duration" in declared.name:
            assert declared.name.endswith("_seconds"), declared.name
