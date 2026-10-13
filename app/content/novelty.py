"""Explainable, deterministic similarity helpers for V2 content planning.

Novelty is a planning signal, not a factual determination.  These functions
only compare normalized stored terms and deliberately expose the terms that
contributed to an overlap so a person or calling agent can judge the result.
"""

import re
from dataclasses import dataclass
from typing import Literal

NoveltyClassification = Literal[
    "same-topic-same-angle",
    "same-topic-new-angle",
    "related-topic",
    "new-topic",
]

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]*", re.IGNORECASE)
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "about",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "what",
        "with",
        "why",
    }
)


@dataclass(frozen=True, slots=True)
class ContentOverlap:
    """The explainable overlap between a candidate and one stored record."""

    topic_overlap: float
    angle_overlap: float
    shared_topic_terms: tuple[str, ...]
    shared_angle_terms: tuple[str, ...]
    classification: NoveltyClassification


def compare_content(
    *,
    candidate_title: str,
    candidate_detail: str,
    candidate_tags: tuple[str, ...],
    candidate_pillar_id: str | None,
    stored_title: str,
    stored_detail: str,
    stored_tags: tuple[str, ...],
    stored_pillar_id: str | None,
) -> ContentOverlap:
    """Compare topic and angle terms without claiming semantic certainty.

    Topic uses titles, tags, and pillars.  Angle uses the descriptive fields
    after removing topic terms, preventing a shared subject from being
    reported as the same explanation merely because its name is repeated.
    """
    candidate_topic = _terms(candidate_title, *candidate_tags, candidate_pillar_id or "")
    stored_topic = _terms(stored_title, *stored_tags, stored_pillar_id or "")
    shared_topic = tuple(sorted(candidate_topic & stored_topic))
    topic_overlap = _containment(candidate_topic, stored_topic)
    # Tags and pillars are explicit human-curated topic declarations.  A
    # shared one therefore carries stronger evidence than incidental title
    # vocabulary (which is especially sparse for short proposed titles).
    shared_tags = _terms(*candidate_tags) & _terms(*stored_tags)
    same_pillar = candidate_pillar_id is not None and candidate_pillar_id == stored_pillar_id
    if shared_tags or same_pillar:
        topic_overlap = max(topic_overlap, 0.65)

    candidate_angle = _terms(candidate_detail) - candidate_topic
    stored_angle = _terms(stored_detail) - stored_topic
    # Very short records can have no remaining detail once topic tokens are
    # removed.  In that case the descriptive terms still provide the best
    # available, explicitly limited evidence.
    if not candidate_angle:
        candidate_angle = _terms(candidate_detail)
    if not stored_angle:
        stored_angle = _terms(stored_detail)
    shared_angle = tuple(sorted(candidate_angle & stored_angle))
    angle_overlap = _containment(candidate_angle, stored_angle)
    classification = classify_overlap(topic_overlap, angle_overlap)
    return ContentOverlap(
        topic_overlap=topic_overlap,
        angle_overlap=angle_overlap,
        shared_topic_terms=shared_topic,
        shared_angle_terms=shared_angle,
        classification=classification,
    )


def classify_overlap(topic_overlap: float, angle_overlap: float) -> NoveltyClassification:
    """Classify an advisory overlap using stable, documented thresholds."""
    if topic_overlap >= 0.60:
        if angle_overlap >= 0.60:
            return "same-topic-same-angle"
        return "same-topic-new-angle"
    if topic_overlap >= 0.25 or angle_overlap >= 0.25:
        return "related-topic"
    return "new-topic"


def _terms(*values: str) -> frozenset[str]:
    return frozenset(
        token.lower()
        for value in values
        for token in _TOKEN.findall(value)
        if len(token) > 1 and token.lower() not in _STOP_WORDS
    )


def _containment(left: frozenset[str], right: frozenset[str]) -> float:
    """Return shared terms relative to the smaller non-empty representation."""
    if not left or not right:
        return 0.0
    return round(len(left & right) / min(len(left), len(right)), 4)
