"""Platform formatting contracts for V2 Phase 7 manual export.

``app.models.content.ContentPlatform`` remains the persisted destination enum.
This module deliberately uses the same name in its own namespace for the
adapter protocol requested by V2; importing it never changes stored values or
the existing public model API.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from app.models.content import ContentItemDocument
from app.models.content import ContentPlatform as ContentPlatformName

ExportMode = Literal["stdout", "file", "markdown"]
ExportFormat = Literal["plain_text", "markdown"]


class ContentPlatformValidationError(ValueError):
    """The agent-supplied content cannot be represented safely on this platform."""


class UnsupportedContentPlatformError(ValueError):
    """No Phase 7 formatter has been registered for the stored destination."""


@dataclass(frozen=True, slots=True)
class PlatformExport:
    """A manual export representation; it is never a publish request."""

    profile_id: str
    content_id: str
    platform: ContentPlatformName
    mode: ExportMode
    format: ExportFormat
    text: str
    suggested_file_name: str
    suggested_path: str | None
    metadata: Mapping[str, object]


class ContentPlatform(Protocol):
    """Validate and format agent-authored content without generating prose."""

    platform: ContentPlatformName

    def validate(self, content: ContentItemDocument) -> None:
        """Reject an unsupported representation before it can be exported."""

    def format(self, content: ContentItemDocument) -> str:
        """Return the platform representation while preserving supplied prose."""

    def export(self, content: ContentItemDocument, *, mode: ExportMode) -> PlatformExport:
        """Build a manual representation with no external side effect."""
