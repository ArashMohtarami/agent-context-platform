"""The MongoDB command listener: what it measures, and what it never reads."""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

from app.observability import instruments
from app.observability.instruments import REGISTRY
from app.observability.mongo import CommandMetricsListener


@pytest.fixture(autouse=True)
def empty_registry() -> Iterator[None]:
    """Record into a registry no other test has already written to."""
    REGISTRY.reset()
    yield
    REGISTRY.reset()


def event(command_name: str, duration_micros: int, **extra: object) -> Any:
    """Build one driver event carrying only what a listener may read."""
    return SimpleNamespace(
        command_name=command_name,
        duration_micros=duration_micros,
        **extra,
    )


def test_a_successful_command_is_timed() -> None:
    CommandMetricsListener().succeeded(event("aggregate", 12_500))

    measured = REGISTRY.summary(instruments.MONGODB_DURATION, command="aggregate")
    assert measured is not None
    count, _, recent = measured
    assert count == 1
    assert recent == (0.0125,)


def test_a_failed_command_is_timed_and_counted() -> None:
    CommandMetricsListener().failed(event("find", 3_000, failure={"errmsg": "nope"}))

    assert REGISTRY.value(instruments.MONGODB_FAILURES, command="find") == 1
    measured = REGISTRY.summary(instruments.MONGODB_DURATION, command="find")
    assert measured is not None and measured[0] == 1


def test_a_successful_command_is_not_counted_as_a_failure() -> None:
    CommandMetricsListener().succeeded(event("insert", 1_000))

    assert REGISTRY.value(instruments.MONGODB_FAILURES, command="insert") is None


def test_each_command_name_is_measured_separately() -> None:
    listener = CommandMetricsListener()

    listener.succeeded(event("aggregate", 20_000))
    listener.succeeded(event("find", 1_000))

    assert REGISTRY.summary(instruments.MONGODB_DURATION, command="aggregate") is not None
    assert REGISTRY.summary(instruments.MONGODB_DURATION, command="find") is not None


def test_the_command_document_is_never_read() -> None:
    # A started event carries the filter, the pipeline, and the documents being
    # written. Reading it would turn a latency measurement into a second copy
    # of the database, so the attribute is never touched at all.
    class Forbidden:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"the listener read {name} from a started event")

    CommandMetricsListener().started(cast(Any, Forbidden()))


def test_a_failure_is_logged_without_the_servers_error_reply() -> None:
    # The reply can quote the operation that failed. What the operator needs
    # here is that a command failed and how long it took; the application error
    # raised by the repository is what carries the explanation.
    with patch("app.observability.mongo.logger") as logger:
        CommandMetricsListener().failed(
            event("aggregate", 5_000, failure={"errmsg": "PlanExecutor error: secret query"})
        )

    logged = logger.warning.call_args
    assert logged.args == ("mongodb_command_failed",)
    assert logged.kwargs["extra"] == {"db_command": "aggregate", "duration_ms": 5.0}


def test_a_measurement_that_goes_wrong_never_becomes_a_database_failure() -> None:
    # This runs inside the driver, so an exception here would surface as the
    # query failing rather than as the measurement problem it actually is.
    with patch(
        "app.observability.mongo.record_mongodb_command",
        side_effect=RuntimeError("the registry broke"),
    ):
        CommandMetricsListener().succeeded(event("find", 1_000))
