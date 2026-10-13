"""Explicit publisher adapters; optional network providers are disabled by default."""

from app.content.publishers.base import (
    PublicationReceipt,
    PublicationStatus,
    Publisher,
    PublisherConnection,
    PublisherError,
    PublisherRegistry,
)
from app.content.publishers.manual import ManualPublisher


def default_publisher_registry() -> PublisherRegistry:
    """Return only the no-network ManualPublisher for an unconfigured deployment."""
    return PublisherRegistry((ManualPublisher(),))


__all__ = [
    "ManualPublisher",
    "PublicationReceipt",
    "PublicationStatus",
    "Publisher",
    "PublisherConnection",
    "PublisherError",
    "PublisherRegistry",
    "default_publisher_registry",
]
