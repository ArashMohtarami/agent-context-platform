"""Indexes for bounded gateway idempotency replay storage."""

from typing import Final

from pymongo import ASCENDING, IndexModel

from app.db.idempotency_schema import IDEMPOTENCY_COLLECTION

IDEMPOTENCY_STANDARD_INDEX_DEFINITIONS: Final[dict[str, tuple[IndexModel, ...]]] = {
    IDEMPOTENCY_COLLECTION: (
        IndexModel(
            [("client_id", ASCENDING), ("operation", ASCENDING), ("key_hash", ASCENDING)],
            unique=True,
            name="gateway_idempotency_client_operation_key_uq",
        ),
        # The date is computed from the deployment's configured retention;
        # MongoDB's TTL monitor removes records without a second scheduler.
        IndexModel(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
            name="gateway_idempotency_expiry_ttl",
        ),
    )
}
