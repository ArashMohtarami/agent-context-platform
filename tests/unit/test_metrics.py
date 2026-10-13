"""The metrics registry: what it holds, what it refuses, what it renders."""

import pytest

from app.observability.metrics import (
    CONTENT_TYPE,
    MetricError,
    MetricSpec,
    MetricsRegistry,
)

CALLS = "acp_calls_total"
LEVEL = "acp_level"
LATENCY = "acp_latency_seconds"

CALLS_SPEC = MetricSpec(name=CALLS, kind="counter", help="Calls.", labels=("tool",))
LEVEL_SPEC = MetricSpec(name=LEVEL, kind="gauge", help="A level.")
LATENCY_SPEC = MetricSpec(
    name=LATENCY,
    kind="summary",
    help="Seconds one call took.",
    labels=("route",),
)


def build() -> MetricsRegistry:
    """Return a registry holding one of each kind of metric."""
    return MetricsRegistry((CALLS_SPEC, LEVEL_SPEC, LATENCY_SPEC))


def test_a_counter_accumulates_per_label_combination() -> None:
    registry = build()

    registry.increment(CALLS, tool="search")
    registry.increment(CALLS, tool="search")
    registry.increment(CALLS, tool="remember")

    assert registry.value(CALLS, tool="search") == 2
    assert registry.value(CALLS, tool="remember") == 1


def test_a_counter_refuses_to_go_backwards() -> None:
    # Every rate a scraper computes assumes a counter only rises. Accepting a
    # negative step would not lose one observation; it would make the whole
    # series wrong from then on.
    registry = build()

    with pytest.raises(MetricError, match="cannot decrease"):
        registry.increment(CALLS, -1.0, tool="search")


def test_a_gauge_replaces_its_value() -> None:
    registry = build()

    registry.set(LEVEL, 3.0)
    registry.set(LEVEL, 1.0)

    assert registry.value(LEVEL) == 1.0


def test_an_unrecorded_series_reads_as_nothing_rather_than_zero() -> None:
    # Nothing and zero are different answers: one means the code path never
    # ran, the other means it ran and found nothing.
    registry = build()

    assert registry.value(CALLS, tool="search") is None
    assert registry.summary(LATENCY, route="/search") is None


def test_a_summary_keeps_lifetime_totals_and_a_recent_window() -> None:
    registry = MetricsRegistry((LATENCY_SPEC,), window=3)

    for value in (1.0, 2.0, 3.0, 4.0):
        registry.observe(LATENCY, value, route="/search")

    measured = registry.summary(LATENCY, route="/search")
    assert measured is not None
    count, total, recent = measured
    # The count and sum cover every observation; the window holds the last few,
    # which is what makes a quantile describe now rather than an average of
    # everything since startup.
    assert (count, total) == (4, 10.0)
    assert recent == (2.0, 3.0, 4.0)


def test_a_summary_ignores_an_impossible_observation() -> None:
    # One infinite value would make the sum unreadable for the life of the
    # process, and a clock that produced it has already failed at measuring.
    registry = build()

    registry.observe(LATENCY, float("inf"), route="/search")

    assert registry.summary(LATENCY, route="/search") is None


def test_recording_an_undeclared_metric_is_refused() -> None:
    registry = build()

    with pytest.raises(MetricError, match="never declared"):
        registry.increment("acp_invented_total", tool="search")


def test_recording_with_the_wrong_labels_is_refused() -> None:
    # A typo in a label name would otherwise become a second series that looks
    # like the first and never adds up with it.
    registry = build()

    with pytest.raises(MetricError, match="takes tool"):
        registry.increment(CALLS, toool="search")
    with pytest.raises(MetricError, match="takes tool"):
        registry.increment(CALLS, tool="search", extra="no")


def test_recording_against_the_wrong_kind_is_refused() -> None:
    registry = build()

    with pytest.raises(MetricError, match="is a counter, not a summary"):
        registry.observe(CALLS, 1.0, tool="search")
    with pytest.raises(MetricError, match="is a gauge, not a counter"):
        registry.increment(LEVEL)


def test_a_metric_stops_admitting_series_rather_than_growing_forever() -> None:
    # The defence that makes a metrics endpoint safe to expose: whatever goes
    # wrong upstream, a process cannot be made to run out of memory by being
    # given more label values.
    registry = MetricsRegistry((CALLS_SPEC,), max_series=2)

    for index in range(5):
        registry.increment(CALLS, tool=f"tool-{index}")

    assert registry.value(CALLS, tool="tool-0") == 1
    assert registry.value(CALLS, tool="tool-1") == 1
    assert registry.value(CALLS, tool="tool-4") is None
    assert registry.dropped() == {CALLS: 3}


def test_an_admitted_series_keeps_accumulating_at_the_limit() -> None:
    registry = MetricsRegistry((CALLS_SPEC,), max_series=1)

    registry.increment(CALLS, tool="search")
    registry.increment(CALLS, tool="other")
    registry.increment(CALLS, tool="search")

    assert registry.value(CALLS, tool="search") == 2


def test_declaring_a_reserved_label_is_refused() -> None:
    # `quantile` is the label a summary's own output uses, so a metric carrying
    # one would render two different things under the same name.
    with pytest.raises(MetricError, match="reserved"):
        MetricSpec(name="acp_x", kind="summary", help="x", labels=("quantile",))


def test_declaring_an_unusable_name_is_refused() -> None:
    with pytest.raises(MetricError, match="not a valid metric name"):
        MetricSpec(name="acp-calls total", kind="counter", help="x")
    with pytest.raises(MetricError, match="not a valid label name"):
        MetricSpec(name="acp_x", kind="counter", help="x", labels=("a label",))


def test_declaring_one_metric_twice_is_refused() -> None:
    with pytest.raises(MetricError, match="declared twice"):
        MetricsRegistry((CALLS_SPEC, CALLS_SPEC))


def test_rendering_names_every_declared_metric_even_before_it_is_used() -> None:
    # The output is the catalogue of what this build can report, not only of
    # what it happened to do since it started.
    rendered = build().render()

    assert f"# HELP {CALLS} Calls." in rendered
    assert f"# TYPE {CALLS} counter" in rendered
    assert f"# TYPE {LEVEL} gauge" in rendered
    assert f"# TYPE {LATENCY} summary" in rendered


def test_rendering_a_counter_and_a_gauge_reads_as_the_exposition_format() -> None:
    registry = build()
    registry.increment(CALLS, 2.0, tool="search")
    registry.set(LEVEL, 0.5)

    lines = registry.render().splitlines()

    assert 'acp_calls_total{tool="search"} 2' in lines
    assert "acp_level 0.5" in lines
    assert registry.render().endswith("\n")


def test_rendering_a_summary_emits_quantiles_with_a_count_and_a_sum() -> None:
    registry = build()
    for value in (1.0, 2.0, 3.0, 4.0):
        registry.observe(LATENCY, value, route="/search")

    lines = registry.render().splitlines()

    assert 'acp_latency_seconds{route="/search",quantile="0.5"} 2' in lines
    assert 'acp_latency_seconds{route="/search",quantile="0.95"} 4' in lines
    assert 'acp_latency_seconds{route="/search",quantile="0.99"} 4' in lines
    assert 'acp_latency_seconds_count{route="/search"} 4' in lines
    assert 'acp_latency_seconds_sum{route="/search"} 10' in lines


def test_a_reported_quantile_is_a_value_that_was_actually_measured() -> None:
    # Nearest rank rather than interpolation, so a p99 names a request that
    # really did take that long instead of one nobody ever saw.
    registry = build()
    for value in (1.0, 1.0, 1.0, 100.0):
        registry.observe(LATENCY, value, route="/search")

    lines = registry.render().splitlines()

    assert 'acp_latency_seconds{route="/search",quantile="0.5"} 1' in lines
    assert 'acp_latency_seconds{route="/search",quantile="0.99"} 100' in lines


def test_a_label_value_cannot_end_its_own_label() -> None:
    # Label values reach the registry from bounded vocabularies, but the
    # rendering is what a scraper parses, so it escapes rather than trusts.
    registry = build()
    registry.increment(CALLS, tool='search" evil="yes')

    rendered = registry.render()

    assert 'acp_calls_total{tool="search\\" evil=\\"yes"} 1' in rendered


def test_rendering_is_stable_across_calls() -> None:
    # A scrape that reordered itself would look like series appearing and
    # disappearing to anything doing a textual comparison.
    registry = build()
    registry.increment(CALLS, tool="b")
    registry.increment(CALLS, tool="a")

    assert registry.render() == registry.render()
    assert registry.render().index('tool="a"') < registry.render().index('tool="b"')


def test_resetting_forgets_the_numbers_and_keeps_the_declarations() -> None:
    registry = build()
    registry.increment(CALLS, tool="search")

    registry.reset()

    assert registry.value(CALLS, tool="search") is None
    assert f"# TYPE {CALLS} counter" in registry.render()


def test_the_content_type_is_the_one_a_scraper_parses() -> None:
    assert CONTENT_TYPE == "text/plain; version=0.0.4; charset=utf-8"


def test_a_registry_refuses_an_unusable_window_or_series_limit() -> None:
    with pytest.raises(MetricError, match="at least one observation"):
        MetricsRegistry((LATENCY_SPEC,), window=0)
    with pytest.raises(MetricError, match="at least one series"):
        MetricsRegistry((LATENCY_SPEC,), max_series=0)
