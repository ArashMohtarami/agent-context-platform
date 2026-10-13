"""Business rules for durable, project-scoped context."""

from collections.abc import Callable, Sequence
from typing import Protocol

from bson import ObjectId

from app.embeddings.errors import EmbeddingCompatibilityError
from app.embeddings.types import EmbeddingVector
from app.models.common import Page, utc_now
from app.models.project import ProjectDocument
from app.models.project_context import ContextType, ProjectContextDocument
from app.models.promotion import PromotionProvenance
from app.models.skill import LifecycleStatus
from app.projects.errors import ContextNotFoundError, ProjectNotFoundError
from app.repositories.errors import DuplicateDocumentError

DEFAULT_PAGE_SIZE = 20


def embedding_text(title: str, content: str) -> str:
    """Return the text one context document is embedded from.

    The title states what the entry is about and the body says it, so both are
    embedded together: a vector built from the body alone loses the framing a
    question is most likely to match.
    """
    return f"{title}\n\n{content}"


class ContextRepository(Protocol):
    """Project-scoped context persistence required by the service."""

    project_id: str

    async def create(self, document: ProjectContextDocument) -> ProjectContextDocument: ...

    async def get(self, document_id: ObjectId) -> ProjectContextDocument | None: ...

    async def list(
        self,
        *,
        context_type: ContextType | None = None,
        status: LifecycleStatus | None = None,
        tags: Sequence[str] = (),
        minimum_importance: float = 0.0,
        limit: int = ...,
        cursor: str | None = None,
    ) -> Page[ProjectContextDocument]: ...

    async def update(
        self,
        document: ProjectContextDocument,
        *,
        expected_revision: int,
    ) -> ProjectContextDocument: ...


class ProjectLookup(Protocol):
    """The project read that proves a context item has an owner."""

    async def get(self, project_id: str) -> ProjectDocument | None: ...


class ContentEmbedder(Protocol):
    """Vector generation required to store retrievable context."""

    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]: ...


ContextRepositoryFactory = Callable[[str], ContextRepository]


class ContextService:
    """Keep context writes inside one project and always retrievable.

    A context repository is built per project rather than shared, so a request
    physically cannot read or write outside the project it names.
    """

    def __init__(
        self,
        projects: ProjectLookup,
        contexts: ContextRepositoryFactory,
        embedder: ContentEmbedder | None = None,
    ) -> None:
        self._projects = projects
        self._contexts = contexts
        self._embedder = embedder

    async def create_context(
        self,
        project_id: str,
        *,
        context_type: ContextType,
        title: str,
        content: str,
        importance: float,
        source: str,
        tags: Sequence[str] = (),
        status: LifecycleStatus = "active",
        external_id: str | None = None,
        promotion_provenance: PromotionProvenance | None = None,
    ) -> ProjectContextDocument:
        """Store one context item, embedded and bound to an existing project.

        Repeating a create with the same `external_id` returns what is already
        stored instead of failing, which is what makes an interrupted import
        safe to run again.
        """
        await self._require_project(project_id)
        repository = self._contexts(project_id)
        document = ProjectContextDocument(
            project_id=project_id,
            type=context_type,
            title=title,
            content=content,
            importance=importance,
            tags=list(tags),
            source=source,
            status=status,
            external_id=external_id,
            promotion_provenance=promotion_provenance,
        )
        document = await self._embedded(document)
        try:
            return await repository.create(document)
        except DuplicateDocumentError:
            existing = await repository.get(document.id)
            if existing is None:  # pragma: no cover - the key was taken and then removed
                raise
            return existing

    async def get_context(self, project_id: str, context_id: ObjectId) -> ProjectContextDocument:
        """Load one context item from inside its own project."""
        context = await self._contexts(project_id).get(context_id)
        if context is None:
            raise ContextNotFoundError("context was not found in this project")
        return context

    async def list_context(
        self,
        project_id: str,
        *,
        context_type: ContextType | None = None,
        status: LifecycleStatus | None = "active",
        tags: Sequence[str] = (),
        minimum_importance: float = 0.0,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ProjectContextDocument]:
        """List one project's context with bounded cursor pagination."""
        return await self._contexts(project_id).list(
            context_type=context_type,
            status=status,
            tags=tags,
            minimum_importance=minimum_importance,
            limit=limit,
            cursor=cursor,
        )

    async def update_context(
        self,
        project_id: str,
        context_id: ObjectId,
        *,
        expected_revision: int | None = None,
        context_type: ContextType | None = None,
        title: str | None = None,
        content: str | None = None,
        importance: float | None = None,
        tags: Sequence[str] | None = None,
        status: LifecycleStatus | None = None,
    ) -> ProjectContextDocument:
        """Apply a partial update, re-embedding whenever the text changes.

        The project and the idempotency key are identity, not content, so they
        cannot be edited: a context item never moves between projects.

        Public callers must supply ``expected_revision``. The optional service
        parameter remains for internal, server-derived workflows that read and
        conditionally update in one operation; it is not exposed by REST/MCP.
        """
        repository = self._contexts(project_id)
        existing = await repository.get(context_id)
        if existing is None:
            raise ContextNotFoundError("context was not found in this project")

        changes: dict[str, object] = {}
        if context_type is not None:
            changes["type"] = context_type
        if title is not None:
            changes["title"] = title
        if content is not None:
            changes["content"] = content
        if importance is not None:
            changes["importance"] = importance
        if tags is not None:
            changes["tags"] = list(tags)
        if status is not None:
            changes["status"] = status
        if not changes:
            return existing

        revision = existing.revision if expected_revision is None else expected_revision
        payload = existing.model_dump(by_alias=True, mode="python")
        payload.update(changes)
        payload["revision"] = revision
        updated = ProjectContextDocument.model_validate(payload)
        if updated.title != existing.title or updated.content != existing.content:
            updated = await self._embedded(updated)
        return await repository.update(updated, expected_revision=revision)

    async def _require_project(self, project_id: str) -> ProjectDocument:
        """Refuse context that would belong to no project."""
        project = await self._projects.get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"project {project_id} was not found")
        return project

    async def _embedded(self, document: ProjectContextDocument) -> ProjectContextDocument:
        """Attach a vector built from the document's own text.

        A write fails rather than silently storing knowledge that semantic
        retrieval could never find; with no embedding model configured, the
        document is stored unembedded and stays lexically retrievable.
        """
        if self._embedder is None:
            return document
        vectors = await self._embedder.embed_chunks(
            [embedding_text(document.title, document.content)]
        )
        if len(vectors) != 1:
            raise EmbeddingCompatibilityError("the embedder returned more than one vector")
        vector = vectors[0]
        payload = document.model_dump(by_alias=True, mode="python")
        payload.update(
            embedding=list(vector.values),
            embedding_model=vector.model,
            embedding_dimensions=vector.dimensions,
            embedding_generated_at=utc_now(),
        )
        return ProjectContextDocument.model_validate(payload)
