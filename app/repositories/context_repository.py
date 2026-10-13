"""Project-isolated MongoDB repository for durable context."""

from collections.abc import Sequence
from datetime import datetime

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.context_indexes import (
    CONTEXT_TEXT_INDEX,
    CONTEXT_VECTOR_INDEX,
    ContextQueryFilters,
    context_filter_clauses,
    context_text_query,
    context_vector_filter,
)
from app.db.vector_indexes import VECTOR_PATH
from app.models.common import Page, validate_identifier
from app.models.project_context import ContextType, ProjectContextDocument
from app.models.skill import LifecycleStatus
from app.projects.types import ProjectContextMatch
from app.repositories.base import (
    DEFAULT_PAGE_SIZE,
    SCORE_FIELD,
    MongoRepository,
    SearchMethod,
)
from app.repositories.errors import ProjectIsolationError
from app.repositories.vectors import validated_vector

DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50
MAX_NUM_CANDIDATES = 10_000


class MongoProjectContextRepository(MongoRepository[ProjectContextDocument]):
    """Bind every context operation to one immutable project scope.

    The project is fixed when the repository is built, so no call site can
    widen it: reads, writes, and searches are all restricted to it, and a query
    naming a different project is refused rather than quietly answered.
    """

    def __init__(self, database: AsyncDatabase[MongoDocument], project_id: str) -> None:
        super().__init__(database, "project_context", ProjectContextDocument)
        self.project_id = validate_identifier(project_id)

    def _require_scope(self, document: ProjectContextDocument) -> None:
        if document.project_id != self.project_id:
            raise ProjectIsolationError("document belongs to a different project")

    def _require_query_scope(self, filters: ContextQueryFilters) -> None:
        if filters.project_id != self.project_id:
            raise ProjectIsolationError("query belongs to a different project")

    async def create(self, document: ProjectContextDocument) -> ProjectContextDocument:
        self._require_scope(document)
        return await self._create(document)

    async def get(self, document_id: ObjectId) -> ProjectContextDocument | None:
        return await self._get({"_id": document_id, "project_id": self.project_id})

    async def list(
        self,
        *,
        context_type: ContextType | None = None,
        status: LifecycleStatus | None = None,
        tags: Sequence[str] = (),
        minimum_importance: float = 0.0,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ProjectContextDocument]:
        filters: MongoDocument = {"project_id": self.project_id}
        if context_type is not None:
            filters["type"] = context_type
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
    ) -> tuple[ProjectContextDocument, ...]:
        """Return active project context in newest-update-first order."""
        return await self._recent(
            {"project_id": self.project_id, "status": "active"},
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
        filters: ContextQueryFilters,
    ) -> tuple[ProjectContextMatch, ...]:
        """Return the most similar context in this project, with scores.

        Every restriction, the project included, is applied inside the
        approximate search, so filtering never silently returns fewer results
        than were asked for.
        """
        self._require_query_scope(filters)
        query_vector = validated_vector(embedding)
        _validate_limit(limit)
        if isinstance(num_candidates, bool) or not limit <= num_candidates <= MAX_NUM_CANDIDATES:
            raise ValueError(f"num_candidates must be between limit and {MAX_NUM_CANDIDATES}")
        pipeline: list[MongoDocument] = [
            {
                "$vectorSearch": {
                    "index": CONTEXT_VECTOR_INDEX,
                    "path": VECTOR_PATH,
                    "queryVector": query_vector,
                    "numCandidates": num_candidates,
                    "limit": limit,
                    "filter": context_vector_filter(filters),
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
        filters: ContextQueryFilters,
    ) -> tuple[ProjectContextMatch, ...]:
        """Return the context in this project matching a lexical query."""
        self._require_query_scope(filters)
        _validate_limit(limit)
        compound = {
            **context_text_query(query, phrase=phrase),
            "filter": context_filter_clauses(filters),
        }
        pipeline: list[MongoDocument] = [
            {"$search": {"index": CONTEXT_TEXT_INDEX, "compound": compound}},
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
    ) -> tuple[ProjectContextMatch, ...]:
        """Run one search pipeline and return its scored context documents."""
        scored = await self._scored_search(pipeline, limit, method=method)
        return tuple(
            ProjectContextMatch(context=document, score=score) for document, score in scored
        )

    async def update(
        self,
        document: ProjectContextDocument,
        *,
        expected_revision: int,
    ) -> ProjectContextDocument:
        self._require_scope(document)
        return await self._update(
            document,
            expected_revision=expected_revision,
            filters={"project_id": self.project_id},
        )

    async def delete(self, document_id: ObjectId, *, expected_revision: int) -> bool:
        return await self._delete(
            document_id,
            expected_revision=expected_revision,
            filters={"project_id": self.project_id},
        )


def _validate_limit(limit: int) -> None:
    """Reject a result count outside the supported range."""
    if isinstance(limit, bool) or not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
