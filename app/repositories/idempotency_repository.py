"""MongoDB persistence for client-scoped MCP idempotency records."""

from datetime import datetime

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.db.client_types import MongoDocument
from app.models.idempotency import IdempotencyRecordDocument, IdempotencyRecordStatus
from app.repositories.errors import DuplicateDocumentError


class MongoGatewayIdempotencyRepository:
    """Store only hashed idempotency identities and bounded replay results."""

    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        self._collection = database["gateway_idempotency"]

    async def create(self, document: IdempotencyRecordDocument) -> IdempotencyRecordDocument:
        """Create a reservation, reporting a natural-key race to the service."""
        payload = document.model_dump(by_alias=True, mode="python")
        try:
            await self._collection.insert_one(payload)
        except DuplicateKeyError as error:
            raise DuplicateDocumentError("an idempotency reservation already exists") from error
        return document

    async def get(
        self, *, client_id: str, operation: str, key_hash: str
    ) -> IdempotencyRecordDocument | None:
        raw = await self._collection.find_one(
            {"client_id": client_id, "operation": operation, "key_hash": key_hash}
        )
        return None if raw is None else IdempotencyRecordDocument.model_validate(raw)

    async def complete(
        self,
        document: IdempotencyRecordDocument,
        *,
        result_text: str,
        result_data: dict[str, object],
    ) -> IdempotencyRecordDocument | None:
        """Attach a result only to this still-pending reservation."""
        raw = await self._collection.find_one_and_update(
            {
                "_id": document.id,
                "payload_hash": document.payload_hash,
                "status": IdempotencyRecordStatus.IN_PROGRESS.value,
            },
            {
                "$set": {
                    "status": IdempotencyRecordStatus.COMPLETED.value,
                    "result_text": result_text,
                    "result_data": result_data,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return None if raw is None else IdempotencyRecordDocument.model_validate(raw)

    async def abandon(self, document: IdempotencyRecordDocument) -> None:
        """Release a known-safe failed attempt before it has produced a result."""
        await self._collection.delete_one(
            {
                "_id": document.id,
                "payload_hash": document.payload_hash,
                "status": IdempotencyRecordStatus.IN_PROGRESS.value,
            }
        )

    async def prune_expired(self, now: datetime) -> None:
        """Bound storage promptly; the TTL index remains the crash-safe backstop."""
        await self._collection.delete_many({"expires_at": {"$lte": now}})
