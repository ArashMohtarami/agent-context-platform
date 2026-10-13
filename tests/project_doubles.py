"""In-memory project and context doubles shared by the Phase 9 tests."""

from collections.abc import Sequence
from datetime import UTC, datetime

from bson import ObjectId

from app.embeddings.types import EmbeddingVector
from app.models.common import Page
from app.models.project import ProjectDocument
from app.models.project_context import ContextType, ProjectContextDocument
from app.models.skill import LifecycleStatus
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError

DIMENSIONS = 4
MODEL = "stub-embedding-model"


def project(project_id: str = "sampleco-payment", **overrides: object) -> ProjectDocument:
    """Build one project with production-shaped defaults."""
    values: dict[str, object] = {
        "project_id": project_id,
        "name": "Sample Company Payment Service",
        "description": "Payment orchestration",
        "repository": "git@example.invalid:sampleco/payment.git",
        "environment": "production",
        "tags": ["payments"],
    }
    values.update(overrides)
    return ProjectDocument.model_validate(values)


def context(
    project_id: str = "sampleco-payment",
    *,
    title: str = "Payment idempotency strategy",
    content: str = "Payment requests carry a UUID4 idempotency key.",
    context_type: ContextType = "architecture_decision",
    importance: float = 0.95,
    tags: Sequence[str] = ("payments", "idempotency"),
    source: str = "user",
    status: LifecycleStatus = "active",
    external_id: str | None = None,
    embedded: bool = False,
) -> ProjectContextDocument:
    """Build one stored context document."""
    values: dict[str, object] = {
        "project_id": project_id,
        "type": context_type,
        "title": title,
        "content": content,
        "importance": importance,
        "tags": list(tags),
        "source": source,
        "status": status,
        "external_id": external_id,
    }
    if embedded:
        values.update(
            embedding=[0.5] * DIMENSIONS,
            embedding_model=MODEL,
            embedding_dimensions=DIMENSIONS,
            embedding_generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    return ProjectContextDocument.model_validate(values)


class FakeProjectRepository:
    """Serve projects from an in-memory catalogue."""

    def __init__(self, documents: Sequence[ProjectDocument] = ()) -> None:
        self.documents: dict[str, ProjectDocument] = {
            document.project_id: document for document in documents
        }

    async def create(self, document: ProjectDocument) -> ProjectDocument:
        if document.project_id in self.documents:
            raise DuplicateDocumentError("a document with this key already exists")
        self.documents[document.project_id] = document
        return document

    async def get(self, project_id: str) -> ProjectDocument | None:
        return self.documents.get(project_id)

    async def list(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ProjectDocument]:
        items = tuple(self.documents.values())[:limit]
        return Page(items=items, next_cursor=None)


class FakeContextRepository:
    """One project's context, stored in memory and never leaving its scope."""

    def __init__(self, project_id: str, documents: Sequence[ProjectContextDocument] = ()) -> None:
        self.project_id = project_id
        self.documents: dict[ObjectId, ProjectContextDocument] = {
            document.id: document for document in documents if document.project_id == project_id
        }

    async def create(self, document: ProjectContextDocument) -> ProjectContextDocument:
        if document.id in self.documents:
            raise DuplicateDocumentError("a document with this key already exists")
        self.documents[document.id] = document
        return document

    async def get(self, document_id: ObjectId) -> ProjectContextDocument | None:
        return self.documents.get(document_id)

    async def list(
        self,
        *,
        context_type: ContextType | None = None,
        status: LifecycleStatus | None = None,
        tags: Sequence[str] = (),
        minimum_importance: float = 0.0,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ProjectContextDocument]:
        selected = [
            document
            for document in self.documents.values()
            if (context_type is None or document.type == context_type)
            and (status is None or document.status == status)
            and all(tag in document.tags for tag in tags)
            and document.importance >= minimum_importance
        ]
        return Page(items=tuple(selected[:limit]), next_cursor=None)

    async def update(
        self,
        document: ProjectContextDocument,
        *,
        expected_revision: int,
    ) -> ProjectContextDocument:
        stored = self.documents.get(document.id)
        if stored is None or stored.revision != expected_revision:
            raise ConcurrencyConflictError("document is absent or was concurrently modified")
        payload = document.model_dump(by_alias=True, mode="python")
        payload["revision"] = expected_revision + 1
        updated = ProjectContextDocument.model_validate(payload)
        self.documents[document.id] = updated
        return updated


class FakeContextRepositoryFactory:
    """Build one in-memory repository per project, remembering each."""

    def __init__(self, documents: Sequence[ProjectContextDocument] = ()) -> None:
        self.repositories: dict[str, FakeContextRepository] = {}
        for document in documents:
            self(document.project_id).documents[document.id] = document

    def __call__(self, project_id: str) -> FakeContextRepository:
        if project_id not in self.repositories:
            self.repositories[project_id] = FakeContextRepository(project_id)
        return self.repositories[project_id]


class FakeEmbedder:
    """Embed document text without loading a model."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.texts: list[str] = []

    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        self.texts.extend(contents)
        if self.error is not None:
            raise self.error
        return tuple(
            EmbeddingVector(
                model=MODEL,
                dimensions=DIMENSIONS,
                values=(float(len(text) % 7) / 10.0,) * DIMENSIONS,
            )
            for text in contents
        )

    async def embed_query(self, text: str) -> EmbeddingVector:
        self.texts.append(text)
        if self.error is not None:
            raise self.error
        return EmbeddingVector(model=MODEL, dimensions=DIMENSIONS, values=(0.1,) * DIMENSIONS)
