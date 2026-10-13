"""V2 Phase 10 generic content-metric storage and retrieval.

Metric observations are descriptive, append-only data.  This service does not
collect provider analytics, infer strategy, mutate creator profiles, or change
content.  Future provider adapters can submit the same explicit observation
shape after a caller chooses to use them.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.content.errors import ContentItemNotFoundError, ContentMetricAlreadyExistsError
from app.creator.errors import CreatorProfileNotFoundError
from app.models.common import Page
from app.models.content import (
    ContentItemDocument,
    ContentMetricDocument,
    ContentPlatform,
    CreatorProfileDocument,
)
from app.repositories.errors import DuplicateDocumentError


class CreatorProfileRepository(Protocol):
    """Read the owner of a profile-scoped metric series."""

    async def get(self, profile_id: str) -> CreatorProfileDocument | None: ...


class ContentItemRepository(Protocol):
    """Read the content item that owns a metrics time series."""

    async def get(self, content_id: str) -> ContentItemDocument | None: ...


class ContentMetricRepository(Protocol):
    """Append and page immutable metric observations inside one profile."""

    async def create(self, document: ContentMetricDocument) -> ContentMetricDocument: ...

    async def list_for_content(
        self,
        *,
        content_id: str,
        platform: ContentPlatform | None = None,
        captured_after: datetime | None = None,
        captured_before: datetime | None = None,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[ContentMetricDocument]: ...


@dataclass(frozen=True, slots=True)
class ContentMetricHistory:
    """One authorized content item and its newest-first metric observations."""

    content: ContentItemDocument
    page: Page[ContentMetricDocument]


class ContentMetricsService:
    """Coordinate generic, profile-isolated metric observations.

    The service deliberately has no analytics client.  A source label and
    provider metadata describe where a manually supplied observation came from;
    they never cause an outbound request or automatic strategy change.
    """

    def __init__(
        self,
        profiles: CreatorProfileRepository,
        items: Callable[[str], ContentItemRepository],
        metrics: Callable[[str], ContentMetricRepository],
    ) -> None:
        self._profiles = profiles
        self._items = items
        self._metrics = metrics

    async def _require_profile(self, profile_id: str) -> CreatorProfileDocument:
        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise CreatorProfileNotFoundError("creator profile was not found")
        return profile

    async def _require_content(self, profile_id: str, content_id: str) -> ContentItemDocument:
        item = await self._items(profile_id).get(content_id)
        if item is None:
            raise ContentItemNotFoundError("content item was not found")
        return item

    async def create_metric(
        self,
        *,
        profile_id: str,
        metric_id: str,
        content_id: str,
        platform: ContentPlatform,
        captured_at: datetime,
        metrics: Mapping[str, float],
        source: str = "manual",
        provider_metadata: Mapping[str, object] | None = None,
    ) -> ContentMetricDocument:
        """Persist one explicitly supplied observation without changing its content."""
        await self._require_profile(profile_id)
        await self._require_content(profile_id, content_id)
        document = ContentMetricDocument(
            profile_id=profile_id,
            metric_id=metric_id,
            content_id=content_id,
            platform=platform,
            captured_at=captured_at,
            metrics=dict(metrics),
            source=source,
            provider_metadata=dict(provider_metadata or {}),
        )
        try:
            return await self._metrics(profile_id).create(document)
        except DuplicateDocumentError as error:
            raise ContentMetricAlreadyExistsError("content metric already exists") from error

    async def list_metrics(
        self,
        *,
        profile_id: str,
        content_id: str,
        platform: ContentPlatform | None = None,
        captured_after: datetime | None = None,
        captured_before: datetime | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ContentMetricHistory:
        """Return one content item's newest-first, cursor-paged metric history."""
        await self._require_profile(profile_id)
        item = await self._require_content(profile_id, content_id)
        if (
            captured_after is not None
            and captured_before is not None
            and captured_after > captured_before
        ):
            raise ValueError("captured_after must be on or before captured_before")
        page = await self._metrics(profile_id).list_for_content(
            content_id=content_id,
            platform=platform,
            captured_after=captured_after,
            captured_before=captured_before,
            limit=limit,
            cursor=cursor,
        )
        return ContentMetricHistory(content=item, page=page)
