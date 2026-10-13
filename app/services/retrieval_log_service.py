"""Persisting retrieval hook, for deployments that opt into storing queries.

The retrieval log is the record that makes retrieval quality answerable later:
what was asked, what came back, and how long it took. It also stores the query
text, which is the one part of a search that can describe a person, so storing
it is a deliberate configuration choice rather than a default.
"""

from collections.abc import Callable
from typing import Protocol

from app.models.agent import AgentClientType
from app.models.retrieval_log import RetrievalLogDocument
from app.search.logging import RetrievalLogEntry


class RetrievalLogRepository(Protocol):
    """The one write a retrieval log needs."""

    project_id: str | None

    async def create(self, document: RetrievalLogDocument) -> RetrievalLogDocument: ...


# A log repository is bound to one project when it is built, so the hook takes a
# factory: the scope comes from the search that was run, and no shared handle can
# file one project's searches under another.
RetrievalLogFactory = Callable[[str | None], RetrievalLogRepository]


class PersistentRetrievalLogger:
    """Store one bounded record per search, inside the scope it was run in."""

    def __init__(
        self,
        logs: RetrievalLogFactory,
        *,
        client_id: str | None = None,
        client_type: AgentClientType | None = None,
        session_id: str | None = None,
    ) -> None:
        self._logs = logs
        self._client_id = client_id
        self._client_type = client_type
        self._session_id = session_id

    async def record(self, entry: RetrievalLogEntry) -> None:
        """Write one immutable retrieval record.

        Only metadata is stored. Retrieved content is referenced by the stable
        keys the search returned, so the record stays small and reading it back
        cannot become a second way to read the knowledge base.
        """
        document = RetrievalLogDocument(
            query=entry.query,
            project_id=entry.project_id,
            requested_sources=list(entry.requested_sources),
            result_count=entry.result_count,
            selected_result_ids=list(entry.selected_result_ids),
            latency_ms=entry.latency_ms,
            retrieval_method=entry.retrieval_method,
            client_id=entry.client_id or self._client_id,
            client_type=entry.client_type or self._client_type,
            session_id=entry.session_id or self._session_id,
        )
        await self._logs(entry.project_id).create(document)
