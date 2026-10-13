"""Pure V2 repurposing-plan metadata and non-draft source extraction.

The values here describe planning fields only.  Canonical Git-backed content
skills remain the source of writing guidance; this module never generates a
target body or substitutes for those skills.
"""

import re
from dataclasses import dataclass

from app.models.content import ContentPlatform


@dataclass(frozen=True, slots=True)
class PlatformRepurposeMetadata:
    """Stable structural differences a plan should expose for one destination."""

    content_skill_id: str
    shorten: tuple[str, ...]
    expand: tuple[str, ...]
    differences: tuple[str, ...]


# This is intentionally limited to output-shape metadata.  The reviewed
# Markdown skills named below carry writing instructions and can be changed
# without changing service logic.
PLATFORM_REPURPOSE_METADATA: dict[ContentPlatform, PlatformRepurposeMetadata] = {
    ContentPlatform.LINKEDIN: PlatformRepurposeMetadata(
        content_skill_id="linkedin-writing",
        shorten=("background context", "supporting detail"),
        expand=("practical conclusion",),
        differences=("short-form feed structure", "one primary idea"),
    ),
    ContentPlatform.MEDIUM: PlatformRepurposeMetadata(
        content_skill_id="medium-writing",
        shorten=("repeated framing",),
        expand=("problem context", "technical depth", "tradeoffs"),
        differences=("long-form article structure", "first-principles explanation"),
    ),
    ContentPlatform.GITHUB: PlatformRepurposeMetadata(
        content_skill_id="github-writing",
        shorten=("narrative framing",),
        expand=("implementation details", "reproducible commands", "troubleshooting"),
        differences=("documentation structure", "reference-oriented examples"),
    ),
}

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def source_points(value: str, *, limit: int = 3, maximum_length: int = 240) -> tuple[str, ...]:
    """Return at most a few concise source excerpts, never a target draft.

    The caller receives evidence labels and short fragments to decide how to
    adapt them.  Returning neither the full source body nor a rewritten body
    prevents this planning helper from becoming a copy/generation path.
    """
    whole = " ".join(value.split())
    points: list[str] = []
    for fragment in _SENTENCE.split(value):
        normalized = " ".join(fragment.split())
        if not normalized:
            continue
        points.append(normalized[:maximum_length].rstrip())
        if len(points) == limit:
            break
    if len(points) == 1 and points[0] == whole:
        # A one-sentence source must not come back verbatim as a purported
        # repurpose plan.  The stable label still signals usable evidence
        # while directing the reviewer to the attributable source record.
        return ("Review the source's core supporting point.",)
    return tuple(points)


def source_examples(value: str, *, limit: int = 2) -> tuple[str, ...]:
    """Surface bounded explicit examples without inventing any."""
    return tuple(
        point
        for point in source_points(value, limit=20)
        if "`" in point or "example" in point.lower()
    )[:limit]
