"""Retrieval logging hooks.

Every search reports what it did, so retrieval quality can be debugged after
the fact rather than reproduced by guesswork. A hook receives one entry; what
it does with it — emit a log line, store a record, do nothing — is its own
decision, and a hook that fails never fails the search that called it.

The query text is deliberately not part of any log line. It is the one field in
an entry that can carry what a person actually asked, so it is recorded only
where a deployment has opted into storing it on purpose.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from app.models.agent import AgentClientType
from app.models.retrieval_log import RetrievalMethod as LoggedMethod
from app.retrieval.types import SourceType
from app.search.types import RetrievalTrace

# The retrieval log document bounds what it stores; an entry is trimmed to the
# same bound so a search can never be refused because it selected too much.
MAX_LOGGED_RESULTS: Final = 50

logger = logging.getLogger("agent_context.retrieval")


@dataclass(frozen=True, slots=True)
class RetrievalLogEntry:
    """What one retrieval operation did, in the shape the platform records.

    The fields are the ones retrieval-quality debugging needs: what was asked
    for, how much came back, which results were selected, and how long it took.
    Retrieved content is not among them — a log of every returned document
    would duplicate the database and multiply what a leak would expose.
    """

    query_id: str
    query: str
    project_id: str | None
    requested_sources: tuple[SourceType, ...]
    result_count: int
    selected_result_ids: tuple[str, ...]
    latency_ms: float
    retrieval_method: LoggedMethod
    client_id: str | None = None
    client_type: AgentClientType | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        """Reject an entry that could not describe a real retrieval."""
        if not self.requested_sources:
            raise ValueError("a retrieval log entry requires at least one requested source")
        if isinstance(self.result_count, bool) or self.result_count < 0:
            raise ValueError("result_count must not be negative")
        if len(self.selected_result_ids) > self.result_count:
            raise ValueError("selected results cannot exceed the result count")

    @classmethod
    def from_trace(
        cls,
        trace: RetrievalTrace,
        *,
        query: str,
        project_id: str | None,
        result_keys: Sequence[str],
        client_id: str | None = None,
        client_type: AgentClientType | None = None,
        session_id: str | None = None,
    ) -> "RetrievalLogEntry":
        """Build one entry from a finished search.

        Selected results are trimmed to the recorded bound rather than rejected,
        because losing the tail of a debugging record is better than losing the
        record.
        """
        selected = tuple(result_keys)[:MAX_LOGGED_RESULTS]
        return cls(
            query_id=trace.query_id,
            query=query,
            project_id=project_id,
            requested_sources=trace.requested_sources,
            result_count=len(result_keys),
            selected_result_ids=selected,
            latency_ms=trace.latency_ms,
            retrieval_method=trace.retrieval_method,
            client_id=client_id,
            client_type=client_type,
            session_id=session_id,
        )


class RetrievalLogger(Protocol):
    """One place a finished retrieval is reported to."""

    async def record(self, entry: RetrievalLogEntry) -> None: ...


class StructuredRetrievalLogger:
    """Emit one credential-free retrieval event per search.

    This carries the identifiers retrieval debugging correlates on and nothing
    a person typed: the query text stays out of the application log, where it
    would be shipped, indexed, and retained by whatever collects stdout.
    """

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_type: AgentClientType | None = None,
        session_id: str | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_type = client_type
        self._session_id = session_id

    async def record(self, entry: RetrievalLogEntry) -> None:
        """Log what the search did, without the query or the content it found."""
        client_id = entry.client_id or self._client_id
        client_type = entry.client_type or self._client_type
        session_id = entry.session_id or self._session_id
        logger.info(
            "retrieval_completed",
            extra={
                "query_id": entry.query_id,
                "project_id": entry.project_id,
                "retrieval_method": entry.retrieval_method,
                "requested_sources": list(entry.requested_sources),
                "result_count": entry.result_count,
                "latency_ms": entry.latency_ms,
                "client_id": client_id,
                "client_type": client_type.value if client_type is not None else None,
                "session_id": session_id,
            },
        )


class CompositeRetrievalLogger:
    """Report one search to every configured hook.

    A hook that raises is reported and skipped: retrieval logging exists to
    explain searches, so it must never be the reason one fails.
    """

    def __init__(self, *loggers: RetrievalLogger) -> None:
        self._loggers = loggers

    async def record(self, entry: RetrievalLogEntry) -> None:
        """Pass the entry to each hook in turn, surviving any failure."""
        for hook in self._loggers:
            try:
                await hook.record(entry)
            # Deliberately broad: a hook is arbitrary code, and no failure in it
            # is worth failing the retrieval it was only meant to describe.
            except Exception as error:
                logger.warning(
                    "retrieval_log_failed",
                    extra={
                        "query_id": entry.query_id,
                        "retrieval_error": type(error).__name__,
                    },
                )
