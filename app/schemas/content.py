"""REST contracts for V2 creator content through Phase 9 explicit publishing.

These schemas carry user-supplied data only.  They contain no prompt,
generation, retrieval, novelty, or publication-integration fields.
"""

from collections.abc import Mapping
from datetime import datetime
from math import isfinite
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.content.discovery import DiscoveryTimeRange
from app.content.novelty import NoveltyClassification
from app.content.platforms.base import ExportFormat, ExportMode, PlatformExport
from app.content.publication import (
    PublicationFinding,
    PublicationFindingSeverity,
    PublicationFindingType,
    PublicationReview,
)
from app.models.actor import ActorProvenance
from app.models.common import ContentText, Identifier, ShortText, Tag
from app.models.content import (
    ContentBriefDocument,
    ContentBriefStatus,
    ContentFactualReviewStatus,
    ContentIdeaDocument,
    ContentItemDocument,
    ContentMetricDocument,
    ContentMetricStatus,
    ContentOrigin,
    ContentPlatform,
    ContentPublicationProvider,
    ContentSourceReference,
    ContentStatus,
    ContentVersionDocument,
)
from app.models.research import (
    ClaimState,
    ResearchBriefCitationReference,
    ResearchBriefClaimReference,
)
from app.retrieval.types import MIN_TOKEN_BUDGET
from app.services.content_discovery_service import (
    ContentBriefPreparation,
    ContentIdeaCandidate,
    ContentIdeaDiscovery,
    DiscoveryEvidence,
    DiscoveryFreshness,
    ExistingIdea,
    SimilarPublishedContent,
    SkillReference,
)
from app.services.content_novelty_service import ContentNoveltyResult, SimilarContent
from app.services.content_repurpose_service import (
    RelatedContent,
    RepurposePlan,
    RepurposeSourceKind,
    RepurposeTargetPlan,
)
from app.services.content_research_service import (
    ContentFactFinding,
    ContentFactReview,
    FactReviewClassification,
)
from app.services.content_retrieval_service import (
    ContentRetrievalProfileName,
    ContentSearchItem,
    ContentSearchKind,
    ContentSearchMethod,
    ContentSearchResult,
)

TagValues = Annotated[list[Tag], Field(max_length=50)]
IdentifierValues = Annotated[list[Identifier], Field(max_length=50)]
PlatformValues = Annotated[list[ContentPlatform], Field(min_length=1, max_length=16)]
MetricValues = Annotated[dict[Identifier, float], Field(min_length=1, max_length=100)]
MetricMetadata = Annotated[dict[Identifier, object], Field(max_length=100)]
SourceReferences = Annotated[list[ContentSourceReference], Field(max_length=100)]
ResearchClaimReferences = Annotated[list[ResearchBriefClaimReference], Field(max_length=200)]
ResearchCitationReferences = Annotated[list[ResearchBriefCitationReference], Field(max_length=200)]
SupportingPoints = Annotated[list[ContentText], Field(max_length=50)]
ContentSearchStatus = ContentStatus | ContentBriefStatus
ContentSearchKinds = Annotated[list[ContentSearchKind], Field(max_length=3)]
AudienceValues = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=500)]], Field(max_length=50)
]


def _unique[T](values: list[T], field_name: str) -> list[T]:
    """Reject duplicates rather than changing user ordering in an API boundary."""
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be unique")
    return values


def _unique_source_refs(
    values: list[ContentSourceReference], field_name: str = "source_refs"
) -> list[ContentSourceReference]:
    keys = [(item.source_type, item.source_id, item.project_id) for item in values]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{field_name} must be unique")
    return values


def _unique_claim_refs(
    values: list[ResearchBriefClaimReference],
) -> list[ResearchBriefClaimReference]:
    if len({item.claim_id for item in values}) != len(values):
        raise ValueError("claim_refs must be unique by claim_id")
    return values


def _unique_citation_refs(
    values: list[ResearchBriefCitationReference],
) -> list[ResearchBriefCitationReference]:
    if len({(item.claim_id, item.citation_id) for item in values}) != len(values):
        raise ValueError("citation_refs must be unique by claim_id and citation_id")
    return values


def _validate_raw_metric_values(value: object) -> object:
    """Reject lossy metric coercion before the API boundary converts JSON values."""
    if not isinstance(value, Mapping) or not value:
        raise ValueError("metrics must be a non-empty object")
    for measurement in value.values():
        if isinstance(measurement, bool) or not isinstance(measurement, int | float):
            raise ValueError("metric values must be finite numbers")
        if not isfinite(measurement) or measurement < 0:
            raise ValueError("metric values must be finite non-negative numbers")
    return value


class ContentIdeaCreateRequest(BaseModel):
    """A manually provided candidate topic and its origin metadata."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    idea_id: Identifier
    title: ShortText
    description: ContentText
    pillar_id: Identifier | None = None
    platforms: PlatformValues
    origin: ContentOrigin
    tags: TagValues = Field(default_factory=list)
    priority: float = Field(ge=0.0, le=1.0)
    status: ContentStatus = ContentStatus.IDEA

    @field_validator("platforms", "tags")
    @classmethod
    def unique_values(cls, value: list[object]) -> list[object]:
        return _unique(value, "content idea values")


class ContentIdeaUpdateRequest(BaseModel):
    """A revision-guarded partial idea update inside an explicit profile."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    expected_revision: int = Field(ge=1)
    title: ShortText | None = None
    description: ContentText | None = None
    pillar_id: Identifier | None = None
    platforms: PlatformValues | None = None
    origin: ContentOrigin | None = None
    tags: TagValues | None = None
    priority: float | None = Field(default=None, ge=0.0, le=1.0)
    status: ContentStatus | None = None

    @field_validator("platforms", "tags")
    @classmethod
    def unique_values(cls, value: list[object] | None) -> list[object] | None:
        return None if value is None else _unique(value, "content idea values")

    @model_validator(mode="after")
    def require_change(self) -> "ContentIdeaUpdateRequest":
        if len(self.model_fields_set - {"profile_id", "expected_revision"}) == 0:
            raise ValueError("at least one content idea field must be updated")
        return self


class ContentIdeaResponse(BaseModel):
    """A profile-owned idea without MongoDB internals."""

    profile_id: str
    idea_id: str
    title: str
    description: str
    pillar_id: str | None
    platforms: tuple[ContentPlatform, ...]
    origin: ContentOrigin
    tags: tuple[str, ...]
    priority: float
    status: ContentStatus
    actor: ActorProvenance
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: ContentIdeaDocument) -> "ContentIdeaResponse":
        return cls(
            profile_id=document.profile_id,
            idea_id=document.idea_id,
            title=document.title,
            description=document.description,
            pillar_id=document.pillar_id,
            platforms=tuple(document.platforms),
            origin=document.origin,
            tags=tuple(document.tags),
            priority=document.priority,
            status=document.status,
            actor=document.actor,
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ContentIdeaListResponse(BaseModel):
    """A cursor page from one creator-profile idea collection."""

    profile_id: str
    items: tuple[ContentIdeaResponse, ...]
    next_cursor: str | None


class ContentBriefCreateRequest(BaseModel):
    """An evidence plan for one platform-specific content item."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    brief_id: Identifier
    idea_id: Identifier
    platform: ContentPlatform
    objective: ContentText
    audience: AudienceValues = Field(default_factory=list)
    primary_message: ContentText
    supporting_points: SupportingPoints = Field(default_factory=list)
    source_refs: SourceReferences = Field(default_factory=list)
    research_project_id: Identifier | None = None
    claim_refs: ResearchClaimReferences = Field(default_factory=list)
    citation_refs: ResearchCitationReferences = Field(default_factory=list)
    skills: IdentifierValues = Field(default_factory=list)
    avoid_repetition_with: IdentifierValues = Field(default_factory=list)
    tags: TagValues = Field(default_factory=list)
    status: ContentBriefStatus = ContentBriefStatus.DRAFT

    @field_validator("audience", "supporting_points", "skills", "avoid_repetition_with", "tags")
    @classmethod
    def unique_values(cls, value: list[object]) -> list[object]:
        return _unique(value, "content brief values")

    @field_validator("source_refs")
    @classmethod
    def unique_source_refs(
        cls, value: list[ContentSourceReference]
    ) -> list[ContentSourceReference]:
        return _unique_source_refs(value)

    @field_validator("claim_refs")
    @classmethod
    def unique_claim_refs(
        cls, value: list[ResearchBriefClaimReference]
    ) -> list[ResearchBriefClaimReference]:
        return _unique_claim_refs(value)

    @field_validator("citation_refs")
    @classmethod
    def unique_citation_refs(
        cls, value: list[ResearchBriefCitationReference]
    ) -> list[ResearchBriefCitationReference]:
        return _unique_citation_refs(value)

    @model_validator(mode="after")
    def research_references_require_a_project(self) -> "ContentBriefCreateRequest":
        if self.research_project_id is None and (self.claim_refs or self.citation_refs):
            raise ValueError("research references require research_project_id")
        claim_ids = {item.claim_id for item in self.claim_refs}
        if any(item.claim_id not in claim_ids for item in self.citation_refs):
            raise ValueError("citation_refs must reference a claim in claim_refs")
        return self


class ContentBriefResponse(BaseModel):
    """An evidence-plan record without MongoDB internals."""

    profile_id: str
    brief_id: str
    idea_id: str
    platform: ContentPlatform
    objective: str
    audience: tuple[str, ...]
    primary_message: str
    supporting_points: tuple[str, ...]
    source_refs: tuple[ContentSourceReference, ...]
    research_project_id: str | None
    claim_refs: tuple[ResearchBriefClaimReference, ...]
    citation_refs: tuple[ResearchBriefCitationReference, ...]
    factual_review_status: ContentFactualReviewStatus
    skills: tuple[str, ...]
    avoid_repetition_with: tuple[str, ...]
    tags: tuple[str, ...]
    status: ContentBriefStatus
    actor: ActorProvenance
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: ContentBriefDocument) -> "ContentBriefResponse":
        return cls(
            profile_id=document.profile_id,
            brief_id=document.brief_id,
            idea_id=document.idea_id,
            platform=document.platform,
            objective=document.objective,
            audience=tuple(document.audience),
            primary_message=document.primary_message,
            supporting_points=tuple(document.supporting_points),
            source_refs=tuple(document.source_refs),
            research_project_id=document.research_project_id,
            claim_refs=tuple(document.claim_refs),
            citation_refs=tuple(document.citation_refs),
            factual_review_status=document.factual_review_status,
            skills=tuple(document.skills),
            avoid_repetition_with=tuple(document.avoid_repetition_with),
            tags=tuple(document.tags),
            status=document.status,
            actor=document.actor,
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ContentItemCreateRequest(BaseModel):
    """A manually supplied draft.  It always starts in the drafting state."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    content_id: Identifier
    brief_id: Identifier | None = None
    platform: ContentPlatform
    content_type: Identifier
    title: ShortText
    body: ContentText
    topic_tags: IdentifierValues = Field(default_factory=list)
    pillar_id: Identifier | None = None
    project_ids: IdentifierValues = Field(default_factory=list)
    source_refs: SourceReferences = Field(default_factory=list)
    tags: TagValues = Field(default_factory=list)
    created_by: Identifier | None = None

    @field_validator("topic_tags", "project_ids", "tags")
    @classmethod
    def unique_values(cls, value: list[object]) -> list[object]:
        return _unique(value, "content item values")

    @field_validator("source_refs")
    @classmethod
    def unique_source_refs(
        cls, value: list[ContentSourceReference]
    ) -> list[ContentSourceReference]:
        return _unique_source_refs(value)


class ContentItemUpdateRequest(BaseModel):
    """A revision-guarded update that cannot overwrite a versioned body."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    expected_revision: int = Field(ge=1)
    title: ShortText | None = None
    status: ContentStatus | None = None
    topic_tags: IdentifierValues | None = None
    pillar_id: Identifier | None = None
    project_ids: IdentifierValues | None = None
    source_refs: SourceReferences | None = None
    tags: TagValues | None = None
    published_at: datetime | None = None
    external_url: str | None = Field(default=None, min_length=1, max_length=2_048)

    @field_validator("topic_tags", "project_ids", "tags")
    @classmethod
    def unique_values(cls, value: list[object] | None) -> list[object] | None:
        return None if value is None else _unique(value, "content item values")

    @field_validator("source_refs")
    @classmethod
    def unique_source_refs(
        cls, value: list[ContentSourceReference] | None
    ) -> list[ContentSourceReference] | None:
        return None if value is None else _unique_source_refs(value)

    @model_validator(mode="after")
    def require_change(self) -> "ContentItemUpdateRequest":
        if len(self.model_fields_set - {"profile_id", "expected_revision"}) == 0:
            raise ValueError("at least one content item field must be updated")
        return self


class ContentItemResponse(BaseModel):
    """The current copy; its immutable bodies live in content_versions."""

    profile_id: str
    content_id: str
    brief_id: str | None
    platform: ContentPlatform
    content_type: str
    title: str
    body: str
    status: ContentStatus
    topic_tags: tuple[str, ...]
    pillar_id: str | None
    project_ids: tuple[str, ...]
    source_refs: tuple[ContentSourceReference, ...]
    tags: tuple[str, ...]
    current_version: int
    published_at: datetime | None
    external_url: str | None
    publication_provider: ContentPublicationProvider | None
    external_id: str | None
    publication_idempotency_key: str | None
    actor: ActorProvenance
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: ContentItemDocument) -> "ContentItemResponse":
        return cls(
            profile_id=document.profile_id,
            content_id=document.content_id,
            brief_id=document.brief_id,
            platform=document.platform,
            content_type=document.content_type,
            title=document.title,
            body=document.body,
            status=document.status,
            topic_tags=tuple(document.topic_tags),
            pillar_id=document.pillar_id,
            project_ids=tuple(document.project_ids),
            source_refs=tuple(document.source_refs),
            tags=tuple(document.tags),
            current_version=document.current_version,
            published_at=document.published_at,
            external_url=document.external_url,
            publication_provider=document.publication_provider,
            external_id=document.external_id,
            publication_idempotency_key=document.publication_idempotency_key,
            actor=document.actor,
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ContentFactReviewRequest(BaseModel):
    """Ask for a read-only mapping from a saved draft to V3 research evidence.

    Omitting both explicit lists performs only conservative exact matching of
    stored claim statements in the draft. The service never treats semantic
    similarity or retrieval scores as factual support.
    """

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    research_project_id: Identifier | None = None
    claim_ids: IdentifierValues = Field(default_factory=list)
    claim_statements: Annotated[list[ContentText], Field(max_length=100)] = Field(
        default_factory=list
    )

    @field_validator("claim_ids", "claim_statements")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        return _unique(value, "fact review claim values")


class ContentFactFindingResponse(BaseModel):
    """One explicitly assessed claim or a visible evidence gap in a draft review."""

    statement: str
    classification: FactReviewClassification
    claim_id: str | None
    claim_state: ClaimState | None
    citation_refs: tuple[ResearchBriefCitationReference, ...]
    missing_evidence: str | None

    @classmethod
    def from_value(cls, value: ContentFactFinding) -> "ContentFactFindingResponse":
        return cls(
            statement=value.statement,
            classification=value.classification,
            claim_id=value.claim_id,
            claim_state=value.claim_state,
            citation_refs=value.citation_refs,
            missing_evidence=value.missing_evidence,
        )


class ContentFactReviewResponse(BaseModel):
    """Advisory evidence mapping, not a guarantee that content is factually true."""

    profile_id: str
    content_id: str
    research_project_id: str
    factual_review_status: ContentFactualReviewStatus
    supported: int = Field(ge=0)
    unsupported: int = Field(ge=0)
    contested: int = Field(ge=0)
    outdated: int = Field(ge=0)
    findings: tuple[ContentFactFindingResponse, ...]
    citation_refs: tuple[ResearchBriefCitationReference, ...]
    missing_evidence: tuple[str, ...]
    disclaimer: str = (
        "This is an evidence mapping over explicitly assessed research claims; "
        "it does not guarantee factual truth or completeness."
    )

    @classmethod
    def from_value(
        cls, value: ContentFactReview, *, profile_id: str
    ) -> "ContentFactReviewResponse":
        return cls(
            profile_id=profile_id,
            content_id=value.content_id,
            research_project_id=value.research_project_id,
            factual_review_status=value.factual_review_status,
            supported=value.supported,
            unsupported=value.unsupported,
            contested=value.contested,
            outdated=value.outdated,
            findings=tuple(ContentFactFindingResponse.from_value(item) for item in value.findings),
            citation_refs=value.citation_refs,
            missing_evidence=value.missing_evidence,
        )


class ContentPublishRequest(BaseModel):
    """Explicitly publish one already-approved stored content representation.

    The request deliberately contains no title, body, destination URL, or
    provider credentials. A caller can only publish the existing, authorized
    record addressed by the route.
    """

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    expected_revision: int = Field(ge=1)
    provider: ContentPublicationProvider = ContentPublicationProvider.MANUAL
    idempotency_key: Identifier | None = None


class ContentPublishResponse(BaseModel):
    """The durable result of an explicit publication action."""

    content: ContentItemResponse
    provider: ContentPublicationProvider
    external_id: str | None
    external_url: str | None
    published_at: datetime
    idempotency_key: str
    already_published: bool


class ContentVersionCreateRequest(BaseModel):
    """Either append a supplied body or create a new version from history."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    expected_revision: int = Field(ge=1)
    body: ContentText | None = None
    rollback_to_version: int | None = Field(default=None, ge=1)
    change_reason: ShortText
    created_by: Identifier | None = None

    @model_validator(mode="after")
    def require_one_revision_source(self) -> "ContentVersionCreateRequest":
        if (self.body is None) == (self.rollback_to_version is None):
            raise ValueError("provide exactly one of body or rollback_to_version")
        return self


class ContentVersionResponse(BaseModel):
    """An immutable version snapshot, useful for service and test clients."""

    profile_id: str
    content_id: str
    version: int
    title: str
    body: str
    status: ContentStatus
    change_reason: str | None
    created_by: str | None
    actor: ActorProvenance
    tags: tuple[str, ...]
    created_at: datetime

    @classmethod
    def from_document(cls, document: ContentVersionDocument) -> "ContentVersionResponse":
        return cls(
            profile_id=document.profile_id,
            content_id=document.content_id,
            version=document.version,
            title=document.title,
            body=document.body,
            status=document.status,
            change_reason=document.change_reason,
            created_by=document.created_by,
            actor=document.actor,
            tags=tuple(document.tags),
            created_at=document.created_at,
        )


class ContentVersionListResponse(BaseModel):
    """A profile-scoped page of immutable content history."""

    profile_id: str
    content_id: str
    items: tuple[ContentVersionResponse, ...]
    next_cursor: str | None


class ContentMetricCreateRequest(BaseModel):
    """One manually supplied, immutable generic metric observation."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    metric_id: Identifier
    platform: ContentPlatform
    captured_at: AwareDatetime
    metrics: MetricValues
    source: Identifier = "manual"
    provider_metadata: MetricMetadata = Field(default_factory=dict)

    @field_validator("metrics", mode="before")
    @classmethod
    def valid_raw_metrics(cls, value: object) -> object:
        return _validate_raw_metric_values(value)


class ContentMetricResponse(BaseModel):
    """One persisted descriptive observation without MongoDB internals."""

    profile_id: str
    metric_id: str
    content_id: str
    platform: ContentPlatform
    captured_at: datetime
    metrics: dict[str, float]
    source: str
    provider_metadata: dict[str, object]
    status: ContentMetricStatus
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: ContentMetricDocument) -> "ContentMetricResponse":
        return cls(
            profile_id=document.profile_id,
            metric_id=document.metric_id,
            content_id=document.content_id,
            platform=document.platform,
            captured_at=document.captured_at,
            metrics=dict(document.metrics),
            source=document.source,
            provider_metadata=dict(document.provider_metadata),
            status=document.status,
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ContentMetricListResponse(BaseModel):
    """A newest-first, cursor-paged metric history for one content item."""

    profile_id: str
    content_id: str
    items: tuple[ContentMetricResponse, ...]
    next_cursor: str | None


class ContentSearchRequest(BaseModel):
    """A bounded V2 content search; retrieved prose remains data in the response."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    query: ShortText
    retrieval_profile: ContentRetrievalProfileName = "content_history"
    method: ContentSearchMethod = "hybrid"
    kinds: ContentSearchKinds = Field(default_factory=list)
    platform: ContentPlatform | None = None
    project_id: Identifier | None = None
    # Briefs use a smaller evidence-plan lifecycle, while ideas and current
    # items use ContentStatus. Search spans all three representations.
    status: ContentSearchStatus | None = None
    pillar_id: Identifier | None = None
    tags: TagValues = Field(default_factory=list)
    token_budget: int | None = Field(default=None, ge=MIN_TOKEN_BUDGET)
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("tags", "kinds")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        return _unique(value, "content search values")


class ContentSearchItemResponse(BaseModel):
    """One V2 result and its retained provenance metadata."""

    kind: Literal["idea", "brief", "content"]
    identifier: str
    title: str
    content: str
    status: str
    platforms: tuple[str, ...]
    tags: tuple[str, ...]
    pillar_id: str | None
    project_ids: tuple[str, ...]
    source_refs: tuple[ContentSourceReference, ...]
    score: float
    methods: tuple[ContentSearchMethod, ...]
    tokens: int
    updated_at: datetime

    @classmethod
    def from_result(cls, item: ContentSearchItem) -> "ContentSearchItemResponse":
        return cls(
            kind=item.kind,
            identifier=item.identifier,
            title=item.title,
            content=item.content,
            status=item.status,
            platforms=item.platforms,
            tags=item.tags,
            pillar_id=item.pillar_id,
            project_ids=item.project_ids,
            source_refs=tuple(
                ContentSourceReference.model_validate(ref) for ref in item.source_refs
            ),
            score=item.score,
            methods=item.methods,
            tokens=item.tokens,
            updated_at=item.updated_at,
        )


class ContentSearchResponse(BaseModel):
    """A token-budgeted content answer from one authorized creator profile."""

    profile_id: str
    retrieval_profile: ContentRetrievalProfileName
    method: ContentSearchMethod
    items: tuple[ContentSearchItemResponse, ...]
    token_budget: int
    tokens_used: int

    @classmethod
    def from_result(
        cls,
        result: ContentSearchResult,
        items: tuple[ContentSearchItem, ...],
    ) -> "ContentSearchResponse":
        return cls(
            profile_id=result.profile_id,
            retrieval_profile=result.retrieval_profile,
            method=result.method,
            items=tuple(ContentSearchItemResponse.from_result(item) for item in items),
            token_budget=result.token_budget,
            tokens_used=sum(item.tokens for item in items),
        )


class ContentIdeaDiscoveryRequest(BaseModel):
    """Bounded evidence discovery; its response is agent input, never a saved idea."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    project_id: Identifier | None = None
    pillar_id: Identifier | None = None
    time_range: DiscoveryTimeRange | None = None
    platform: ContentPlatform | None = None
    limit: int = Field(default=10, ge=1, le=50)


class DiscoveryFreshnessResponse(BaseModel):
    """Stable JSON form of source recency data."""

    updated_at: datetime
    age_days: int
    inside_requested_range: bool

    @classmethod
    def from_value(cls, value: DiscoveryFreshness) -> "DiscoveryFreshnessResponse":
        return cls(
            updated_at=value.updated_at,
            age_days=value.age_days,
            inside_requested_range=value.inside_requested_range,
        )


class DiscoveryEvidenceResponse(BaseModel):
    """A compact attributed input record; the excerpt is data, not instructions."""

    source: ContentSourceReference
    source_type: str
    title: str
    excerpt: str
    tags: tuple[str, ...]
    freshness: DiscoveryFreshnessResponse

    @classmethod
    def from_value(cls, value: DiscoveryEvidence) -> "DiscoveryEvidenceResponse":
        return cls(
            source=value.source,
            source_type=value.source_type,
            title=value.title,
            excerpt=value.excerpt,
            tags=value.tags,
            freshness=DiscoveryFreshnessResponse.from_value(value.freshness),
        )


class SimilarPublishedContentResponse(BaseModel):
    """An explicit, explainable previous-content reference; not novelty scoring."""

    content_id: str
    title: str
    platform: ContentPlatform
    published_at: datetime | None
    reason: str

    @classmethod
    def from_value(cls, value: SimilarPublishedContent) -> "SimilarPublishedContentResponse":
        return cls(
            content_id=value.content_id,
            title=value.title,
            platform=value.platform,
            published_at=value.published_at,
            reason=value.reason,
        )


class ExistingIdeaResponse(BaseModel):
    """A retained unpublished idea made visible to avoid exact re-proposals."""

    idea_id: str
    title: str
    pillar_id: str | None
    platforms: tuple[ContentPlatform, ...]
    updated_at: datetime

    @classmethod
    def from_value(cls, value: ExistingIdea) -> "ExistingIdeaResponse":
        return cls(
            idea_id=value.idea_id,
            title=value.title,
            pillar_id=value.pillar_id,
            platforms=value.platforms,
            updated_at=value.updated_at,
        )


class ContentIdeaCandidateResponse(BaseModel):
    """A source-backed topic label that an agent may choose to save or ignore."""

    topic: str
    source_refs: tuple[ContentSourceReference, ...]
    evidence: tuple[DiscoveryEvidenceResponse, ...]
    related_project_id: str | None
    related_pillar_id: str | None
    suggested_platforms: tuple[ContentPlatform, ...]
    why_useful: tuple[str, ...]
    previous_similar_content: tuple[SimilarPublishedContentResponse, ...]
    freshness: DiscoveryFreshnessResponse

    @classmethod
    def from_value(cls, value: ContentIdeaCandidate) -> "ContentIdeaCandidateResponse":
        return cls(
            topic=value.topic,
            source_refs=value.source_refs,
            evidence=tuple(DiscoveryEvidenceResponse.from_value(item) for item in value.evidence),
            related_project_id=value.related_project_id,
            related_pillar_id=value.related_pillar_id,
            suggested_platforms=value.suggested_platforms,
            why_useful=value.why_useful,
            previous_similar_content=tuple(
                SimilarPublishedContentResponse.from_value(item)
                for item in value.previous_similar_content
            ),
            freshness=DiscoveryFreshnessResponse.from_value(value.freshness),
        )


class ContentIdeaDiscoveryResponse(BaseModel):
    """Complete, read-only discovery material for an agent's explicit review."""

    profile_id: str
    project_id: str | None
    pillar_id: str | None
    platform: ContentPlatform | None
    candidates: tuple[ContentIdeaCandidateResponse, ...]
    existing_unpublished_ideas: tuple[ExistingIdeaResponse, ...]

    @classmethod
    def from_value(cls, value: ContentIdeaDiscovery) -> "ContentIdeaDiscoveryResponse":
        return cls(
            profile_id=value.profile_id,
            project_id=value.project_id,
            pillar_id=value.pillar_id,
            platform=value.platform,
            candidates=tuple(
                ContentIdeaCandidateResponse.from_value(item) for item in value.candidates
            ),
            existing_unpublished_ideas=tuple(
                ExistingIdeaResponse.from_value(item) for item in value.existing_unpublished_ideas
            ),
        )


class ContentBriefPrepareRequest(BaseModel):
    """Ask for a non-persistent evidence brief for one saved idea and platform."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    idea_id: Identifier
    platform: ContentPlatform
    project_id: Identifier | None = None
    pillar_id: Identifier | None = None


class SkillReferenceResponse(BaseModel):
    """A reviewed active skill selected for the brief's input package."""

    skill_id: str
    name: str
    version: str

    @classmethod
    def from_value(cls, value: SkillReference) -> "SkillReferenceResponse":
        return cls(skill_id=value.skill_id, name=value.name, version=value.version)


class ContentBriefPreparationResponse(BaseModel):
    """A draftable evidence plan; it does not create or publish any content."""

    profile_id: str
    idea_id: str
    platform: ContentPlatform
    objective: str
    audience: tuple[str, ...]
    primary_message: str
    supporting_points: tuple[str, ...]
    source_refs: tuple[ContentSourceReference, ...]
    technical_skills: tuple[SkillReferenceResponse, ...]
    content_skill: SkillReferenceResponse | None
    similar_previous_content: tuple[SimilarPublishedContentResponse, ...]
    content_constraints: tuple[str, ...]

    @classmethod
    def from_value(cls, value: ContentBriefPreparation) -> "ContentBriefPreparationResponse":
        return cls(
            profile_id=value.profile_id,
            idea_id=value.idea_id,
            platform=value.platform,
            objective=value.objective,
            audience=value.audience,
            primary_message=value.primary_message,
            supporting_points=value.supporting_points,
            source_refs=value.source_refs,
            technical_skills=tuple(
                SkillReferenceResponse.from_value(item) for item in value.technical_skills
            ),
            content_skill=None
            if value.content_skill is None
            else SkillReferenceResponse.from_value(value.content_skill),
            similar_previous_content=tuple(
                SimilarPublishedContentResponse.from_value(item)
                for item in value.similar_previous_content
            ),
            content_constraints=value.content_constraints,
        )


class ContentNoveltyRequest(BaseModel):
    """Ask for advisory history overlap for a proposed, unsaved content angle."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    title: ShortText
    description: ContentText
    tags: TagValues = Field(default_factory=list)
    pillar_id: Identifier | None = None
    platform: ContentPlatform | None = None
    project_id: Identifier | None = None
    method: ContentSearchMethod = "hybrid"
    token_budget: int | None = Field(default=None, ge=MIN_TOKEN_BUDGET)
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        return _unique(value, "content novelty tags")


class SimilarContentResponse(BaseModel):
    """One explainable history match; its scores are advisory evidence only."""

    kind: Literal["idea", "brief", "content"]
    identifier: str
    title: str
    platforms: tuple[str, ...]
    status: str
    pillar_id: str | None
    project_ids: tuple[str, ...]
    source_refs: tuple[ContentSourceReference, ...]
    retrieval_score: float
    topic_overlap: float = Field(ge=0.0, le=1.0)
    angle_overlap: float = Field(ge=0.0, le=1.0)
    shared_topic_terms: tuple[str, ...]
    shared_angle_terms: tuple[str, ...]
    classification: NoveltyClassification

    @classmethod
    def from_value(cls, value: SimilarContent) -> "SimilarContentResponse":
        return cls(
            kind=value.kind,
            identifier=value.identifier,
            title=value.title,
            platforms=value.platforms,
            status=value.status,
            pillar_id=value.pillar_id,
            project_ids=value.project_ids,
            source_refs=tuple(
                ContentSourceReference.model_validate(ref) for ref in value.source_refs
            ),
            retrieval_score=value.retrieval_score,
            topic_overlap=value.topic_overlap,
            angle_overlap=value.angle_overlap,
            shared_topic_terms=value.shared_topic_terms,
            shared_angle_terms=value.shared_angle_terms,
            classification=value.classification,
        )


class ContentNoveltyResponse(BaseModel):
    """A non-authoritative novelty assessment backed by visible prior content."""

    profile_id: str
    similar_content: tuple[SimilarContentResponse, ...]
    topic_overlap: float = Field(ge=0.0, le=1.0)
    angle_overlap: float = Field(ge=0.0, le=1.0)
    classification: NoveltyClassification
    advisory: bool

    @classmethod
    def from_value(cls, value: ContentNoveltyResult) -> "ContentNoveltyResponse":
        return cls(
            profile_id=value.profile_id,
            similar_content=tuple(
                SimilarContentResponse.from_value(item) for item in value.similar_content
            ),
            topic_overlap=value.topic_overlap,
            angle_overlap=value.angle_overlap,
            classification=value.classification,
            advisory=value.advisory,
        )


class ContentRepurposeRequest(BaseModel):
    """Request a structured plan for a stored source, never a target draft."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    source_kind: RepurposeSourceKind = "content"
    targets: PlatformValues
    method: ContentSearchMethod = "hybrid"
    token_budget: int | None = Field(default=None, ge=MIN_TOKEN_BUDGET)
    limit: int = Field(default=5, ge=1, le=50)

    @field_validator("targets")
    @classmethod
    def unique_targets(cls, value: list[ContentPlatform]) -> list[ContentPlatform]:
        return _unique(value, "repurpose target platforms")


class RelatedContentResponse(BaseModel):
    """A compact related-history record retained in a repurpose plan."""

    kind: Literal["idea", "brief", "content"]
    identifier: str
    title: str
    platforms: tuple[str, ...]
    status: str
    source_refs: tuple[ContentSourceReference, ...]

    @classmethod
    def from_value(cls, value: RelatedContent) -> "RelatedContentResponse":
        return cls(
            kind=value.kind,
            identifier=value.identifier,
            title=value.title,
            platforms=value.platforms,
            status=value.status,
            source_refs=tuple(
                ContentSourceReference.model_validate(ref) for ref in value.source_refs
            ),
        )


class RepurposeTargetPlanResponse(BaseModel):
    """Destination-specific planning fields; the content skill holds instructions."""

    platform: ContentPlatform
    what_to_shorten: tuple[str, ...]
    what_requires_expansion: tuple[str, ...]
    target_platform_differences: tuple[str, ...]
    target_content_skill: SkillReferenceResponse | None
    previous_related_content: tuple[RelatedContentResponse, ...]

    @classmethod
    def from_value(cls, value: RepurposeTargetPlan) -> "RepurposeTargetPlanResponse":
        return cls(
            platform=value.platform,
            what_to_shorten=value.what_to_shorten,
            what_requires_expansion=value.what_requires_expansion,
            target_platform_differences=value.target_platform_differences,
            target_content_skill=None
            if value.target_content_skill is None
            else SkillReferenceResponse.from_value(value.target_content_skill),
            previous_related_content=tuple(
                RelatedContentResponse.from_value(item) for item in value.previous_related_content
            ),
        )


class ContentRepurposeResponse(BaseModel):
    """Complete source-preserving plan; it intentionally has no generated body field."""

    profile_id: str
    source_kind: RepurposeSourceKind
    source_id: str
    source_platform: ContentPlatform | None
    core_thesis: str
    reusable_supporting_points: tuple[str, ...]
    examples: tuple[str, ...]
    source_refs: tuple[ContentSourceReference, ...]
    targets: tuple[RepurposeTargetPlanResponse, ...]

    @classmethod
    def from_value(cls, value: RepurposePlan) -> "ContentRepurposeResponse":
        return cls(
            profile_id=value.profile_id,
            source_kind=value.source_kind,
            source_id=value.source_id,
            source_platform=value.source_platform,
            core_thesis=value.core_thesis,
            reusable_supporting_points=value.reusable_supporting_points,
            examples=value.examples,
            source_refs=value.source_refs,
            targets=tuple(RepurposeTargetPlanResponse.from_value(item) for item in value.targets),
        )


class ContentExportRequest(BaseModel):
    """Choose a local/manual representation; this never selects a publisher."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    mode: ExportMode = "stdout"


class ContentExportResponse(BaseModel):
    """A formatted representation that a client may print or write locally."""

    profile_id: str
    content_id: str
    platform: ContentPlatform
    mode: ExportMode
    format: ExportFormat
    text: str
    suggested_file_name: str
    suggested_path: str | None
    metadata: dict[str, object]

    @classmethod
    def from_value(cls, value: PlatformExport) -> "ContentExportResponse":
        return cls(
            profile_id=value.profile_id,
            content_id=value.content_id,
            platform=value.platform,
            mode=value.mode,
            format=value.format,
            text=value.text,
            suggested_file_name=value.suggested_file_name,
            suggested_path=value.suggested_path,
            metadata=dict(value.metadata),
        )


class ContentPublicationReviewRequest(BaseModel):
    """Review one persisted current item; clients cannot submit replacement prose here."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier


class ContentPublicationFindingResponse(BaseModel):
    """Explain a finding without returning the secret or personal value that matched."""

    finding_type: PublicationFindingType
    severity: PublicationFindingSeverity
    field: str
    location: str
    detail: str

    @classmethod
    def from_value(cls, value: PublicationFinding) -> "ContentPublicationFindingResponse":
        return cls(**value.model_dump())


class ContentPublicationReviewResponse(BaseModel):
    """An authorized review result with private provenance metadata, never content prose."""

    profile_id: str
    content_id: str
    current_status: ContentStatus
    publishable: bool
    findings: tuple[ContentPublicationFindingResponse, ...]
    project_ids: tuple[str, ...]
    source_refs: tuple[ContentSourceReference, ...]
    disclaimer: str

    @classmethod
    def from_value(
        cls, value: PublicationReview, *, current_status: ContentStatus
    ) -> "ContentPublicationReviewResponse":
        return cls(
            profile_id=value.profile_id,
            content_id=value.content_id,
            current_status=current_status,
            publishable=value.publishable,
            findings=tuple(
                ContentPublicationFindingResponse.from_value(finding) for finding in value.findings
            ),
            project_ids=value.project_ids,
            source_refs=value.source_refs,
            disclaimer=value.disclaimer,
        )


class ContentApprovalRequest(BaseModel):
    """Explicitly approve the exact revision that was put into review."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    expected_revision: int = Field(ge=1)


class ContentApprovalResponse(BaseModel):
    """The approved current record and the redacted scan used for that decision."""

    content: ContentItemResponse
    review: ContentPublicationReviewResponse
