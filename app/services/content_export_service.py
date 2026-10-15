"""V2 Phase 7 manual content export orchestration.

This service selects a platform adapter for an already-authorized current
content item.  It never persists a change, creates a content version, calls an
external provider, or treats formatting as content generation.
"""

from collections.abc import Mapping

from app.content.platforms.base import (
    ContentPlatform,
    ExportMode,
    PlatformExport,
    UnsupportedContentPlatformError,
)
from app.models.content import ContentItemDocument
from app.models.content import ContentPlatform as ContentPlatformName
from app.security.tenant import TenantContextRequired, require_tenant
from app.security.tenant_policy import policy_allows
from app.security.tenant_service import tenant_service


@tenant_service
class ContentExportService:
    """Return platform-specific manual export representations without side effects."""

    def __init__(self, platforms: Mapping[ContentPlatformName, ContentPlatform]) -> None:
        self._platforms = platforms

    def export_content(self, content: ContentItemDocument, *, mode: ExportMode) -> PlatformExport:
        """Validate and format current agent-authored text for its stored platform."""
        if content.org_id != require_tenant().org_id or not policy_allows(
            content.model_dump(by_alias=True, mode="python"), "content_items"
        ):
            raise TenantContextRequired("export content is unavailable in this tenant")
        platform = self._platforms.get(content.platform)
        if platform is None:
            raise UnsupportedContentPlatformError(
                f"content platform {content.platform.value!r} is not supported for export"
            )
        return platform.export(content, mode=mode)
