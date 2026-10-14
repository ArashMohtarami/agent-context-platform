"""MongoDB repository for the audit trail.

Audit records are written and read, never updated or deleted: a trail that can
be edited is not a trail. Nothing here exposes a way to change one.
"""

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.models.audit_log import AuditLogDocument, AuditOutcome
from app.models.common import Page
from app.repositories.base import DEFAULT_PAGE_SIZE, MongoRepository
from app.security.tenant import TenantContext


class MongoAuditRepository(MongoRepository[AuditLogDocument]):
    """Append to, and read back, the record of authorization decisions."""

    def __init__(
        self, database: AsyncDatabase[MongoDocument], *, tenant: TenantContext | None = None
    ) -> None:
        super().__init__(database, "audit_logs", AuditLogDocument, tenant=tenant)

    async def create(self, document: AuditLogDocument) -> AuditLogDocument:
        """Append one decision."""
        return await self._create(document)

    async def list(
        self,
        *,
        key_id: str | None = None,
        outcome: AuditOutcome | None = None,
        project_id: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[AuditLogDocument]:
        """Read the trail, filtered by who, what happened, or where."""
        filters: MongoDocument = {}
        if key_id is not None:
            filters["key_id"] = key_id
        if outcome is not None:
            filters["outcome"] = outcome
        if project_id is not None:
            filters["project_id"] = project_id
        return await self._page(filters, limit=limit, cursor=cursor)
