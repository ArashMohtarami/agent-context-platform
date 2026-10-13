"""V2 Phase 7 deterministic platform-format and manual-export behavior."""

import pytest

from app.content.platforms import GitHubPlatform, LinkedInPlatform, MediumPlatform
from app.content.platforms.base import (
    ContentPlatformValidationError,
    UnsupportedContentPlatformError,
)
from app.models.content import ContentItemDocument, ContentPlatform
from app.services.content_export_service import ContentExportService


def content_item(
    platform: ContentPlatform | str,
    *,
    body: str = "A current, agent-authored draft.",
    title: str = "Idempotency in payment retries",
    content_type: str = "post",
) -> ContentItemDocument:
    """Build an existing V2 record; platform adapters must not mutate it."""
    return ContentItemDocument(
        profile_id="personal-brand",
        content_id="idempotency-draft",
        platform=platform,
        content_type=content_type,
        title=title,
        body=body,
        tags=["payments"],
        topic_tags=["idempotency"],
    )


def test_linkedin_export_preserves_unicode_and_uses_readable_blank_paragraphs() -> None:
    item = content_item(
        "linkedin",
        body="سلام 👋  \r\nA practical retry lesson.\r\n\r\n\r\nUse a stable request key.",
    )

    exported = LinkedInPlatform().export(item, mode="stdout")

    assert exported.format == "plain_text"
    assert exported.text == "سلام 👋\nA practical retry lesson.\n\nUse a stable request key.\n"
    assert exported.metadata["paragraph_separator"] == "blank_line"
    assert exported.metadata["character_count"] == len(exported.text.rstrip("\n"))
    assert item.body.startswith("سلام 👋")


def test_linkedin_length_validation_is_configurable_without_an_external_provider() -> None:
    item = content_item("linkedin", body="x" * 25)

    with pytest.raises(ContentPlatformValidationError, match="configured LinkedIn character"):
        LinkedInPlatform(max_characters=24).export(item, mode="markdown")


def test_medium_export_preserves_markdown_headings_links_and_code_blocks() -> None:
    item = content_item(
        "medium",
        body=(
            "## Context\n\n"
            "See the [retry guide](https://example.test/retries).\n\n"
            "```python\n"
            "def retry() -> None:\n"
            "    return None\n"
            "```"
        ),
    )

    exported = MediumPlatform().export(item, mode="markdown")

    assert exported.format == "markdown"
    assert exported.text.startswith("# Idempotency in payment retries\n\n## Context\n")
    assert "[retry guide](https://example.test/retries)" in exported.text
    assert "```python\ndef retry() -> None:\n    return None\n```" in exported.text
    assert exported.metadata["article_slug"] == "idempotency-in-payment-retries"
    assert exported.suggested_file_name == "idempotency-in-payment-retries.md"


def test_github_export_suggests_readme_and_preserves_reproducible_code_fences() -> None:
    item = content_item(
        "github",
        content_type="readme",
        body="Install it:\n\n```bash\npip install payment-retries\n```",
    )

    exported = GitHubPlatform().export(item, mode="file")

    assert exported.format == "markdown"
    assert exported.suggested_path == "README.md"
    assert exported.suggested_file_name == "README.md"
    assert "```bash\npip install payment-retries\n```" in exported.text
    assert exported.metadata["code_fences_validated"] is True


def test_markdown_export_rejects_an_unclosed_code_fence() -> None:
    item = content_item("github", body="```bash\npip install incomplete")

    with pytest.raises(ContentPlatformValidationError, match="code fences must be closed"):
        GitHubPlatform().export(item, mode="stdout")


def test_export_service_reports_an_unregistered_platform_without_publishing() -> None:
    item = content_item("medium")

    with pytest.raises(UnsupportedContentPlatformError, match="not supported for export"):
        ContentExportService({}).export_content(item, mode="stdout")


def test_medium_long_content_is_not_truncated_or_published() -> None:
    body = "paragraph " * 15_000
    item = content_item("medium", body=body)

    exported = MediumPlatform().export(item, mode="markdown")

    assert exported.text.endswith(body.rstrip() + "\n")
    assert len(exported.text) > len(body)
    assert item.status.value == "drafting"
    assert item.external_url is None
