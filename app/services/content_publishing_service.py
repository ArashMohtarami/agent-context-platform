"""Explicit, approval-gated publication orchestration for stored content."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.content.errors import (
    ContentAlreadyPublishedError,
    ContentApprovalBlockedError,
    ContentPublishingStateError,
    PublisherDeliveryError,
    PublisherUnavailableError,
)
from app.content.publication import (
    PublicationSafetyPolicy,
    review_content_for_publication,
)
from app.content.publishers import PublicationReceipt, PublisherError, PublisherRegistry
from app.models.content import ContentItemDocument, ContentPublicationProvider, ContentStatus
from app.repositories.errors import ConcurrencyConflictError

logger = logging.getLogger("agent_context.content.publishing")


@dataclass(frozen=True, slots=True)
class ContentPublishOutcome:
    """A persisted receipt and whether a retry reused an existing publication."""

    item: ContentItemDocument
    receipt: PublicationReceipt
    already_published: bool


class ContentPublishingStore(Protocol):
    """The lifecycle-service surface required for explicit publication."""

    async def get_content(self, profile_id: str, content_id: str) -> ContentItemDocument: ...

    async def mark_content_published(
        self,
        profile_id: str,
        content_id: str,
        *,
        expected_revision: int,
        provider: ContentPublicationProvider,
        external_id: str | None,
        external_url: str | None,
        published_at: datetime,
        idempotency_key: str,
    ) -> ContentItemDocument: ...


class ContentPublishingService:
    """Run a fresh safety check then record one explicit provider action."""

    def __init__(
        self,
        content: ContentPublishingStore,
        *,
        policy: PublicationSafetyPolicy,
        publishers: PublisherRegistry,
    ) -> None:
        self._content = content
        self._policy = policy
        self._publishers = publishers

    async def publish_content(
        self,
        profile_id: str,
        content_id: str,
        *,
        expected_revision: int,
        provider: ContentPublicationProvider,
        idempotency_key: str | None,
    ) -> ContentPublishOutcome:
        """Publish an approved current representation, never arbitrary supplied prose."""
        item = await self._content.get_content(profile_id, content_id)
        key = idempotency_key or f"publish:{content_id}:{item.current_version}"
        if item.revision != expected_revision:
            raise ContentPublishingStateError("content item changed after it was read")
        if item.status is ContentStatus.PUBLISHED:
            if item.publication_provider is provider and item.publication_idempotency_key == key:
                return self._existing_outcome(item)
            raise ContentAlreadyPublishedError(
                "the current content representation has already been published"
            )
        if item.status is not ContentStatus.APPROVED:
            raise ContentPublishingStateError("content must be approved before it can be published")

        review = review_content_for_publication(item, policy=self._policy)
        if not review.publishable:
            raise ContentApprovalBlockedError(
                "publishing is blocked by high-severity publication safety findings"
            )
        publisher = self._publishers.get(provider)
        if not publisher.supports_idempotency:
            raise PublisherUnavailableError(
                "the requested publication provider does not support safe retries"
            )
        try:
            receipt = await publisher.publish(item, idempotency_key=key)
        except PublisherError as error:
            logger.warning(
                "content publication provider failed",
                extra={"provider": provider.value},
            )
            raise PublisherDeliveryError("the publication provider failed") from error
        if receipt.provider is not provider or receipt.idempotency_key != key:
            logger.warning("content publication provider returned an invalid receipt")
            raise PublisherDeliveryError("the publication provider returned an invalid receipt")

        try:
            persisted = await self._content.mark_content_published(
                profile_id,
                content_id,
                expected_revision=expected_revision,
                provider=receipt.provider,
                external_id=receipt.external_id,
                external_url=receipt.external_url,
                published_at=receipt.published_at,
                idempotency_key=receipt.idempotency_key,
            )
        except ConcurrencyConflictError as error:
            latest = await self._content.get_content(profile_id, content_id)
            if (
                latest.status is ContentStatus.PUBLISHED
                and latest.publication_provider is provider
                and latest.publication_idempotency_key == key
            ):
                return self._existing_outcome(latest)
            raise ContentPublishingStateError(
                "content changed while publication was being recorded; retry with a fresh read"
            ) from error
        return ContentPublishOutcome(item=persisted, receipt=receipt, already_published=False)

    @staticmethod
    def _existing_outcome(item: ContentItemDocument) -> ContentPublishOutcome:
        """Turn an idempotent retry into the durable receipt already on the item."""
        if item.published_at is None or item.publication_provider is None:
            raise ContentPublishingStateError(
                "published content has no reusable publication receipt"
            )
        if item.publication_idempotency_key is None:
            raise ContentPublishingStateError("published content has no idempotency key")
        return ContentPublishOutcome(
            item=item,
            receipt=PublicationReceipt(
                provider=item.publication_provider,
                external_id=item.external_id,
                external_url=item.external_url,
                published_at=item.published_at,
                idempotency_key=item.publication_idempotency_key,
            ),
            already_published=True,
        )
