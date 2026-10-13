"""Public request and response schemas for V2 creator preferences.

These schemas expose preference data only.  They deliberately have no prompt,
instruction, template-execution, or generation fields.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.common import Identifier, ShortText, Tag
from app.models.content import (
    ContentPillarDocument,
    ContentPillarStatus,
    CreatorProfileDocument,
    CreatorProfileStatus,
    CreatorVoice,
    PreferenceText,
)

PreferenceValues = Annotated[list[PreferenceText], Field(max_length=50)]
TagValues = Annotated[list[Tag], Field(max_length=50)]


def _unique(values: list[str], field_name: str) -> list[str]:
    """Reject repeated input rather than making an ambiguous profile record."""
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be unique")
    return values


class CreatorProfileCreateRequest(BaseModel):
    """One creator profile expressed as preference data."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    name: ShortText
    status: CreatorProfileStatus = CreatorProfileStatus.ACTIVE
    audiences: PreferenceValues = Field(default_factory=list)
    domains: PreferenceValues = Field(default_factory=list)
    voice: CreatorVoice = Field(default_factory=CreatorVoice)
    writing_preferences: PreferenceValues = Field(default_factory=list)
    formatting_preferences: PreferenceValues = Field(default_factory=list)
    terminology_preferences: PreferenceValues = Field(default_factory=list)
    language_preferences: PreferenceValues = Field(default_factory=list)
    tone_preferences: PreferenceValues = Field(default_factory=list)
    topics_of_interest: PreferenceValues = Field(default_factory=list)
    topics_to_avoid: PreferenceValues = Field(default_factory=list)
    content_objectives: PreferenceValues = Field(default_factory=list)
    tags: TagValues = Field(default_factory=list)

    @field_validator(
        "audiences",
        "domains",
        "writing_preferences",
        "formatting_preferences",
        "terminology_preferences",
        "language_preferences",
        "tone_preferences",
        "topics_of_interest",
        "topics_to_avoid",
        "content_objectives",
        "tags",
    )
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        return _unique(value, "creator profile values")


class CreatorProfileUpdateRequest(BaseModel):
    """A revision-guarded partial update to preference data."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    name: ShortText | None = None
    status: CreatorProfileStatus | None = None
    audiences: PreferenceValues | None = None
    domains: PreferenceValues | None = None
    voice: CreatorVoice | None = None
    writing_preferences: PreferenceValues | None = None
    formatting_preferences: PreferenceValues | None = None
    terminology_preferences: PreferenceValues | None = None
    language_preferences: PreferenceValues | None = None
    tone_preferences: PreferenceValues | None = None
    topics_of_interest: PreferenceValues | None = None
    topics_to_avoid: PreferenceValues | None = None
    content_objectives: PreferenceValues | None = None
    tags: TagValues | None = None

    @field_validator(
        "audiences",
        "domains",
        "writing_preferences",
        "formatting_preferences",
        "terminology_preferences",
        "language_preferences",
        "tone_preferences",
        "topics_of_interest",
        "topics_to_avoid",
        "content_objectives",
        "tags",
    )
    @classmethod
    def unique_values(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _unique(value, "creator profile values")

    @model_validator(mode="after")
    def require_change(self) -> "CreatorProfileUpdateRequest":
        if len(self.model_fields_set - {"expected_revision"}) == 0:
            raise ValueError("at least one creator profile field must be updated")
        return self


class CreatorProfileResponse(BaseModel):
    """Creator preference data without internal MongoDB identifiers."""

    profile_id: str
    name: str
    status: CreatorProfileStatus
    audiences: tuple[str, ...]
    domains: tuple[str, ...]
    voice: CreatorVoice
    writing_preferences: tuple[str, ...]
    formatting_preferences: tuple[str, ...]
    terminology_preferences: tuple[str, ...]
    language_preferences: tuple[str, ...]
    tone_preferences: tuple[str, ...]
    topics_of_interest: tuple[str, ...]
    topics_to_avoid: tuple[str, ...]
    content_objectives: tuple[str, ...]
    tags: tuple[str, ...]
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: CreatorProfileDocument) -> "CreatorProfileResponse":
        return cls(
            profile_id=document.profile_id,
            name=document.name,
            status=document.status,
            audiences=tuple(document.audiences),
            domains=tuple(document.domains),
            voice=document.voice,
            writing_preferences=tuple(document.writing_preferences),
            formatting_preferences=tuple(document.formatting_preferences),
            terminology_preferences=tuple(document.terminology_preferences),
            language_preferences=tuple(document.language_preferences),
            tone_preferences=tuple(document.tone_preferences),
            topics_of_interest=tuple(document.topics_of_interest),
            topics_to_avoid=tuple(document.topics_to_avoid),
            content_objectives=tuple(document.content_objectives),
            tags=tuple(document.tags),
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class CreatorProfileListResponse(BaseModel):
    """Cursor-paginated creator profiles visible to the caller."""

    items: tuple[CreatorProfileResponse, ...]
    next_cursor: str | None


class ContentPillarCreateRequest(BaseModel):
    """One thematic pillar owned by a creator profile."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    pillar_id: Identifier
    name: ShortText
    description: str = Field(min_length=1, max_length=200_000)
    tags: TagValues = Field(default_factory=list)
    priority: float = Field(ge=0.0, le=1.0)
    status: ContentPillarStatus = ContentPillarStatus.ACTIVE

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        return _unique(value, "content pillar tags")


class ContentPillarUpdateRequest(BaseModel):
    """A revision-guarded partial update within one explicit profile."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    expected_revision: int = Field(ge=1)
    name: ShortText | None = None
    description: str | None = Field(default=None, min_length=1, max_length=200_000)
    tags: TagValues | None = None
    priority: float | None = Field(default=None, ge=0.0, le=1.0)
    status: ContentPillarStatus | None = None

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _unique(value, "content pillar tags")

    @model_validator(mode="after")
    def require_change(self) -> "ContentPillarUpdateRequest":
        if len(self.model_fields_set - {"profile_id", "expected_revision"}) == 0:
            raise ValueError("at least one content pillar field must be updated")
        return self


class ContentPillarResponse(BaseModel):
    """A profile-owned content pillar without internal MongoDB identifiers."""

    profile_id: str
    pillar_id: str
    name: str
    description: str
    tags: tuple[str, ...]
    priority: float
    status: ContentPillarStatus
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: ContentPillarDocument) -> "ContentPillarResponse":
        return cls(
            profile_id=document.profile_id,
            pillar_id=document.pillar_id,
            name=document.name,
            description=document.description,
            tags=tuple(document.tags),
            priority=document.priority,
            status=document.status,
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ContentPillarListResponse(BaseModel):
    """Cursor-paginated pillars from exactly one creator profile."""

    profile_id: str
    items: tuple[ContentPillarResponse, ...]
    next_cursor: str | None
