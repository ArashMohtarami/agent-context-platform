"""Bounded freshness classifications for research-derived knowledge."""

from enum import StrEnum


class FreshnessClass(StrEnum):
    """How often a promoted research finding should be reconsidered.

    The class is a review-planning label. It is not a confidence score, a
    truth assessment, or an instruction to automatically mutate knowledge.
    """

    STABLE = "stable"
    VERSION_DEPENDENT = "version_dependent"
    TIME_SENSITIVE = "time_sensitive"
    VOLATILE = "volatile"


class RevalidationReason(StrEnum):
    """Observable reasons a human review may be needed."""

    REVIEW_DUE = "review_due"
    BASELINE_SNAPSHOT_UNAVAILABLE = "baseline_snapshot_unavailable"
    CONTENT_HASH_CHANGED = "content_hash_changed"
    ETAG_CHANGED = "etag_changed"
    LAST_MODIFIED_CHANGED = "last_modified_changed"
    VERSION_IDENTIFIER_CHANGED = "version_identifier_changed"
    REPOSITORY_COMMIT_CHANGED = "repository_commit_changed"
