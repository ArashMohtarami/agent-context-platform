"""Scope-isolated MongoDB repository for intentional memories."""

from collections.abc import Sequence
from datetime import datetime

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.memory_indexes import (
    MEMORY_TEXT_INDEX,
    MEMORY_VECTOR_INDEX,
    MemoryQueryFilters,
    memory_filter_clauses,
    memory_text_query,
    memory_vector_filter,
)
from app.db.vector_indexes import VECTOR_PATH
from app.memory.types import MemoryMatch
from app.models.common import Page, validate_identifier
from app.models.memory import MemoryDocument, MemoryScope
from app.models.skill import LifecycleStatus
from app.repositories.base import (
    DEFAULT_PAGE_SIZE,
    SCORE_FIELD,
    MongoRepository,
    SearchMethod,
)
from app.repositories.errors import ProjectIsolationError
from app.repositories.vectors import validated_vector
from app.security.tenant import TenantContext

DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50
MAX_NUM_CANDIDATES = 10_000


class MongoMemoryRepository(MongoRepository[MemoryDocument]):
    """Bind memory operations to one shared or agent-operational address.

    Writes and direct reads stay inside the scope the repository was built
    with. Searches may include the exact global/project/agent/project-agent
    union described by ``MemoryQueryFilters``, but never another project's or
    another client's operational state.
    """

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        *,
        project_id: str | None,
        client_id: str | None = None,
        scope: MemoryScope | None = None,
        tenant: TenantContext | None = None,
    ) -> None:
        super().__init__(database, "memories", MemoryDocument, tenant=tenant)
        self.project_id = validate_identifier(project_id) if project_id is not None else None
        self.client_id = validate_identifier(client_id) if client_id is not None else None
        self.scope = _address_scope(self.project_id, self.client_id, scope)

    @property
    def _scope_filter(self) -> MongoDocument:
        return {
            "scope": self.scope,
            "project_id": self.project_id,
            "client_id": self.client_id,
        }

    def _require_scope(self, document: MemoryDocument) -> None:
        if (
            document.scope != self.scope
            or document.project_id != self.project_id
            or document.client_id != self.client_id
        ):
            raise ProjectIsolationError("memory belongs to a different scope or project or client")

    def _require_query_scope(self, filters: MemoryQueryFilters) -> None:
        """Refuse a search that would reach outside this repository's scope."""
        if filters.project_id != self.project_id:
            raise ProjectIsolationError("query belongs to a different project")
        if filters.client_id != self.client_id:
            raise ProjectIsolationError("query belongs to a different client")

    async def create(self, document: MemoryDocument) -> MemoryDocument:
        self._require_scope(document)
        return await self._create(document)

    async def get(self, document_id: ObjectId) -> MemoryDocument | None:
        return await self._get({"_id": document_id, **self._scope_filter})

    async def list(
        self,
        *,
        memory_type: str | None = None,
        status: LifecycleStatus | None = None,
        tags: Sequence[str] = (),
        minimum_importance: float = 0.0,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[MemoryDocument]:
        filters = self._scope_filter
        if memory_type is not None:
            filters["type"] = memory_type
        if status is not None:
            filters["status"] = status
        if tags:
            filters["tags"] = {"$all": list(tags)}
        if minimum_importance > 0.0:
            filters["importance"] = {"$gte": minimum_importance}
        return await self._page(filters, limit=limit, cursor=cursor)

    async def list_recent(
        self,
        *,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[MemoryDocument, ...]:
        """Return active memories in this immutable scope by most recent update."""
        return await self._recent(
            {**self._scope_filter, "status": "active"},
            updated_after=updated_after,
            updated_before=updated_before,
            limit=limit,
        )

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        num_candidates: int,
        filters: MemoryQueryFilters,
    ) -> tuple[MemoryMatch, ...]:
        """Return the most similar memories in scope, with their scores."""
        self._require_query_scope(filters)
        query_vector = validated_vector(embedding)
        _validate_limit(limit)
        if isinstance(num_candidates, bool) or not limit <= num_candidates <= MAX_NUM_CANDIDATES:
            raise ValueError(f"num_candidates must be between limit and {MAX_NUM_CANDIDATES}")
        pipeline: list[MongoDocument] = [
            {
                "$vectorSearch": {
                    "index": MEMORY_VECTOR_INDEX,
                    "path": VECTOR_PATH,
                    "queryVector": query_vector,
                    "numCandidates": num_candidates,
                    "limit": limit,
                    "filter": memory_vector_filter(filters),
                }
            },
            {"$addFields": {SCORE_FIELD: {"$meta": "vectorSearchScore"}}},
        ]
        return await self._matches(pipeline, limit, method="vector")

    async def text_search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        phrase: bool = False,
        filters: MemoryQueryFilters,
    ) -> tuple[MemoryMatch, ...]:
        """Return the memories in scope matching a lexical query."""
        self._require_query_scope(filters)
        _validate_limit(limit)
        compound = {
            **memory_text_query(query, phrase=phrase),
            "filter": memory_filter_clauses(filters),
        }
        pipeline: list[MongoDocument] = [
            {"$search": {"index": MEMORY_TEXT_INDEX, "compound": compound}},
            {"$addFields": {SCORE_FIELD: {"$meta": "searchScore"}}},
            {"$limit": limit},
        ]
        return await self._matches(pipeline, limit, method="lexical")

    async def _matches(
        self,
        pipeline: Sequence[MongoDocument],
        limit: int,
        *,
        method: SearchMethod,
    ) -> tuple[MemoryMatch, ...]:
        """Run one search pipeline and return its scored memories."""
        scored = await self._scored_search(pipeline, limit, method=method)
        return tuple(MemoryMatch(memory=document, score=score) for document, score in scored)

    async def update(
        self,
        document: MemoryDocument,
        *,
        expected_revision: int,
    ) -> MemoryDocument:
        self._require_scope(document)
        return await self._update(
            document,
            expected_revision=expected_revision,
            filters=self._scope_filter,
        )

    async def delete(self, document_id: ObjectId, *, expected_revision: int) -> bool:
        return await self._delete(
            document_id,
            expected_revision=expected_revision,
            filters=self._scope_filter,
        )


def _validate_limit(limit: int) -> None:
    """Reject a result count outside the supported range."""
    if isinstance(limit, bool) or not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")


def _address_scope(
    project_id: str | None,
    client_id: str | None,
    requested: MemoryScope | None,
) -> MemoryScope:
    """Validate a repository's CRUD address without importing service policy."""
    scope: MemoryScope = (
        requested if requested is not None else ("global" if project_id is None else "project")
    )
    requires_project = scope in {"project", "project_agent"}
    requires_client = scope in {"agent", "project_agent"}
    if (project_id is not None) != requires_project or (client_id is not None) != requires_client:
        raise ValueError("memory repository address does not match its scope")
    return scope
