"""Scope-isolated MongoDB repository for retrieval logs."""

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.models.common import Page, validate_identifier
from app.models.retrieval_log import RetrievalLogDocument, RetrievalMethod
from app.repositories.base import DEFAULT_PAGE_SIZE, MongoRepository
from app.repositories.errors import ProjectIsolationError


class MongoRetrievalLogRepository(MongoRepository[RetrievalLogDocument]):
    """Persist immutable retrieval metadata within one project/global boundary."""

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        *,
        project_id: str | None,
    ) -> None:
        super().__init__(database, "retrieval_logs", RetrievalLogDocument)
        self.project_id = validate_identifier(project_id) if project_id is not None else None

    def _require_scope(self, document: RetrievalLogDocument) -> None:
        if document.project_id != self.project_id:
            raise ProjectIsolationError("retrieval log belongs to a different project scope")

    async def create(self, document: RetrievalLogDocument) -> RetrievalLogDocument:
        self._require_scope(document)
        return await self._create(document)

    async def get(self, document_id: ObjectId) -> RetrievalLogDocument | None:
        return await self._get({"_id": document_id, "project_id": self.project_id})

    async def list(
        self,
        *,
        method: RetrievalMethod | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[RetrievalLogDocument]:
        filters: MongoDocument = {"project_id": self.project_id}
        if method is not None:
            filters["retrieval_method"] = method
        return await self._page(filters, limit=limit, cursor=cursor)

    async def delete(self, document_id: ObjectId, *, expected_revision: int) -> bool:
        return await self._delete(
            document_id,
            expected_revision=expected_revision,
            filters={"project_id": self.project_id},
        )
