"""Medium Markdown validation and long-form manual export representation."""

from types import MappingProxyType

from app.content.platforms.base import ExportMode, PlatformExport
from app.content.platforms.common import markdown_document, slug, validate_code_fences
from app.models.content import ContentItemDocument
from app.models.content import ContentPlatform as ContentPlatformName


class MediumPlatform:
    """Preserve Markdown headings, links, and code blocks for article export."""

    platform = ContentPlatformName.MEDIUM

    def validate(self, content: ContentItemDocument) -> None:
        validate_code_fences(content.body)

    def format(self, content: ContentItemDocument) -> str:
        self.validate(content)
        return markdown_document(content.title, content.body)

    def export(self, content: ContentItemDocument, *, mode: ExportMode) -> PlatformExport:
        text = self.format(content)
        article_slug = slug(content.title, fallback=content.content_id)
        return PlatformExport(
            profile_id=content.profile_id,
            content_id=content.content_id,
            platform=self.platform,
            mode=mode,
            format="markdown",
            text=text,
            suggested_file_name=f"{article_slug}.md",
            suggested_path=None,
            metadata=MappingProxyType(
                {
                    "representation": "markdown",
                    "article_title": " ".join(content.title.splitlines()).strip(),
                    "article_slug": article_slug,
                    "tags": tuple(content.tags),
                    "topic_tags": tuple(content.topic_tags),
                    "code_fences_validated": True,
                }
            ),
        )
