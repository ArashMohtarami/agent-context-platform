"""MongoDB persistence for client-scoped MCP idempotency records."""

from datetime import datetime

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.db.client_types import MongoDocument
from app.models.idempotency import IdempotencyRecordDocument, IdempotencyRecordStatus
from app.repositories.base import MongoRepository
from app.repositories.errors import DuplicateDocumentError


class MongoGatewayIdempotencyRepository(MongoRepository[IdempotencyRecordDocument]):
    """Store only hashed idempotency identities and bounded replay results."""

    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        super().__init__(database, "gateway_idempotency", IdempotencyRecordDocument)

    async def create(self, document: IdempotencyRecordDocument) -> IdempotencyRecordDocument:
        """Create a reservation, reporting a natural-key race to the service."""
        if document.identity_version == 1:
            raise ValueError("only adoption may insert legacy tenant identities")
        document = self._validate(document.model_dump(by_alias=True, mode="python"))
        payload = document.model_dump(by_alias=True, mode="python")
        try:
            await self._collection.insert_one(payload)
        except DuplicateKeyError as error:
            raise DuplicateDocumentError("an idempotency reservation already exists") from error
        return document

    async def get(
        self, *, client_id: str, operation: str, key_hash: str
    ) -> IdempotencyRecordDocument | None:
        return await self._get(
            {"client_id": client_id, "operation": operation, "key_hash": key_hash}
        )

    async def complete(
        self,
        document: IdempotencyRecordDocument,
        *,
        result_text: str,
        result_data: dict[str, object],
    ) -> IdempotencyRecordDocument | None:
        """Attach a result only to this still-pending reservation."""
        raw = await self._collection.find_one_and_update(
            self._owned_filter(
                {
                    "_id": document.id,
                    "payload_hash": document.payload_hash,
                    "status": IdempotencyRecordStatus.IN_PROGRESS.value,
                }
            ),
            {
                "$set": {
                    "status": IdempotencyRecordStatus.COMPLETED.value,
                    "result_text": result_text,
                    "result_data": result_data,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return None if raw is None else self._validate(raw)

    async def abandon(self, document: IdempotencyRecordDocument) -> None:
        """Release a known-safe failed attempt before it has produced a result."""
        await self._collection.delete_one(
            self._owned_filter(
                {
                    "_id": document.id,
                    "payload_hash": document.payload_hash,
                    "status": IdempotencyRecordStatus.IN_PROGRESS.value,
                }
            )
        )

    async def prune_expired(self, now: datetime) -> None:
        """Bound storage promptly; the TTL index remains the crash-safe backstop."""
        await self._collection.delete_many(self._owned_filter({"expires_at": {"$lte": now}}))
