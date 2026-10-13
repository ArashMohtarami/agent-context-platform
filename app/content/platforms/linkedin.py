"""LinkedIn plain-text validation and manual export representation."""

from types import MappingProxyType

from app.content.platforms.base import (
    ContentPlatformValidationError,
    ExportMode,
    PlatformExport,
)
from app.content.platforms.common import normalize_text, slug
from app.models.content import ContentItemDocument
from app.models.content import ContentPlatform as ContentPlatformName


class LinkedInPlatform:
    """Format readable plain text; core wording always remains agent-supplied."""

    platform = ContentPlatformName.LINKEDIN

    def __init__(self, *, max_characters: int | None = None) -> None:
        if max_characters is not None and max_characters < 1:
            raise ValueError("LinkedIn maximum characters must be positive when configured")
        self._max_characters = max_characters

    def validate(self, content: ContentItemDocument) -> None:
        text = self.format(content)
        if self._max_characters is not None and len(text.rstrip("\n")) > self._max_characters:
            raise ContentPlatformValidationError(
                "content exceeds the configured LinkedIn character limit"
            )

    def format(self, content: ContentItemDocument) -> str:
        """Normalize paragraph spacing while retaining text and Unicode verbatim."""
        body = content.body.replace("\r\n", "\n").replace("\r", "\n")
        paragraphs = [
            "\n".join(line.rstrip() for line in part.splitlines()).strip()
            for part in body.split("\n\n")
        ]
        return normalize_text("\n\n".join(part for part in paragraphs if part))

    def export(self, content: ContentItemDocument, *, mode: ExportMode) -> PlatformExport:
        self.validate(content)
        text = self.format(content)
        return PlatformExport(
            profile_id=content.profile_id,
            content_id=content.content_id,
            platform=self.platform,
            mode=mode,
            format="plain_text",
            text=text,
            suggested_file_name=f"{slug(content.title, fallback=content.content_id)}.txt",
            suggested_path=None,
            metadata=MappingProxyType(
                {
                    "representation": "plain_text",
                    "paragraph_separator": "blank_line",
                    "character_count": len(text.rstrip("\n")),
                    "max_characters": self._max_characters,
                }
            ),
        )
