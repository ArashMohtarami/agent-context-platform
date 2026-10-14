"""V2 creator and content persistence models.

These models intentionally describe stored data only.  They do not decide
which knowledge is suitable for publication, generate prose, or perform a
lifecycle transition; those are later V2 service responsibilities.
"""

from collections.abc import Mapping
from enum import StrEnum
from math import isfinite
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models.actor import ActorProvenance, system_actor
from app.models.common import (
    ContentText,
    Identifier,
    Metadata,
    ShortText,
    TaggedDocument,
    document_object_id,
    normalize_mongo_timestamp,
    populate_deterministic_id,
)
from app.models.embedding import EmbeddingFields
from app.models.research import ResearchBriefCitationReference, ResearchBriefClaimReference


class ContentStatus(StrEnum):
    """The shared lifecycle vocabulary for content work."""

    IDEA = "idea"
    SELECTED = "selected"
    RESEARCHING = "researching"
    BRIEF_READY = "brief_ready"
    DRAFTING = "drafting"
    REVIEW = "review"
    APPROVED = "approved"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ContentPlatform(StrEnum):
    """A destination supported by the content data model.

    Adding a future destination is a deliberately local change: add its enum
    value and the corresponding validator value.  Storage and repositories do
    not branch on the current three destinations.
    """

    LINKEDIN = "linkedin"
    MEDIUM = "medium"
    GITHUB = "github"


class ContentPublicationProvider(StrEnum):
    """The publisher that recorded a stored content item as published.

    Only ``manual`` is configured in Phase 9. The other values are durable
    vocabulary for future, explicitly configured adapters; adding the enum
    does not enable credentials, network access, or automatic publishing.
    """

    MANUAL = "manual"
    GITHUB = "github"
    LINKEDIN = "linkedin"
    MEDIUM = "medium"


class ContentRelationType(StrEnum):
    """How a content item relates to a provenance source."""

    DERIVED_FROM = "derived_from"
    MENTIONS = "mentions"
    EXPLAINS = "explains"
    DOCUMENTS = "documents"
    UPDATES = "updates"
    CONTRASTS = "contrasts"


class CreatorProfileStatus(StrEnum):
    """Whether a creator profile remains available for new content."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class ContentPillarStatus(StrEnum):
    """Whether a thematic pillar remains available for new content."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ContentBriefStatus(StrEnum):
    """The persistence state of an evidence-backed content brief."""

    DRAFT = "draft"
    READY = "ready"
    ARCHIVED = "archived"


class ContentFactualReviewStatus(StrEnum):
    """Whether a brief's cited factual material has received an evidence review.

    ``VERIFIED`` is deliberately narrow: it means each reviewed, mapped claim
    had an explicitly supported state and supporting citation at review time.
    It is not a guarantee that the resulting prose is true or complete.
    """

    NOT_REVIEWED = "not_reviewed"
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"


class ContentLinkStatus(StrEnum):
    """Whether a provenance relation is currently in use."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ContentMetricStatus(StrEnum):
    """Whether an imported metrics observation should be retained."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ContentCampaignStatus(StrEnum):
    """The lifecycle of a coordinated set of content."""

    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


PreferenceText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
PreferenceList = Annotated[list[PreferenceText], Field(default_factory=list, max_length=50)]
SupportingPoints = Annotated[list[ContentText], Field(default_factory=list, max_length=50)]
ContentIdentifiers = Annotated[list[Identifier], Field(default_factory=list, max_length=50)]
PlatformList = Annotated[list[ContentPlatform], Field(min_length=1, max_length=16)]


def _unique[T](values: list[T], field_name: str) -> list[T]:
    """Reject duplicates without changing a creator's supplied ordering."""
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be unique")
    return values


class CreatorVoice(BaseModel):
    """Structured preference data, never executable or hidden instructions."""

    model_config = ConfigDict(extra="forbid")

    tone: PreferenceList = Field(default_factory=list)
    avoid: PreferenceList = Field(default_factory=list)

    @field_validator("tone", "avoid")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        return _unique(value, "voice values")


class ContentOrigin(BaseModel):
    """The initial source that led to a content idea."""

    model_config = ConfigDict(extra="forbid")

    type: Identifier
    project_id: Identifier | None = None
    source_ids: ContentIdentifiers = Field(default_factory=list)

    @field_validator("source_ids")
    @classmethod
    def unique_source_ids(cls, value: list[str]) -> list[str]:
        return _unique(value, "origin source_ids")


class ContentSourceReference(BaseModel):
    """A source record cited by a brief or a content item.

    ``project_id`` stays on each reference rather than being inferred from a
    later lookup.  This makes provenance auditable and lets later review code
    enforce project isolation before anything is exported publicly.
    """

    model_config = ConfigDict(extra="forbid")

    source_type: Identifier
    source_id: Identifier
    project_id: Identifier | None = None


class ContentDocument(TaggedDocument):
    """Base for V2 records whose generated defaults must round-trip exactly."""

    model_config = ConfigDict(validate_default=True)

    actor: ActorProvenance = Field(default_factory=system_actor)


class CreatorProfileDocument(ContentDocument):
    """A creator's communication preferences, stored as data."""

    profile_id: Identifier
    name: ShortText
    status: CreatorProfileStatus = CreatorProfileStatus.ACTIVE
    audiences: PreferenceList = Field(default_factory=list)
    domains: PreferenceList = Field(default_factory=list)
    voice: CreatorVoice = Field(default_factory=CreatorVoice)
    writing_preferences: PreferenceList = Field(default_factory=list)
    formatting_preferences: PreferenceList = Field(default_factory=list)
    terminology_preferences: PreferenceList = Field(default_factory=list)
    language_preferences: PreferenceList = Field(default_factory=list)
    tone_preferences: PreferenceList = Field(default_factory=list)
    topics_of_interest: PreferenceList = Field(default_factory=list)
    topics_to_avoid: PreferenceList = Field(default_factory=list)
    content_objectives: PreferenceList = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(value, namespace="creator_profile", fields=("profile_id",))

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
    )
    @classmethod
    def unique_preferences(cls, value: list[str]) -> list[str]:
        return _unique(value, "profile values")

    @model_validator(mode="after")
    def validate_id(self) -> "CreatorProfileDocument":
        if self.id != self.identity_id("creator_profile", self.profile_id):
            raise ValueError("_id does not match profile_id")
        return self


class ContentPillarDocument(ContentDocument):
    """A long-lived thematic area owned by one creator profile."""

    profile_id: Identifier
    pillar_id: Identifier
    name: ShortText
    description: ContentText
    priority: float = Field(ge=0.0, le=1.0)
    status: ContentPillarStatus = ContentPillarStatus.ACTIVE

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value,
            namespace="content_pillar",
            fields=("profile_id", "pillar_id"),
        )

    @model_validator(mode="after")
    def validate_id(self) -> "ContentPillarDocument":
        if self.id != self.identity_id("content_pillar", self.profile_id, self.pillar_id):
            raise ValueError("_id does not match profile_id and pillar_id")
        return self


class ContentIdeaDocument(EmbeddingFields, ContentDocument):
    """A candidate content topic and its initial provenance."""

    profile_id: Identifier
    idea_id: Identifier
    title: ShortText
    description: ContentText
    pillar_id: Identifier | None = None
    platforms: PlatformList
    origin: ContentOrigin
    priority: float = Field(ge=0.0, le=1.0)
    status: ContentStatus = ContentStatus.IDEA

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value,
            namespace="content_idea",
            fields=("profile_id", "idea_id"),
        )

    @field_validator("platforms")
    @classmethod
    def unique_platforms(cls, value: list[ContentPlatform]) -> list[ContentPlatform]:
        return _unique(value, "platforms")

    @model_validator(mode="after")
    def validate_id(self) -> "ContentIdeaDocument":
        if self.id != self.identity_id("content_idea", self.profile_id, self.idea_id):
            raise ValueError("_id does not match profile_id and idea_id")
        return self


class ContentBriefDocument(EmbeddingFields, ContentDocument):
    """A platform-specific evidence plan for one proposed item."""

    profile_id: Identifier
    brief_id: Identifier
    idea_id: Identifier
    platform: ContentPlatform
    objective: ContentText
    audience: PreferenceList = Field(default_factory=list)
    primary_message: ContentText
    supporting_points: SupportingPoints = Field(default_factory=list)
    source_refs: Annotated[
        list[ContentSourceReference], Field(default_factory=list, max_length=100)
    ]
    # These are frozen V3 evidence references. They make a content plan
    # auditable without treating research as durable V1 knowledge or allowing
    # it to silently change a draft.
    research_project_id: Identifier | None = None
    claim_refs: Annotated[
        list[ResearchBriefClaimReference], Field(default_factory=list, max_length=200)
    ]
    citation_refs: Annotated[
        list[ResearchBriefCitationReference], Field(default_factory=list, max_length=200)
    ]
    factual_review_status: ContentFactualReviewStatus = ContentFactualReviewStatus.NOT_REVIEWED
    skills: ContentIdentifiers = Field(default_factory=list)
    avoid_repetition_with: ContentIdentifiers = Field(default_factory=list)
    status: ContentBriefStatus = ContentBriefStatus.DRAFT

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value,
            namespace="content_brief",
            fields=("profile_id", "brief_id"),
        )

    @field_validator("audience", "supporting_points", "skills", "avoid_repetition_with")
    @classmethod
    def unique_values(cls, value: list[object]) -> list[object]:
        return _unique(value, "brief values")

    @field_validator("source_refs")
    @classmethod
    def unique_source_refs(
        cls, value: list[ContentSourceReference]
    ) -> list[ContentSourceReference]:
        keys = [(item.source_type, item.source_id, item.project_id) for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("source_refs must be unique")
        return value

    @field_validator("claim_refs")
    @classmethod
    def unique_claim_refs(
        cls, value: list[ResearchBriefClaimReference]
    ) -> list[ResearchBriefClaimReference]:
        if len({item.claim_id for item in value}) != len(value):
            raise ValueError("claim_refs must be unique by claim_id")
        return value

    @field_validator("citation_refs")
    @classmethod
    def unique_citation_refs(
        cls, value: list[ResearchBriefCitationReference]
    ) -> list[ResearchBriefCitationReference]:
        if len({(item.claim_id, item.citation_id) for item in value}) != len(value):
            raise ValueError("citation_refs must be unique by claim_id and citation_id")
        return value

    @model_validator(mode="after")
    def validate_research_references(self) -> "ContentBriefDocument":
        if self.research_project_id is None and (self.claim_refs or self.citation_refs):
            raise ValueError("research references require research_project_id")
        claim_ids = {item.claim_id for item in self.claim_refs}
        if any(item.claim_id not in claim_ids for item in self.citation_refs):
            raise ValueError("citation_refs must reference a claim in claim_refs")
        return self

    @model_validator(mode="after")
    def validate_id(self) -> "ContentBriefDocument":
        if self.id != self.identity_id("content_brief", self.profile_id, self.brief_id):
            raise ValueError("_id does not match profile_id and brief_id")
        return self


class ContentItemDocument(EmbeddingFields, ContentDocument):
    """The current working copy of an item; versions live separately."""

    profile_id: Identifier
    content_id: Identifier
    brief_id: Identifier | None = None
    platform: ContentPlatform
    content_type: Identifier
    title: ShortText
    body: ContentText
    status: ContentStatus = ContentStatus.DRAFTING
    topic_tags: Annotated[list[Identifier], Field(default_factory=list, max_length=50)]
    pillar_id: Identifier | None = None
    project_ids: ContentIdentifiers = Field(default_factory=list)
    source_refs: Annotated[
        list[ContentSourceReference], Field(default_factory=list, max_length=100)
    ]
    current_version: int = Field(default=1, ge=1)
    published_at: AwareDatetime | None = None
    external_url: str | None = Field(default=None, min_length=1, max_length=2_048)
    publication_provider: ContentPublicationProvider | None = None
    external_id: str | None = Field(default=None, min_length=1, max_length=1_024)
    publication_idempotency_key: Identifier | None = None

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value,
            namespace="content_item",
            fields=("profile_id", "content_id"),
        )

    @field_validator("topic_tags", "project_ids")
    @classmethod
    def unique_identifiers(cls, value: list[str]) -> list[str]:
        return _unique(value, "content identifiers")

    @field_validator("published_at")
    @classmethod
    def normalize_published_at(cls, value: AwareDatetime | None) -> AwareDatetime | None:
        return None if value is None else normalize_mongo_timestamp(value)

    @field_validator("source_refs")
    @classmethod
    def unique_source_refs(
        cls, value: list[ContentSourceReference]
    ) -> list[ContentSourceReference]:
        keys = [(item.source_type, item.source_id, item.project_id) for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("source_refs must be unique")
        return value

    @model_validator(mode="after")
    def validate_id(self) -> "ContentItemDocument":
        if self.id != self.identity_id("content_item", self.profile_id, self.content_id):
            raise ValueError("_id does not match profile_id and content_id")
        return self


class ContentVersionDocument(ContentDocument):
    """An immutable snapshot of one content item revision."""

    profile_id: Identifier
    content_id: Identifier
    version: int = Field(ge=1)
    title: ShortText
    body: ContentText
    status: ContentStatus
    change_reason: ShortText | None = None
    created_by: Identifier | None = None

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        if not isinstance(value, Mapping) or "_id" in value or "id" in value:
            return value
        profile_id = value.get("profile_id")
        content_id = value.get("content_id")
        version = value.get("version")
        if not isinstance(profile_id, str) or not isinstance(content_id, str):
            return value
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return value
        populated = dict(value)
        populated["_id"] = document_object_id(
            value, "content_version", profile_id, content_id, str(version)
        )
        return populated

    @model_validator(mode="after")
    def validate_id(self) -> "ContentVersionDocument":
        expected = self.identity_id(
            "content_version", self.profile_id, self.content_id, str(self.version)
        )
        if self.id != expected:
            raise ValueError("_id does not match profile_id, content_id and version")
        return self


class ContentLinkDocument(ContentDocument):
    """A durable, reviewable relation from content to source knowledge."""

    profile_id: Identifier
    link_id: Identifier
    content_id: Identifier
    source_type: Identifier
    source_id: Identifier
    project_id: Identifier | None = None
    relation: ContentRelationType
    status: ContentLinkStatus = ContentLinkStatus.ACTIVE

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value,
            namespace="content_link",
            fields=("profile_id", "link_id"),
        )

    @model_validator(mode="after")
    def validate_id(self) -> "ContentLinkDocument":
        if self.id != self.identity_id("content_link", self.profile_id, self.link_id):
            raise ValueError("_id does not match profile_id and link_id")
        return self


class ContentMetricDocument(ContentDocument):
    """One timestamped metrics capture, retained independently of analysis."""

    profile_id: Identifier
    metric_id: Identifier
    content_id: Identifier
    platform: ContentPlatform
    captured_at: AwareDatetime
    metrics: dict[Identifier, float] = Field(default_factory=dict, min_length=1, max_length=100)
    source: Identifier = "manual"
    provider_metadata: Metadata = Field(default_factory=dict, max_length=100)
    status: ContentMetricStatus = ContentMetricStatus.ACTIVE

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value,
            namespace="content_metric",
            fields=("profile_id", "metric_id"),
        )

    @field_validator("metrics", mode="before")
    @classmethod
    def valid_raw_metrics(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            raise ValueError("metrics must be an object")
        for measurement in value.values():
            if isinstance(measurement, bool) or not isinstance(measurement, int | float):
                raise ValueError("metric values must be finite numbers")
            if not isfinite(measurement) or measurement < 0:
                raise ValueError("metric values must be finite non-negative numbers")
        return value

    @field_validator("metrics")
    @classmethod
    def valid_metrics(cls, value: dict[str, float]) -> dict[str, float]:
        if len(value) > 100:
            raise ValueError("metrics must contain at most 100 values")
        for measurement in value.values():
            if not isfinite(measurement) or measurement < 0:
                raise ValueError("metric values must be finite non-negative numbers")
        return value

    @field_validator("captured_at")
    @classmethod
    def normalize_captured_at(cls, value: AwareDatetime) -> AwareDatetime:
        return normalize_mongo_timestamp(value)

    @model_validator(mode="after")
    def validate_id(self) -> "ContentMetricDocument":
        if self.id != self.identity_id("content_metric", self.profile_id, self.metric_id):
            raise ValueError("_id does not match profile_id and metric_id")
        return self


class ContentCampaignDocument(ContentDocument):
    """A named, profile-owned collection of related content."""

    profile_id: Identifier
    campaign_id: Identifier
    name: ShortText
    description: ContentText
    content_ids: ContentIdentifiers = Field(default_factory=list)
    platforms: Annotated[list[ContentPlatform], Field(default_factory=list, max_length=16)]
    status: ContentCampaignStatus = ContentCampaignStatus.PLANNED

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value,
            namespace="content_campaign",
            fields=("profile_id", "campaign_id"),
        )

    @field_validator("content_ids", "platforms")
    @classmethod
    def unique_values(cls, value: list[object]) -> list[object]:
        return _unique(value, "campaign values")

    @model_validator(mode="after")
    def validate_id(self) -> "ContentCampaignDocument":
        expected = self.identity_id("content_campaign", self.profile_id, self.campaign_id)
        if self.id != expected:
            raise ValueError("_id does not match profile_id and campaign_id")
        return self
