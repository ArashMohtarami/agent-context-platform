"""Business rules for intentional, explicitly stored memory."""

from collections.abc import Callable, Sequence
from typing import Protocol

from bson import ObjectId

from app.embeddings.errors import EmbeddingCompatibilityError
from app.embeddings.types import EmbeddingVector
from app.memory.errors import MemoryNotFoundError, MemorySupersessionError
from app.models.common import Page, utc_now
from app.models.memory import MemoryDocument, MemoryScope
from app.models.project import ProjectDocument
from app.models.promotion import PromotionProvenance
from app.models.skill import LifecycleStatus
from app.observability.instruments import record_memory_operation
from app.projects.errors import ProjectNotFoundError
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError

DEFAULT_PAGE_SIZE = 20
# What a memory write turned out to be. Counted rather than only logged,
# because memory is written deliberately and rarely: the rate at which it is
# being stored, corrected, or refused is a thing an operator watches.
_OK = "ok"
_FAILED = "failed"
_UNCHANGED = "unchanged"


def embedding_text(summary: str | None, content: str) -> str:
    """Return the text one memory is embedded from.

    A summary states the memory in one line, so it is embedded with the body it
    condenses rather than instead of it.
    """
    return content if summary is None else f"{summary}\n\n{content}"


def memory_scope(
    project_id: str | None,
    *,
    client_id: str | None = None,
    requested_scope: MemoryScope | None = None,
) -> MemoryScope:
    """Resolve one safe memory scope from server-derived ownership facts.

    Omitting ``requested_scope`` deliberately retains V1 behavior: memory is
    shared globally or within its project even when the authenticated caller is
    an agent. Agent scopes are opt-in operational state and require the client
    identity supplied by authentication, never a caller-provided client id.
    """
    scope: MemoryScope = (
        requested_scope
        if requested_scope is not None
        else ("global" if project_id is None else "project")
    )
    requires_project = scope in {"project", "project_agent"}
    requires_client = scope in {"agent", "project_agent"}
    if (project_id is not None) != requires_project:
        raise ValueError(f"{scope} memory has an invalid project_id")
    if requires_client and client_id is None:
        raise ValueError(f"{scope} memory requires an authenticated client")
    return scope


def memory_client_id(scope: MemoryScope, client_id: str | None) -> str | None:
    """Keep client ownership only for the two operational memory scopes."""
    return client_id if scope in {"agent", "project_agent"} else None


class MemoryRepository(Protocol):
    """Scope-bound memory persistence required by the service."""

    project_id: str | None
    client_id: str | None
    scope: MemoryScope

    async def create(self, document: MemoryDocument) -> MemoryDocument: ...

    async def get(self, document_id: ObjectId) -> MemoryDocument | None: ...

    async def list(
        self,
        *,
        memory_type: str | None = None,
        status: LifecycleStatus | None = None,
        tags: Sequence[str] = (),
        minimum_importance: float = 0.0,
        limit: int = ...,
        cursor: str | None = None,
    ) -> Page[MemoryDocument]: ...

    async def update(
        self,
        document: MemoryDocument,
        *,
        expected_revision: int,
    ) -> MemoryDocument: ...


class ProjectLookup(Protocol):
    """The project read that proves project memory has an owner."""

    async def get(self, project_id: str) -> ProjectDocument | None: ...


class ContentEmbedder(Protocol):
    """Vector generation required to store retrievable memory."""

    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]: ...


MemoryRepositoryFactory = Callable[[MemoryScope, str | None, str | None], MemoryRepository]


class MemoryService:
    """Store memory on purpose, and replace it without destroying history.

    Nothing here reads a conversation: memory is written only when a caller
    asks for it. Automatic extraction is a later phase with its own safeguards.
    """

    def __init__(
        self,
        projects: ProjectLookup,
        memories: MemoryRepositoryFactory,
        embedder: ContentEmbedder | None = None,
    ) -> None:
        self._projects = projects
        self._memories = memories
        self._embedder = embedder

    async def create_memory(
        self,
        *,
        memory_type: str,
        content: str,
        importance: float,
        confidence: float,
        source: str,
        project_id: str | None = None,
        scope: MemoryScope | None = None,
        client_id: str | None = None,
        summary: str | None = None,
        tags: Sequence[str] = (),
        status: LifecycleStatus = "active",
        external_id: str | None = None,
        supersedes: ObjectId | None = None,
        promotion_provenance: PromotionProvenance | None = None,
    ) -> MemoryDocument:
        """Store one memory, optionally replacing the one it corrects.

        When `supersedes` is given the new memory is written first and the old
        one is then marked superseded. That order matters: if the second write
        fails, both memories are still stored and retrievable, where the
        reverse order would leave a dangling link and lose the old knowledge.
        """
        # A memory that replaces another is a correction, not an addition, and
        # the two are worth counting apart.
        operation = "create" if supersedes is None else "supersede"
        outcome = _FAILED
        try:
            resolved_scope = memory_scope(
                project_id,
                client_id=client_id,
                requested_scope=scope,
            )
            owner_client_id = memory_client_id(resolved_scope, client_id)
            if project_id is not None:
                await self._require_project(project_id)
            repository = self._memories(resolved_scope, project_id, owner_client_id)
            replaced = await self._replaceable(repository, supersedes)

            document = MemoryDocument(
                scope=resolved_scope,
                project_id=project_id,
                client_id=owner_client_id,
                type=memory_type,
                content=content,
                summary=summary,
                importance=importance,
                confidence=confidence,
                source=source,
                tags=list(tags),
                status=status,
                external_id=external_id,
                supersedes=supersedes,
                promotion_provenance=promotion_provenance,
            )
            document = await self._embedded(document)
            created = await self._create_or_existing(repository, document)
            if replaced is not None:
                await self._mark_superseded(repository, replaced, created)
            outcome = _OK
            return created
        finally:
            record_memory_operation(operation=operation, outcome=outcome)

    async def get_memory(
        self,
        memory_id: ObjectId,
        *,
        project_id: str | None = None,
        scope: MemoryScope | None = None,
        client_id: str | None = None,
    ) -> MemoryDocument:
        """Load one memory from inside its own scope."""
        resolved_scope = memory_scope(
            project_id,
            client_id=client_id,
            requested_scope=scope,
        )
        memory = await self._memories(
            resolved_scope, project_id, memory_client_id(resolved_scope, client_id)
        ).get(memory_id)
        if memory is None:
            raise MemoryNotFoundError("memory was not found in this scope")
        return memory

    async def list_memories(
        self,
        *,
        project_id: str | None = None,
        scope: MemoryScope | None = None,
        client_id: str | None = None,
        memory_type: str | None = None,
        status: LifecycleStatus | None = "active",
        tags: Sequence[str] = (),
        minimum_importance: float = 0.0,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[MemoryDocument]:
        """List one scope's memories with bounded cursor pagination."""
        resolved_scope = memory_scope(
            project_id,
            client_id=client_id,
            requested_scope=scope,
        )
        return await self._memories(
            resolved_scope, project_id, memory_client_id(resolved_scope, client_id)
        ).list(
            memory_type=memory_type,
            status=status,
            tags=tags,
            minimum_importance=minimum_importance,
            limit=limit,
            cursor=cursor,
        )

    async def update_memory(
        self,
        memory_id: ObjectId,
        *,
        project_id: str | None = None,
        scope: MemoryScope | None = None,
        client_id: str | None = None,
        expected_revision: int | None = None,
        memory_type: str | None = None,
        content: str | None = None,
        summary: str | None = None,
        importance: float | None = None,
        confidence: float | None = None,
        tags: Sequence[str] | None = None,
        status: LifecycleStatus | None = None,
    ) -> MemoryDocument:
        """Apply a partial update, re-embedding whenever the text changes.

        Scope, the idempotency key, and both ends of a supersession link are
        identity rather than content, so none of them can be edited here: a
        memory never moves between projects and never loses its history.
        """
        outcome = _FAILED
        try:
            resolved_scope = memory_scope(
                project_id,
                client_id=client_id,
                requested_scope=scope,
            )
            repository = self._memories(
                resolved_scope, project_id, memory_client_id(resolved_scope, client_id)
            )
            existing = await repository.get(memory_id)
            if existing is None:
                raise MemoryNotFoundError("memory was not found in this scope")

            changes: dict[str, object] = {}
            if memory_type is not None:
                changes["type"] = memory_type
            if content is not None:
                changes["content"] = content
            if summary is not None:
                changes["summary"] = summary
            if importance is not None:
                changes["importance"] = importance
            if confidence is not None:
                changes["confidence"] = confidence
            if tags is not None:
                changes["tags"] = list(tags)
            if status is not None:
                changes["status"] = status
            if not changes:
                outcome = _UNCHANGED
                return existing

            revision = existing.revision if expected_revision is None else expected_revision
            updated = _applied(existing, changes, revision)
            if updated.content != existing.content or updated.summary != existing.summary:
                updated = await self._embedded(updated)
            stored = await repository.update(updated, expected_revision=revision)
            outcome = _OK
            return stored
        finally:
            record_memory_operation(operation="update", outcome=outcome)

    async def _replaceable(
        self,
        repository: MemoryRepository,
        supersedes: ObjectId | None,
    ) -> MemoryDocument | None:
        """Load the memory a new one replaces, refusing a broken chain."""
        if supersedes is None:
            return None
        replaced = await repository.get(supersedes)
        if replaced is None:
            raise MemoryNotFoundError("the memory to supersede was not found in this scope")
        if replaced.superseded_by is not None or replaced.status == "superseded":
            raise MemorySupersessionError("that memory has already been superseded")
        return replaced

    @staticmethod
    async def _create_or_existing(
        repository: MemoryRepository,
        document: MemoryDocument,
    ) -> MemoryDocument:
        """Store the memory, returning what is stored if the key is taken."""
        try:
            return await repository.create(document)
        except DuplicateDocumentError:
            existing = await repository.get(document.id)
            if existing is None:  # pragma: no cover - the key was taken and then removed
                raise
            return existing

    @staticmethod
    async def _mark_superseded(
        repository: MemoryRepository,
        replaced: MemoryDocument,
        replacement: MemoryDocument,
    ) -> MemoryDocument:
        """Record on the old memory which memory replaced it."""
        updated = _applied(
            replaced,
            {"status": "superseded", "superseded_by": replacement.id},
            replaced.revision,
        )
        try:
            return await repository.update(updated, expected_revision=replaced.revision)
        except ConcurrencyConflictError as error:
            raise MemorySupersessionError(
                f"memory {replacement.id} was stored, but {replaced.id} changed before it "
                "could be marked superseded; mark it explicitly"
            ) from error

    async def _require_project(self, project_id: str) -> ProjectDocument:
        """Refuse project memory that would belong to no project."""
        project = await self._projects.get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"project {project_id} was not found")
        return project

    async def _embedded(self, document: MemoryDocument) -> MemoryDocument:
        """Attach a vector built from the memory's own text.

        A write fails rather than silently storing a memory that semantic
        retrieval could never find; with no embedding model configured, the
        memory is stored unembedded and stays lexically retrievable.
        """
        if self._embedder is None:
            return document
        vectors = await self._embedder.embed_chunks(
            [embedding_text(document.summary, document.content)]
        )
        if len(vectors) != 1:
            raise EmbeddingCompatibilityError("the embedder returned more than one vector")
        vector = vectors[0]
        return _applied(
            document,
            {
                "embedding": list(vector.values),
                "embedding_model": vector.model,
                "embedding_dimensions": vector.dimensions,
                "embedding_generated_at": utc_now(),
            },
            document.revision,
        )


def _applied(
    document: MemoryDocument,
    changes: dict[str, object],
    revision: int,
) -> MemoryDocument:
    """Re-validate a memory with changes applied, never bypassing the model."""
    payload = document.model_dump(by_alias=True, mode="python")
    payload.update(changes)
    payload["revision"] = revision
    return MemoryDocument.model_validate(payload, context={"from_storage": True})
