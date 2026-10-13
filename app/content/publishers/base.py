"""Provider contracts for explicit creator-content publication."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.content.errors import PublisherUnavailableError
from app.models.content import ContentItemDocument, ContentPublicationProvider


class PublisherError(RuntimeError):
    """A provider failed without details that may safely be shown to a caller."""


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    """The minimal durable result returned after one explicit publish request."""

    provider: ContentPublicationProvider
    external_id: str | None
    external_url: str | None
    published_at: datetime
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class PublisherConnection:
    """A redacted provider availability check, never a credential report."""

    provider: ContentPublicationProvider
    ready: bool


@dataclass(frozen=True, slots=True)
class PublicationStatus:
    """A provider's redacted state for a previously recorded receipt."""

    provider: ContentPublicationProvider
    idempotency_key: str
    published: bool
    external_id: str | None


@runtime_checkable
class Publisher(Protocol):
    """An optional, explicitly configured delivery provider.

    Implementations receive an existing stored item only. They must never
    accept arbitrary prose, log credentials, or silently publish during a
    create/update operation.
    """

    provider: ContentPublicationProvider
    supports_idempotency: bool

    async def publish(
        self, item: ContentItemDocument, *, idempotency_key: str
    ) -> PublicationReceipt:
        """Perform one provider-specific explicit delivery."""
        ...

    async def validate_connection(self) -> PublisherConnection:
        """Report whether this provider is ready without exposing credentials."""
        ...

    async def get_publication_status(
        self, *, external_id: str | None, idempotency_key: str
    ) -> PublicationStatus:
        """Return redacted status for a persisted provider receipt."""
        ...


class PublisherRegistry:
    """Resolve only deliberately configured publisher implementations."""

    def __init__(self, publishers: tuple[Publisher, ...]) -> None:
        by_provider: dict[ContentPublicationProvider, Publisher] = {}
        for publisher in publishers:
            if publisher.provider in by_provider:
                raise ValueError(f"duplicate publisher configuration: {publisher.provider}")
            by_provider[publisher.provider] = publisher
        self._by_provider = by_provider

    def get(self, provider: ContentPublicationProvider) -> Publisher:
        """Return the configured provider or a safe, credential-free failure."""
        try:
            return self._by_provider[provider]
        except KeyError as error:
            raise PublisherUnavailableError(
                "the requested publication provider is not configured"
            ) from error
