"""Strict storage contract for short-lived MCP idempotency replay records."""

from typing import Final

from app.db.agent_schema import DATE, IDENTIFIER, document_schema
from app.db.client_types import MongoDocument
from app.models.idempotency import IdempotencyRecordStatus

IDEMPOTENCY_COLLECTION: Final = "gateway_idempotency"

IDEMPOTENCY_COLLECTION_VALIDATORS: Final[dict[str, MongoDocument]] = {
    IDEMPOTENCY_COLLECTION: document_schema(
        [
            "client_id",
            "operation",
            "key_hash",
            "payload_hash",
            "status",
            "result_text",
            "result_data",
            "expires_at",
        ],
        {
            "client_id": IDENTIFIER,
            "operation": IDENTIFIER,
            "key_hash": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
            "payload_hash": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
            "status": {"enum": [value.value for value in IdempotencyRecordStatus]},
            "result_text": {"bsonType": ["string", "null"], "maxLength": 1_000_000},
            "result_data": {"bsonType": ["object", "null"]},
            "expires_at": DATE,
        },
    )
}
