"""Retrieval logging hooks: what they record, and what they never record."""

import logging

import pytest

from app.models.agent import AgentClientType
from app.models.retrieval_log import RetrievalLogDocument
from app.search.logging import (
    MAX_LOGGED_RESULTS,
    CompositeRetrievalLogger,
    RetrievalLogEntry,
    StructuredRetrievalLogger,
)
from app.search.types import RetrievalTrace
from app.services.retrieval_log_service import PersistentRetrievalLogger

QUERY = "How do we prevent duplicate payment requests?"
PROJECT = "sampleco-payment"


def trace(query_id: str = "0123456789abcdef") -> RetrievalTrace:
    """Build one finished search's retrieval metadata."""
    return RetrievalTrace(
        query_id=query_id,
        retrieval_method="hybrid",
        requested_sources=("project_context", "active_skill", "memory"),
        searched_sources=("project_context", "active_skill", "memory"),
        skipped_sources=(),
        latency_ms=12.5,
    )


def entry(**overrides: object) -> RetrievalLogEntry:
    """Build one retrieval log entry from a finished search."""
    values: dict[str, object] = {
        "query": QUERY,
        "project_id": PROJECT,
        "result_keys": ("skill_chunk:fastapi-production:1.0.0:chunk-0000",),
    }
    values.update(overrides)
    return RetrievalLogEntry.from_trace(trace(), **values)  # type: ignore[arg-type]


class FakeLogRepository:
    """Record what one scope's retrieval log was asked to store."""

    def __init__(self, project_id: str | None, error: Exception | None = None) -> None:
        self.project_id = project_id
        self.error = error
        self.documents: list[RetrievalLogDocument] = []

    async def create(self, document: RetrievalLogDocument) -> RetrievalLogDocument:
        if self.error is not None:
            raise self.error
        self.documents.append(document)
        return document


class FakeLogFactory:
    """Build one retrieval log repository per scope, remembering each."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.repositories: dict[str | None, FakeLogRepository] = {}
        self.requested: list[str | None] = []

    def __call__(self, project_id: str | None) -> FakeLogRepository:
        self.requested.append(project_id)
        if project_id not in self.repositories:
            self.repositories[project_id] = FakeLogRepository(project_id, self.error)
        return self.repositories[project_id]


class FailingLogger:
    """A hook that always fails."""

    async def record(self, entry: RetrievalLogEntry) -> None:
        raise RuntimeError("the log is unreachable")


class RecordingLogger:
    """A hook that remembers what it was told."""

    def __init__(self) -> None:
        self.entries: list[RetrievalLogEntry] = []

    async def record(self, entry: RetrievalLogEntry) -> None:
        self.entries.append(entry)


def test_an_entry_carries_what_a_search_did() -> None:
    recorded = entry()

    assert recorded.query_id == "0123456789abcdef"
    assert recorded.retrieval_method == "hybrid"
    assert recorded.requested_sources == ("project_context", "active_skill", "memory")
    assert recorded.result_count == 1
    assert recorded.latency_ms == 12.5


def test_selected_results_are_trimmed_rather_than_refused() -> None:
    keys = tuple(f"skill_chunk:skill:1.0.0:chunk-{index:04d}" for index in range(80))

    recorded = entry(result_keys=keys)

    # Losing the tail of a debugging record beats losing the record.
    assert len(recorded.selected_result_ids) == MAX_LOGGED_RESULTS
    assert recorded.result_count == 80


def test_an_entry_describing_no_source_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one requested source"):
        RetrievalLogEntry(
            query_id="abc",
            query=QUERY,
            project_id=None,
            requested_sources=(),
            result_count=0,
            selected_result_ids=(),
            latency_ms=1.0,
            retrieval_method="hybrid",
        )


def test_the_structured_hook_never_logs_the_query_or_the_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import asyncio

    with caplog.at_level(logging.INFO, logger="agent_context.retrieval"):
        asyncio.run(StructuredRetrievalLogger().record(entry()))

    record = caplog.records[0]
    assert record.message == "retrieval_completed"
    assert record.query_id == "0123456789abcdef"  # type: ignore[attr-defined]
    assert record.project_id == PROJECT  # type: ignore[attr-defined]
    assert record.result_count == 1  # type: ignore[attr-defined]
    assert record.latency_ms == 12.5  # type: ignore[attr-defined]
    # The one field that can describe who asked what stays out of the log.
    assert not hasattr(record, "query")
    assert QUERY not in caplog.text


def test_the_structured_hook_adds_safe_gateway_session_correlation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import asyncio

    with caplog.at_level(logging.INFO, logger="agent_context.retrieval"):
        asyncio.run(
            StructuredRetrievalLogger(
                client_id="claude-code-main",
                client_type=AgentClientType.CLAUDE_CODE,
                session_id="claude-session-1",
            ).record(entry())
        )

    record = caplog.records[0]
    assert record.client_id == "claude-code-main"  # type: ignore[attr-defined]
    assert record.client_type == "claude_code"  # type: ignore[attr-defined]
    assert record.session_id == "claude-session-1"  # type: ignore[attr-defined]
    assert QUERY not in caplog.text


@pytest.mark.asyncio
async def test_the_persistent_hook_stores_one_record_inside_its_own_scope() -> None:
    logs = FakeLogFactory()

    await PersistentRetrievalLogger(logs).record(entry())

    assert logs.requested == [PROJECT]
    stored = logs.repositories[PROJECT].documents[0]
    assert stored.query == QUERY
    assert stored.project_id == PROJECT
    assert stored.result_count == 1
    assert stored.retrieval_method == "hybrid"
    assert stored.selected_result_ids == ["skill_chunk:fastapi-production:1.0.0:chunk-0000"]


@pytest.mark.asyncio
async def test_the_persistent_hook_adds_gateway_client_metadata_without_query_content() -> None:
    logs = FakeLogFactory()

    await PersistentRetrievalLogger(
        logs,
        client_id="codex-main",
        client_type=AgentClientType.CODEX,
        session_id="session-1",
    ).record(entry())

    stored = logs.repositories[PROJECT].documents[0]
    assert stored.client_id == "codex-main"
    assert stored.client_type == AgentClientType.CODEX
    assert stored.session_id == "session-1"
    assert QUERY not in str(
        {
            "client_id": stored.client_id,
            "client_type": stored.client_type,
            "session_id": stored.session_id,
        }
    )


@pytest.mark.asyncio
async def test_a_global_search_is_filed_under_no_project() -> None:
    logs = FakeLogFactory()

    await PersistentRetrievalLogger(logs).record(entry(project_id=None))

    assert logs.requested == [None]
    assert logs.repositories[None].documents[0].project_id is None


@pytest.mark.asyncio
async def test_the_stored_record_holds_no_retrieved_content() -> None:
    logs = FakeLogFactory()

    await PersistentRetrievalLogger(logs).record(entry())

    stored = logs.repositories[PROJECT].documents[0].model_dump()
    # Results are referenced by key; the knowledge itself stays in one place.
    assert "content" not in stored
    assert "results" not in stored


@pytest.mark.asyncio
async def test_every_hook_is_told_even_when_an_earlier_one_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    recording = RecordingLogger()
    composite = CompositeRetrievalLogger(FailingLogger(), recording)

    with caplog.at_level(logging.WARNING, logger="agent_context.retrieval"):
        await composite.record(entry())

    assert len(recording.entries) == 1
    failure = caplog.records[0]
    assert failure.message == "retrieval_log_failed"
    # The failure is named by type, so a hook's exception text cannot smuggle
    # retrieved content into a log that deliberately carries none.
    assert failure.retrieval_error == "RuntimeError"  # type: ignore[attr-defined]
    assert "the log is unreachable" not in caplog.text


@pytest.mark.asyncio
async def test_an_unreachable_log_never_fails_the_search_that_wrote_it() -> None:
    logs = FakeLogFactory(error=RuntimeError("the database is unreachable"))
    composite = CompositeRetrievalLogger(PersistentRetrievalLogger(logs))

    await composite.record(entry())

    assert logs.repositories[PROJECT].documents == []
