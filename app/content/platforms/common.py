"""Shared deterministic formatting helpers for Phase 7 platform adapters."""

import re

from app.content.platforms.base import ContentPlatformValidationError

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$", re.MULTILINE)
_NON_SLUG = re.compile(r"[^a-z0-9]+")


def normalize_text(value: str) -> str:
    """Use stable Unix newlines without rewriting the supplied prose."""
    return value.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"


def validate_code_fences(value: str) -> None:
    """Require paired Markdown fences while leaving fenced content untouched."""
    open_fence: str | None = None
    for match in _FENCE.finditer(value):
        fence = match.group(1)
        if open_fence is None:
            open_fence = fence
        elif (
            fence[0] == open_fence[0]
            and len(fence) >= len(open_fence)
            and not match.group(2).strip()
        ):
            open_fence = None
    if open_fence is not None:
        raise ContentPlatformValidationError("Markdown code fences must be closed before export")


def markdown_document(title: str, body: str) -> str:
    """Add one document heading without duplicating an existing matching H1."""
    normalized_title = " ".join(title.splitlines()).strip()
    normalized_body = normalize_text(body)
    if normalized_body.lstrip().startswith(f"# {normalized_title}\n"):
        return normalized_body
    return f"# {normalized_title}\n\n{normalized_body}"


def slug(value: str, *, fallback: str) -> str:
    """Make a conservative file-name component without changing export text."""
    normalized = _NON_SLUG.sub("-", value.casefold()).strip("-")
    return normalized or fallback
