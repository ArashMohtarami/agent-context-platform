"""The safe default provider for a human-completed external publication."""

from app.content.publishers.base import (
    PublicationReceipt,
    PublicationStatus,
    PublisherConnection,
)
from app.models.common import utc_now
from app.models.content import ContentItemDocument, ContentPublicationProvider


class ManualPublisher:
    """Record an explicit human-confirmed publication without making a network call.

    This provider never receives credentials and never sends content anywhere.
    It is useful when a human pastes an approved export into a platform and
    then deliberately records that completed action in the platform.
    """

    provider = ContentPublicationProvider.MANUAL
    supports_idempotency = True

    async def publish(
        self, item: ContentItemDocument, *, idempotency_key: str
    ) -> PublicationReceipt:
        """Return a durable local receipt for this explicit manual action."""
        _ = item
        return PublicationReceipt(
            provider=self.provider,
            external_id=None,
            external_url=None,
            published_at=utc_now(),
            idempotency_key=idempotency_key,
        )

    async def validate_connection(self) -> PublisherConnection:
        """Manual recording is always available and needs no configuration."""
        return PublisherConnection(provider=self.provider, ready=True)

    async def get_publication_status(
        self, *, external_id: str | None, idempotency_key: str
    ) -> PublicationStatus:
        """Report a locally recorded manual receipt without an external lookup."""
        return PublicationStatus(
            provider=self.provider,
            idempotency_key=idempotency_key,
            published=True,
            external_id=external_id,
        )
