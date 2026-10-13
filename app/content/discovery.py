"""Typed request data shared by the Phase 5 API, MCP, and discovery service."""

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from app.models.common import normalize_mongo_timestamp


class DiscoveryTimeRange(BaseModel):
    """An inclusive update-time window for deterministic content discovery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> "DiscoveryTimeRange":
        """Keep a request's inclusive bounds ordered and BSON-compatible."""
        start = None if self.start_at is None else normalize_mongo_timestamp(self.start_at)
        end = None if self.end_at is None else normalize_mongo_timestamp(self.end_at)
        if start is not None and end is not None and start > end:
            raise ValueError("time_range.start_at must not be after time_range.end_at")
        object.__setattr__(self, "start_at", start)
        object.__setattr__(self, "end_at", end)
        return self

    def includes(self, value: datetime) -> bool:
        """Report whether an update timestamp falls inside this inclusive range."""
        return (self.start_at is None or value >= self.start_at) and (
            self.end_at is None or value <= self.end_at
        )
