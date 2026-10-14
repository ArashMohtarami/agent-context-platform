"""MongoDB repository for API keys.

Lookup is by the public identifier and nothing else. There is no method here
that takes a secret, a hash, or a caller-supplied filter, so no request value
can become part of a query shape.
"""

from datetime import datetime

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.models.api_key import ApiKeyDocument, KeyStatus
from app.models.common import Page
from app.repositories.base import DEFAULT_PAGE_SIZE, MongoRepository
from app.security.tenant import TenantContext


class MongoApiKeyRepository(MongoRepository[ApiKeyDocument]):
    """Read and write issued keys, without ever handling a secret."""

    def __init__(
        self, database: AsyncDatabase[MongoDocument], *, tenant: TenantContext | None = None
    ) -> None:
        super().__init__(database, "api_keys", ApiKeyDocument, tenant=tenant)

    async def create(self, document: ApiKeyDocument) -> ApiKeyDocument:
        """Store one newly issued key."""
        return await self._create(document)

    async def get_by_key_id(self, key_id: str) -> ApiKeyDocument | None:
        """Load one key by its public identifier.

        The identifier is validated as hexadecimal before it reaches here, so
        what is passed into the filter is a fixed-shape string rather than
        whatever a caller sent in a header.
        """
        return await self._get({"key_id": key_id})

    async def get(self, document_id: ObjectId) -> ApiKeyDocument | None:
        """Load one key by its document identifier."""
        return await self._get({"_id": document_id})

    async def list(
        self,
        *,
        status: KeyStatus | None = None,
        client_id: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ApiKeyDocument]:
        """List issued keys using bounded cursor pagination."""
        filters: MongoDocument = {}
        if status is not None:
            filters["status"] = status
        if client_id is not None:
            filters["client_id"] = client_id
        return await self._page(filters, limit=limit, cursor=cursor)

    async def update(
        self,
        document: ApiKeyDocument,
        *,
        expected_revision: int,
    ) -> ApiKeyDocument:
        """Apply a concurrency-safe change, such as a revocation."""
        return await self._update(document, expected_revision=expected_revision)

    async def touch(self, key_id: str, used_at: datetime) -> None:
        """Record that a key was used, without reading it back.

        Deliberately not revisioned and deliberately not awaited by the request
        that triggered it: a usage timestamp is an observation, and losing one
        to a concurrent write is preferable to failing an authenticated request
        or making every request pay for a second round trip's consistency.
        """
        await self._collection.update_one(
            {"key_id": key_id},
            {"$set": {"last_used_at": used_at}},
        )
