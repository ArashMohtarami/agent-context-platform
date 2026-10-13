"""Traceable provenance attached only by controlled research promotion."""

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.common import Identifier, normalize_mongo_timestamp
from app.models.freshness import FreshnessClass


class PromotionProvenance(BaseModel):
    """Research evidence retained on a V1 target after explicit approval.

    This bounded, typed structure carries immutable evidence identifiers,
    verification and review timestamps, and a descriptive freshness class. It
    cannot become an unreviewed arbitrary metadata channel.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: Identifier
    research_project_id: Identifier
    claim_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    citation_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    source_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    source_snapshot_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    verified_at: AwareDatetime
    review_after: AwareDatetime | None = None
    freshness_class: FreshnessClass = FreshnessClass.STABLE

    @field_validator("claim_ids", "citation_ids", "source_ids", "source_snapshot_ids")
    @classmethod
    def unique_identifiers(cls, value: list[str]) -> list[str]:
        """Keep every retained evidence reference unambiguous."""
        if len(value) != len(set(value)):
            raise ValueError("promotion provenance identifiers must be unique")
        return value

    @field_validator("verified_at", "review_after")
    @classmethod
    def normalize_review_times(cls, value: datetime | None) -> datetime | None:
        """Match MongoDB's millisecond UTC precision."""
        return None if value is None else normalize_mongo_timestamp(value)

    @field_validator("verified_at")
    @classmethod
    def require_verified_at(cls, value: datetime | None) -> datetime:
        """Keep the original explicit promotion review timestamp mandatory."""
        if value is None:
            raise ValueError("verified_at is required")
        return value

    @model_validator(mode="after")
    def validate_review_date(self) -> "PromotionProvenance":
        if self.review_after is not None and self.review_after < self.verified_at:
            raise ValueError("review_after must be on or after verified_at")
        return self
