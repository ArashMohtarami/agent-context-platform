"""Phase 7 platform adapters used by the content export service."""

from collections.abc import Mapping
from types import MappingProxyType

from app.content.platforms.base import ContentPlatform
from app.content.platforms.github import GitHubPlatform
from app.content.platforms.linkedin import LinkedInPlatform
from app.content.platforms.medium import MediumPlatform
from app.models.content import ContentPlatform as ContentPlatformName

__all__ = [
    "ContentPlatform",
    "GitHubPlatform",
    "LinkedInPlatform",
    "MediumPlatform",
    "platform_services",
]


def platform_services(
    *, linkedin_max_characters: int | None = None
) -> Mapping[ContentPlatformName, ContentPlatform]:
    """Create all current adapters; adding a platform stays local to this registry."""
    services: dict[ContentPlatformName, ContentPlatform] = {
        ContentPlatformName.LINKEDIN: LinkedInPlatform(max_characters=linkedin_max_characters),
        ContentPlatformName.MEDIUM: MediumPlatform(),
        ContentPlatformName.GITHUB: GitHubPlatform(),
    }
    return MappingProxyType(services)
