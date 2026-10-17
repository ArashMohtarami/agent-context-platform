"""Content-free, bounded summary contracts. Times are instants, never local dates."""

from datetime import UTC, datetime, timedelta
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.common import Identifier

Source = Literal["gateway_activity", "governance"]
Scope = Literal["own", "team", "org"]
MAX_REPORT_ROWS = 5000
MAX_REPORT_BYTES = 2 * 1024 * 1024
SERVED_DAYS = 90


class SummaryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Source = "gateway_activity"
    scope: Scope = "own"
    start: datetime
    end: datetime
    timezone: str = Field(default="UTC", max_length=64)
    bucket: Literal["hour", "day"] = "day"
    team_id: Identifier | None = None
    project_id: Identifier | None = None
    client_id: Identifier | None = None
    outcome: Literal["succeeded", "denied", "failed", "committed"] | None = None

    @field_validator("start", "end")
    @classmethod
    def instant(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone-aware instants are required")
        return value.astimezone(UTC)

    @field_validator("timezone")
    @classmethod
    def zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("an IANA timezone is required") from None
        return value

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if not timedelta(0) < self.end - self.start <= timedelta(days=31):
            raise ValueError("window must be positive and at most 31 days")
        if self.scope == "team" and self.team_id is None:
            raise ValueError("team scope requires a team ID")
        if self.scope == "own" and self.team_id is not None:
            raise ValueError("own scope cannot select a team")
        if self.source == "governance" and (
            self.scope != "org" or self.team_id or self.project_id or self.client_id
        ):
            raise ValueError("governance is an organization audit stream")
        return self

    def current(self, now: datetime) -> None:
        if self.end > now or self.start < now - timedelta(days=SERVED_DAYS):
            from app.security.policy.evaluation import PolicyDenied

            raise PolicyDenied("window is outside the served history horizon")


class HistoryGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def instant(cls, value: datetime) -> datetime:
        return SummaryQuery.instant(value)

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.start >= self.end:
            raise ValueError("gap requires positive bounds")
        return self


class Checkpoint(BaseModel):
    """Operator assertion about a recorded stream, not employee/platform usage."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Source
    coverage_start: datetime
    retained_from: datetime
    # Acknowledgement is explicit; the gateway writer remains best effort.
    available: bool = True
    storage_retention: Literal["installation_managed"] = "installation_managed"
    approval_reference: str = Field(min_length=1, max_length=128)
    gaps: tuple[HistoryGap, ...] = Field(default=(), max_length=32)

    @field_validator("coverage_start", "retained_from")
    @classmethod
    def instant(cls, value: datetime) -> datetime:
        return SummaryQuery.instant(value)


class ActivityQuery(SummaryQuery):
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2048)

    def filters(self) -> SummaryQuery:
        return SummaryQuery.model_validate(self.model_dump(exclude={"limit", "cursor"}))


class EventDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str
    occurred_at: datetime
    action: str
    outcome: str
    actor_kind: Literal["machine", "human", "operator", "unknown"]
    actor_id: str | None
    sponsor_id: str | None
    service_account_id: str | None
    team_id: str | None
    project_id: str | None
    attribution: Literal["verified_event_time", "legacy_unattributed"]


class CoverageDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Source
    state: Literal["missing", "unavailable", "stale", "absent_history", "partial", "complete"]
    population: str
    watermark: datetime | None
    coverage_start: datetime | None
    retained_from: datetime | None
    checked_at: datetime | None
    storage_retention: Literal["installation_managed", "undeclared"]
    served_history_days: int = SERVED_DAYS
    report_ttl_seconds: int = 600
    collection_contract: str
    gaps: list[HistoryGap] = Field(default_factory=list)


class SummaryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org_id: str
    policy_revision: int
    query: SummaryQuery
    generated_at: datetime
    coverage: CoverageDTO
    observed_count: int
    observation_state: Literal["recorded_events", "no_recorded_events"]
    outcomes: dict[str, int]
    buckets: list[dict[str, datetime | int]]
    sparse_series: bool = True


class ActivityDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[EventDTO]
    next_cursor: str | None
    observed_count: int
    coverage: CoverageDTO
    policy_revision: int


class ReportDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: str
    download_path: str
    expires_at: datetime
    max_rows: int = MAX_REPORT_ROWS
