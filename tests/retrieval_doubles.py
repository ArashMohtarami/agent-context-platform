"""Candidate builders shared by the hybrid retrieval tests."""

from collections.abc import Mapping
from datetime import UTC, datetime

from app.retrieval.types import (
    NEUTRAL_IMPORTANCE,
    NO_METADATA,
    RetrievalCandidate,
    RetrievalMatch,
    SourceType,
)

NOW = datetime(2026, 9, 1, tzinfo=UTC)
DAY = 86_400.0


def candidate(
    key: str = "skill_chunk:fastapi-production:1.0.0:chunk-0000",
    *,
    source: SourceType = "active_skill",
    title: str = "FastAPI Production: Rules",
    content: str = "Payment requests use idempotency keys.",
    tokens: int = 100,
    updated_at: datetime = NOW,
    importance: float = NEUTRAL_IMPORTANCE,
    project_id: str | None = None,
    is_active: bool = True,
    category: str | None = "backend",
    tags: tuple[str, ...] = ("python", "fastapi"),
    trust_level: str | None = "trusted",
    metadata: Mapping[str, object] = NO_METADATA,
) -> RetrievalCandidate:
    """Build one retrieval candidate with production-shaped defaults."""
    return RetrievalCandidate(
        key=key,
        source=source,
        title=title,
        content=content,
        tokens=tokens,
        updated_at=updated_at,
        importance=importance,
        project_id=project_id,
        is_active=is_active,
        category=category,
        tags=tags,
        trust_level=trust_level,
        metadata=metadata,
    )


def match(score: float, **overrides: object) -> RetrievalMatch:
    """Build one scored match around a candidate built from overrides."""
    return RetrievalMatch(candidate=candidate(**overrides), score=score)  # type: ignore[arg-type]


def words(count: int, *, prefix: str = "term") -> str:
    """Build distinctive content of a known length for overlap tests."""
    return " ".join(f"{prefix}{index}" for index in range(count))
