"""Project-isolated MongoDB repository for conversation history."""

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.models.common import Page, validate_identifier
from app.models.conversation import ConversationDocument
from app.repositories.base import DEFAULT_PAGE_SIZE, MongoRepository
from app.repositories.errors import ProjectIsolationError
from app.security.tenant import TenantContext


class MongoConversationRepository(MongoRepository[ConversationDocument]):
    """Keep every conversation operation within one project."""

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        project_id: str,
        *,
        tenant: TenantContext | None = None,
    ) -> None:
        super().__init__(database, "conversations", ConversationDocument, tenant=tenant)
        self.project_id = validate_identifier(project_id)

    def _require_scope(self, document: ConversationDocument) -> None:
        if document.project_id != self.project_id:
            raise ProjectIsolationError("conversation belongs to a different project")

    async def create(self, document: ConversationDocument) -> ConversationDocument:
        self._require_scope(document)
        return await self._create(document)

    async def get(self, document_id: ObjectId) -> ConversationDocument | None:
        return await self._get({"_id": document_id, "project_id": self.project_id})

    async def list(
        self,
        *,
        session_id: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ConversationDocument]:
        filters: MongoDocument = {"project_id": self.project_id}
        if session_id is not None:
            filters["session_id"] = session_id
        return await self._page(filters, limit=limit, cursor=cursor)

    async def update(
        self,
        document: ConversationDocument,
        *,
        expected_revision: int,
    ) -> ConversationDocument:
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
