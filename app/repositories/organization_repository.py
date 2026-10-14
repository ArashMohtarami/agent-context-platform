"""Operator-only organization registry; no public provisioning interface."""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.models.organization import OrganizationDocument
from app.security.tenant import TenantContext, tenant_scope


class MongoOrganizationRepository:
    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        self._collection = database["organizations"]

    async def create(self, organization: OrganizationDocument) -> OrganizationDocument:
        await self._collection.insert_one(organization.model_dump(by_alias=True, mode="python"))
        return organization

    async def get(self, org_id: str) -> OrganizationDocument | None:
        raw = await self._collection.find_one({"org_id": org_id})
        if raw is None:
            return None
        # Registry entries are installation metadata; operator inspection may
        # examine a different sealed org while checking whole-DB closure.
        with tenant_scope(TenantContext(org_id)):
            return OrganizationDocument.model_validate(raw)
