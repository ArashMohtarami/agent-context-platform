"""GitHub Markdown validation and documentation-export representation."""

from types import MappingProxyType

from app.content.platforms.base import ExportMode, PlatformExport
from app.content.platforms.common import markdown_document, slug, validate_code_fences
from app.models.content import ContentItemDocument
from app.models.content import ContentPlatform as ContentPlatformName


class GitHubPlatform:
    """Export reproducible Markdown documents without touching a repository."""

    platform = ContentPlatformName.GITHUB

    def validate(self, content: ContentItemDocument) -> None:
        validate_code_fences(content.body)

    def format(self, content: ContentItemDocument) -> str:
        self.validate(content)
        return markdown_document(content.title, content.body)

    def export(self, content: ContentItemDocument, *, mode: ExportMode) -> PlatformExport:
        text = self.format(content)
        suggested_path = self._suggested_path(content)
        return PlatformExport(
            profile_id=content.profile_id,
            content_id=content.content_id,
            platform=self.platform,
            mode=mode,
            format="markdown",
            text=text,
            suggested_file_name=suggested_path.rsplit("/", maxsplit=1)[-1],
            suggested_path=suggested_path,
            metadata=MappingProxyType(
                {
                    "representation": "markdown",
                    "suggested_path": suggested_path,
                    "content_type": content.content_type,
                    "code_fences_validated": True,
                }
            ),
        )

    @staticmethod
    def _suggested_path(content: ContentItemDocument) -> str:
        kind = content.content_type.casefold()
        if kind in {"readme", "readme-md"}:
            return "README.md"
        name = f"{slug(content.title, fallback=content.content_id)}.md"
        if "adr" in kind:
            return f"docs/adr/{name}"
        return f"docs/{name}"
