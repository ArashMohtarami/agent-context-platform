"""In-memory memory doubles shared by the Phase 10 tests."""

from collections.abc import Sequence
from datetime import UTC, datetime

from bson import ObjectId

from app.models.common import Page
from app.models.memory import MemoryDocument, MemoryScope
from app.models.skill import LifecycleStatus
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError

DIMENSIONS = 4
MODEL = "stub-embedding-model"


def memory(
    project_id: str | None = None,
    *,
    scope: MemoryScope | None = None,
    client_id: str | None = None,
    memory_type: str = "preference",
    content: str = "Use strict typing in Python projects.",
    summary: str | None = None,
    importance: float = 0.8,
    confidence: float = 0.9,
    source: str = "user",
    tags: Sequence[str] = ("python",),
    status: LifecycleStatus = "active",
    external_id: str | None = None,
    supersedes: ObjectId | None = None,
    superseded_by: ObjectId | None = None,
    embedded: bool = False,
) -> MemoryDocument:
    """Build one stored memory, global unless a project is named."""
    resolved_scope: MemoryScope = (
        scope if scope is not None else ("global" if project_id is None else "project")
    )
    values: dict[str, object] = {
        "scope": resolved_scope,
        "project_id": project_id,
        "client_id": client_id,
        "type": memory_type,
        "content": content,
        "summary": summary,
        "importance": importance,
        "confidence": confidence,
        "source": source,
        "tags": list(tags),
        "status": status,
        "external_id": external_id,
        "supersedes": supersedes,
        "superseded_by": superseded_by,
    }
    if embedded:
        values.update(
            embedding=[0.5] * DIMENSIONS,
            embedding_model=MODEL,
            embedding_dimensions=DIMENSIONS,
            embedding_generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    return MemoryDocument.model_validate(values)


class FakeMemoryRepository:
    """One scope's memories, stored in memory and never leaving that scope."""

    def __init__(
        self,
        project_id: str | None,
        documents: Sequence[MemoryDocument] = (),
        *,
        scope: MemoryScope | None = None,
        client_id: str | None = None,
    ) -> None:
        self.project_id = project_id
        self.client_id = client_id
        self.scope: MemoryScope = (
            scope if scope is not None else ("global" if project_id is None else "project")
        )
        self.documents: dict[ObjectId, MemoryDocument] = {
            document.id: document
            for document in documents
            if (
                document.scope == self.scope
                and document.project_id == project_id
                and document.client_id == client_id
            )
        }

    async def create(self, document: MemoryDocument) -> MemoryDocument:
        if document.id in self.documents:
            raise DuplicateDocumentError("a document with this key already exists")
        self.documents[document.id] = document
        return document

    async def get(self, document_id: ObjectId) -> MemoryDocument | None:
        return self.documents.get(document_id)

    async def list(
        self,
        *,
        memory_type: str | None = None,
        status: LifecycleStatus | None = None,
        tags: Sequence[str] = (),
        minimum_importance: float = 0.0,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[MemoryDocument]:
        selected = [
            document
            for document in self.documents.values()
            if (memory_type is None or document.type == memory_type)
            and (status is None or document.status == status)
            and all(tag in document.tags for tag in tags)
            and document.importance >= minimum_importance
        ]
        return Page(items=tuple(selected[:limit]), next_cursor=None)

    async def update(
        self,
        document: MemoryDocument,
        *,
        expected_revision: int,
    ) -> MemoryDocument:
        stored = self.documents.get(document.id)
        if stored is None or stored.revision != expected_revision:
            raise ConcurrencyConflictError("document is absent or was concurrently modified")
        payload = document.model_dump(by_alias=True, mode="python")
        payload["revision"] = expected_revision + 1
        updated = MemoryDocument.model_validate(payload)
        self.documents[document.id] = updated
        return updated


class FakeMemoryRepositoryFactory:
    """Build one in-memory repository per scope, remembering each."""

    def __init__(self, documents: Sequence[MemoryDocument] = ()) -> None:
        self.repositories: dict[
            tuple[MemoryScope, str | None, str | None], FakeMemoryRepository
        ] = {}
        self.requested: list[tuple[MemoryScope, str | None, str | None]] = []
        for document in documents:
            self(document.scope, document.project_id, document.client_id).documents[document.id] = (
                document
            )
        self.requested.clear()

    def __call__(
        self,
        scope: MemoryScope | str | None,
        project_id: str | None = None,
        client_id: str | None = None,
    ) -> FakeMemoryRepository:
        """Build a repository, accepting the legacy one-argument test form."""
        if scope not in {"global", "project", "agent", "project_agent"}:
            project_id = scope
            scope = "global" if project_id is None else "project"
        key = (scope, project_id, client_id)
        self.requested.append(key)
        if key not in self.repositories:
            self.repositories[key] = FakeMemoryRepository(
                project_id,
                scope=scope,
                client_id=client_id,
            )
        return self.repositories[key]
