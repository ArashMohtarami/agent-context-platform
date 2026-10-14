"""MongoDB persistence for V5's generic MCP client profiles."""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.models.client_profile import ClientProfileDocument
from app.models.common import Page
from app.repositories.base import DEFAULT_PAGE_SIZE, MongoRepository
from app.security.tenant import TenantContext


class MongoClientProfileRepository(MongoRepository[ClientProfileDocument]):
    """Persist reusable onboarding profiles independently of agent credentials."""

    def __init__(
        self, database: AsyncDatabase[MongoDocument], *, tenant: TenantContext | None = None
    ) -> None:
        super().__init__(database, "client_profiles", ClientProfileDocument, tenant=tenant)

    async def create(self, document: ClientProfileDocument) -> ClientProfileDocument:
        return await self._create(document)

    async def get(self, profile_id: str) -> ClientProfileDocument | None:
        return await self._get(self._identity_filter("client-profile", profile_id))

    async def list(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> Page[ClientProfileDocument]:
        return await self._page(limit=limit, cursor=cursor)

    async def update(
        self, document: ClientProfileDocument, *, expected_revision: int
    ) -> ClientProfileDocument:
        return await self._update(document, expected_revision=expected_revision)
