"""The correlation identifier: what is carried, and what is refused."""

import asyncio

from app.observability import context


def test_a_call_gets_an_identifier_without_one_being_supplied() -> None:
    with context.correlated() as call:
        assert context.request_id() == call.request_id
        assert len(call.request_id) == 32


def test_an_acceptable_identifier_from_a_caller_is_adopted() -> None:
    # This is what makes one MCP tool call and the API request it became read
    # as a single operation across two processes' logs.
    with context.correlated("7f3a9c2e-1b4d-4a6f-8e01-2c5b7d9f0a13") as call:
        assert call.request_id == "7f3a9c2e-1b4d-4a6f-8e01-2c5b7d9f0a13"


def test_an_unacceptable_identifier_is_replaced_rather_than_carried() -> None:
    # The identifier is repeated on every line the call produces, so anything
    # that could be mistaken for structure by whatever reads those lines is
    # refused rather than sanitized.
    for supplied in (
        'evil" injected="yes',
        "with a space",
        "line\nbreak",
        "x" * (context.MAX_ID_LENGTH + 1),
        "",
        "   ",
    ):
        with context.correlated(supplied) as call:
            assert call.request_id != supplied
            assert len(call.request_id) == 32


def test_accepting_reports_what_it_would_carry() -> None:
    assert context.accepted("abc-123_4.5:6") == "abc-123_4.5:6"
    assert context.accepted("  padded  ") == "padded"
    assert context.accepted(None) is None
    assert context.accepted("has space") is None


def test_a_call_collects_what_it_turns_out_to_be_about() -> None:
    with context.correlated() as call:
        context.annotate(operation="search_knowledge")
        context.annotate(project_id="payment-service", query_id="q1", result_count=3)

        assert call.operation == "search_knowledge"
        assert context.fields() == {
            "request_id": call.request_id,
            "operation": "search_knowledge",
            "project_id": "payment-service",
            "query_id": "q1",
            "result_count": 3,
        }


def test_annotating_only_sets_what_it_was_given() -> None:
    with context.correlated() as call:
        context.annotate(operation="search_knowledge")
        context.annotate(result_count=0)

        assert call.operation == "search_knowledge"
        assert call.project_id is None


def test_a_zero_result_count_is_recorded_rather_than_treated_as_absent() -> None:
    # Finding nothing is the answer worth having in a log line: it is what
    # distinguishes a search that ran and matched nothing from one that never
    # reached the index.
    with context.correlated():
        context.annotate(result_count=0)

        assert context.fields()["result_count"] == 0


def test_outside_a_call_there_is_nothing_to_annotate_and_that_is_not_an_error() -> None:
    # A service annotates without knowing whether it was reached through the
    # API, the CLI, or a test, so this has to be a no-op rather than a failure.
    context.annotate(operation="search_knowledge", result_count=1)

    assert context.current() is None
    assert context.request_id() is None
    assert context.fields() == {}


def test_a_call_ends_where_it_began() -> None:
    with context.correlated():
        pass

    assert context.current() is None


def test_concurrent_calls_do_not_see_each_other() -> None:
    # The reason this is a context variable rather than a global: two searches
    # running at once must not end up logging each other's identifiers.
    async def run(name: str) -> tuple[str, str | None]:
        with context.correlated(name):
            await asyncio.sleep(0)
            context.annotate(operation=name)
            await asyncio.sleep(0)
            return name, context.request_id()

    async def both() -> list[tuple[str, str | None]]:
        return list(await asyncio.gather(run("call-one"), run("call-two")))

    assert asyncio.run(both()) == [("call-one", "call-one"), ("call-two", "call-two")]
