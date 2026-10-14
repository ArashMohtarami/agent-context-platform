"""MongoDB repository for projects."""

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.models.common import Page
from app.models.project import ProjectDocument
from app.repositories.base import DEFAULT_PAGE_SIZE, MongoRepository
from app.security.tenant import TenantContext


class MongoProjectRepository(MongoRepository[ProjectDocument]):
    """Persist project boundaries by deterministic project ID."""

    def __init__(
        self, database: AsyncDatabase[MongoDocument], *, tenant: TenantContext | None = None
    ) -> None:
        super().__init__(database, "projects", ProjectDocument, tenant=tenant)

    async def create(self, document: ProjectDocument) -> ProjectDocument:
        return await self._create(document)

    async def get(self, project_id: str) -> ProjectDocument | None:
        return await self._get(self._identity_filter("project", project_id))

    async def list(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ProjectDocument]:
        return await self._page(limit=limit, cursor=cursor)

    async def update(
        self,
        document: ProjectDocument,
        *,
        expected_revision: int,
    ) -> ProjectDocument:
        return await self._update(document, expected_revision=expected_revision)

    async def delete(self, project_id: str, *, expected_revision: int) -> bool:
        document_id: ObjectId = await self._identity_record_id("project", project_id)
        return await self._delete(document_id, expected_revision=expected_revision)
