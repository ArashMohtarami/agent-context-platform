"""V3 research persistence models.

These documents are deliberately inert data contracts.  They preserve source
provenance, scope, version information, and review state, but they do not
fetch, parse, retrieve, evaluate, promote, or execute anything.  Those
operations belong to later V3 services.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.actor import ActorProvenance, system_actor
from app.models.common import (
    ContentText,
    Identifier,
    Metadata,
    MongoDocumentModel,
    ShortText,
    Tags,
    normalize_mongo_timestamp,
    populate_deterministic_id,
)
from app.models.embedding import EmbeddingFields
from app.models.freshness import FreshnessClass, RevalidationReason
from app.models.skill import SemanticVersion


class ResearchProjectType(StrEnum):
    """The question category; it does not change storage or trust rules."""

    TECHNICAL_RESEARCH = "technical_research"
    ARCHITECTURE_EVALUATION = "architecture_evaluation"
    TECHNOLOGY_COMPARISON = "technology_comparison"
    INCIDENT_INVESTIGATION = "incident_investigation"
    CONTENT_RESEARCH = "content_research"
    LEARNING = "learning"
    DOCUMENTATION_RESEARCH = "documentation_research"
    MARKET_RESEARCH = "market_research"
    REPOSITORY_ANALYSIS = "repository_analysis"


class SourceClass(StrEnum):
    """Descriptive source metadata, explicitly not a truth assessment."""

    PRIMARY = "primary"
    OFFICIAL_DOCUMENTATION = "official_documentation"
    STANDARD = "standard"
    SOURCE_CODE = "source_code"
    ACADEMIC = "academic"
    SECONDARY = "secondary"
    COMMUNITY = "community"
    USER_PROVIDED = "user_provided"
    GENERATED = "generated"
    UNKNOWN = "unknown"


class ResearchSourceType(StrEnum):
    """The registered source medium, before any acquisition is attempted."""

    MANUAL = "manual"
    URL = "url"
    FILE = "file"
    MARKDOWN = "markdown"
    PDF = "pdf"
    GIT = "git"
    GITHUB = "github"
    DOCUMENTATION = "documentation"
    RSS = "rss"


class ResearchProjectStatus(StrEnum):
    """Lifecycle for a research workspace, without implying evidence truth."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ResearchRecordStatus(StrEnum):
    """Lifecycle shared by source-derived records that may be superseded."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"


class ResearchNoteStatus(StrEnum):
    """An authored note's workflow state; notes are never trusted knowledge."""

    WORKING = "working"
    REVIEWED = "reviewed"
    ARCHIVED = "archived"


class ClaimState(StrEnum):
    """The explicit evaluation state of a claim."""

    UNREVIEWED = "unreviewed"
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTESTED = "contested"
    UNSUPPORTED = "unsupported"
    OUTDATED = "outdated"


class CitationRelation(StrEnum):
    """How one cited excerpt relates to a claim."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"
    BACKGROUND = "background"


class ResearchBriefStatus(StrEnum):
    """Lifecycle for a structured synthesis, separate from a claim verdict."""

    DRAFT = "draft"
    READY = "ready"
    ARCHIVED = "archived"


class CandidateState(StrEnum):
    """The review state of a proposed durable knowledge change."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_CHANGES = "needs_changes"
    PROMOTED = "promoted"
    SUPERSEDED = "superseded"


class SkillUpdateProposalStatus(StrEnum):
    """Lifecycle of an inert generated skill-change proposal.

    Phase 11 creates only pending proposals. Review, Git changes, and import
    remain separate future operations, so no status transition is exposed here.
    """

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class IngestionJobState(StrEnum):
    """The persisted state of a future constrained ingestion job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CandidateType(StrEnum):
    """The intended destination of a reviewed candidate, never an action."""

    KNOWLEDGE = "knowledge"
    MEMORY = "memory"
    PROJECT_CONTEXT = "project_context"
    ARCHITECTURE_DECISION = "architecture_decision"
    SKILL_UPDATE = "skill_update"
    CONTENT_FACT = "content_fact"


class DocumentContentStorage(StrEnum):
    """What was retained, making copyright-sensitive storage auditable."""

    EXTRACT = "extract"
    FULL = "full"
    METADATA_ONLY = "metadata_only"


class ResearchAuthorization(BaseModel):
    """Persisted scope metadata consumed by the existing V1 authorization layer.

    An empty ``project_ids`` list describes research that is global to the
    installation.  Later V3 services must require the existing V1 project
    permission for every non-global project in this metadata; this model adds
    no new scope vocabulary and makes no authorization decision itself.
    """

    model_config = ConfigDict(extra="forbid")

    project_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    owner_key_id: Identifier | None = None

    @field_validator("project_ids")
    @classmethod
    def unique_project_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("authorization project_ids must be unique")
        return value


class CitationLocator(BaseModel):
    """The strongest available, source-specific location for an evidence excerpt."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(default=None, min_length=1, max_length=2_048)
    anchor: str | None = Field(default=None, min_length=1, max_length=1_024)
    path: str | None = Field(default=None, min_length=1, max_length=2_048)
    page: int | None = Field(default=None, ge=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    commit: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_location(self) -> "CitationLocator":
        if not any(
            value is not None
            for value in (self.url, self.anchor, self.path, self.page, self.line_start, self.commit)
        ):
            raise ValueError("a citation locator must identify a source location")
        if self.line_end is not None and self.line_start is None:
            raise ValueError("line_end requires line_start")
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end must be on or after line_start")
        return self


class ResearchSourceReference(BaseModel):
    """A provenance link used by notes and briefs without copying source text."""

    model_config = ConfigDict(extra="forbid")

    source_id: Identifier
    snapshot_id: Identifier | None = None
    document_id: Identifier | None = None
    chunk_id: Identifier | None = None


class ResearchScopedDocument(MongoDocumentModel):
    """A record grouped under one research project and carrying access metadata."""

    model_config = ConfigDict(validate_default=True)

    research_project_id: Identifier
    authorization: ResearchAuthorization = Field(default_factory=ResearchAuthorization)
    actor: ActorProvenance = Field(default_factory=system_actor)


class ResearchTaggedDocument(ResearchScopedDocument):
    """A research record that may be grouped by bounded, exact tags."""

    tags: Tags = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("tags must be unique")
        return value


class ResearchProjectDocument(ResearchTaggedDocument):
    """The durable boundary for one evidence-gathering question."""

    name: ShortText
    question: ContentText
    type: ResearchProjectType
    related_project_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    status: ResearchProjectStatus = ResearchProjectStatus.ACTIVE

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value, namespace="research_project", fields=("research_project_id",)
        )

    @field_validator("related_project_ids")
    @classmethod
    def unique_related_projects(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("related_project_ids must be unique")
        return value

    @model_validator(mode="after")
    def validate_identity_and_scope(self) -> "ResearchProjectDocument":
        if self.id != self.identity_id("research_project", self.research_project_id):
            raise ValueError("_id does not match research_project_id")
        if set(self.related_project_ids) != set(self.authorization.project_ids):
            raise ValueError("authorization project_ids must match related_project_ids")
        return self


class ResearchSourceDocument(ResearchTaggedDocument):
    """One registered external or internal source; no fetching occurs here."""

    source_id: Identifier
    source_type: ResearchSourceType
    uri: str = Field(min_length=1, max_length=2_048)
    canonical_uri: str | None = Field(default=None, min_length=1, max_length=2_048)
    title: ShortText
    publisher: str | None = Field(default=None, min_length=1, max_length=500)
    author: str | None = Field(default=None, min_length=1, max_length=500)
    published_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime | None = None
    valid_from: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None
    source_class: SourceClass = SourceClass.UNKNOWN
    trust_metadata: Metadata = Field(default_factory=dict)
    status: ResearchRecordStatus = ResearchRecordStatus.ACTIVE

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(value, namespace="research_source", fields=("source_id",))

    @field_validator("published_at", "retrieved_at", "valid_from", "valid_until")
    @classmethod
    def normalize_source_times(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_mongo_timestamp(value)

    @model_validator(mode="after")
    def validate_identity_and_validity(self) -> "ResearchSourceDocument":
        if self.id != self.identity_id("research_source", self.source_id):
            raise ValueError("_id does not match source_id")
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until < self.valid_from
        ):
            raise ValueError("valid_until must be on or after valid_from")
        return self


class SourceSnapshotDocument(ResearchScopedDocument):
    """One immutable observation of a source version at a retrieval instant."""

    source_id: Identifier
    snapshot_id: Identifier
    retrieved_at: AwareDatetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    version_identifier: str | None = Field(default=None, min_length=1, max_length=2_048)
    metadata: Metadata = Field(default_factory=dict)
    status: ResearchRecordStatus = ResearchRecordStatus.ACTIVE

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value,
            namespace="source_snapshot",
            fields=("source_id", "snapshot_id"),
        )

    @field_validator("retrieved_at")
    @classmethod
    def normalize_retrieved_at(cls, value: datetime) -> datetime:
        return normalize_mongo_timestamp(value)

    @model_validator(mode="after")
    def validate_id(self) -> "SourceSnapshotDocument":
        if self.id != self.identity_id("source_snapshot", self.source_id, self.snapshot_id):
            raise ValueError("_id does not match source_id and snapshot_id")
        return self


class ResearchDocumentDocument(ResearchTaggedDocument):
    """Normalized retained source material, with explicit copyright retention mode."""

    source_id: Identifier
    snapshot_id: Identifier
    document_id: Identifier
    title: ShortText
    content_type: str = Field(min_length=1, max_length=255)
    language: str = Field(default="und", min_length=1, max_length=64)
    content: ContentText | None = None
    content_storage: DocumentContentStorage = DocumentContentStorage.EXTRACT
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_class: SourceClass
    status: ResearchRecordStatus = ResearchRecordStatus.ACTIVE

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value,
            namespace="research_document",
            fields=("source_id", "snapshot_id", "document_id"),
        )

    @model_validator(mode="after")
    def validate_retention_and_id(self) -> "ResearchDocumentDocument":
        expected = self.identity_id(
            "research_document", self.source_id, self.snapshot_id, self.document_id
        )
        if self.id != expected:
            raise ValueError("_id does not match source_id, snapshot_id, and document_id")
        if (
            self.content_storage is DocumentContentStorage.METADATA_ONLY
            and self.content is not None
        ):
            raise ValueError("metadata_only documents must not retain content")
        if (
            self.content_storage is not DocumentContentStorage.METADATA_ONLY
            and self.content is None
        ):
            raise ValueError("retained document content is required for this storage mode")
        return self


class ResearchChunkDocument(EmbeddingFields, ResearchTaggedDocument):
    """A citable, embeddable excerpt; vectors carry the existing local-model metadata."""

    source_id: Identifier
    snapshot_id: Identifier
    document_id: Identifier
    chunk_id: Identifier
    chunk_index: int = Field(ge=0)
    section: str | None = Field(default=None, min_length=1, max_length=500)
    content: ContentText
    source_class: SourceClass
    locator: CitationLocator
    metadata: Metadata = Field(default_factory=dict)
    status: ResearchRecordStatus = ResearchRecordStatus.ACTIVE

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value,
            namespace="research_chunk",
            fields=("document_id", "chunk_id"),
        )

    @model_validator(mode="after")
    def validate_id(self) -> "ResearchChunkDocument":
        if self.id != self.identity_id("research_chunk", self.document_id, self.chunk_id):
            raise ValueError("_id does not match document_id and chunk_id")
        return self


class ResearchNoteDocument(EmbeddingFields, ResearchTaggedDocument):
    """An authored working note with explicit source references, not trusted knowledge."""

    note_id: Identifier
    title: ShortText
    body: ContentText
    source_refs: list[ResearchSourceReference] = Field(default_factory=list, max_length=100)
    created_by: Identifier | None = None
    status: ResearchNoteStatus = ResearchNoteStatus.WORKING

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value, namespace="research_note", fields=("research_project_id", "note_id")
        )

    @field_validator("source_refs")
    @classmethod
    def unique_source_refs(
        cls, value: list[ResearchSourceReference]
    ) -> list[ResearchSourceReference]:
        keys = [item.model_dump_json() for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("source_refs must be unique")
        return value

    @model_validator(mode="after")
    def validate_id(self) -> "ResearchNoteDocument":
        if self.id != self.identity_id("research_note", self.research_project_id, self.note_id):
            raise ValueError("_id does not match research_project_id and note_id")
        return self


class ResearchClaimDocument(EmbeddingFields, ResearchTaggedDocument):
    """A statement being evaluated; confidence is never a retrieval score."""

    claim_id: Identifier
    statement: ContentText
    status: ClaimState = ClaimState.UNREVIEWED
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_rationale: str | None = Field(default=None, min_length=1, max_length=4_000)
    supporting_citation_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    contradicting_citation_ids: list[Identifier] = Field(default_factory=list, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value, namespace="research_claim", fields=("research_project_id", "claim_id")
        )

    @field_validator("supporting_citation_ids", "contradicting_citation_ids")
    @classmethod
    def unique_citation_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("citation identifiers must be unique")
        return value

    @model_validator(mode="after")
    def validate_claim(self) -> "ResearchClaimDocument":
        if self.id != self.identity_id("research_claim", self.research_project_id, self.claim_id):
            raise ValueError("_id does not match research_project_id and claim_id")
        overlap = set(self.supporting_citation_ids) & set(self.contradicting_citation_ids)
        if overlap:
            raise ValueError("a citation cannot support and contradict the same claim")
        return self


class ResearchCitationDocument(ResearchScopedDocument):
    """A durable relation between a claim and a source versioned excerpt."""

    claim_id: Identifier
    citation_id: Identifier
    source_id: Identifier
    snapshot_id: Identifier
    chunk_id: Identifier | None = None
    source_class: SourceClass
    locator: CitationLocator
    evidence_excerpt: ContentText
    relation: CitationRelation
    status: ResearchRecordStatus = ResearchRecordStatus.ACTIVE

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value, namespace="research_citation", fields=("claim_id", "citation_id")
        )

    @model_validator(mode="after")
    def validate_id(self) -> "ResearchCitationDocument":
        if self.id != self.identity_id("research_citation", self.claim_id, self.citation_id):
            raise ValueError("_id does not match claim_id and citation_id")
        return self


class ResearchBriefClaimReference(BaseModel):
    """The explicit state a brief observed for one claim; it is not a new verdict."""

    model_config = ConfigDict(extra="forbid")

    claim_id: Identifier
    statement: ContentText
    status: ClaimState
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ResearchBriefCitationReference(BaseModel):
    """A frozen, locatable citation reference retained by a structured brief."""

    model_config = ConfigDict(extra="forbid")

    citation_id: Identifier
    claim_id: Identifier
    source_id: Identifier
    snapshot_id: Identifier
    chunk_id: Identifier | None = None
    source_class: SourceClass
    locator: CitationLocator
    evidence_excerpt: ContentText
    relation: CitationRelation


class ResearchBriefContradiction(BaseModel):
    """A visible conflict between persisted citation relations; no side is selected."""

    model_config = ConfigDict(extra="forbid")

    claim_id: Identifier
    statement: ContentText
    supporting_citation_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    contradicting_citation_ids: list[Identifier] = Field(default_factory=list, max_length=200)

    @field_validator("supporting_citation_ids", "contradicting_citation_ids")
    @classmethod
    def unique_citation_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("contradiction citation identifiers must be unique")
        return value

    @model_validator(mode="after")
    def no_overlapping_citations(self) -> "ResearchBriefContradiction":
        overlap = set(self.supporting_citation_ids) & set(self.contradicting_citation_ids)
        if overlap:
            raise ValueError(
                "a contradiction citation cannot support and contradict the same claim"
            )
        return self


class ResearchBriefDocument(EmbeddingFields, ResearchTaggedDocument):
    """A structured research synthesis that keeps evidence and unknowns visible."""

    brief_id: Identifier
    title: ShortText
    question: ContentText
    scope: ContentText
    summary: ContentText
    key_findings: list[ContentText] = Field(default_factory=list, max_length=100)
    # Phase 0 identifier lists remain for backward-compatible reads. Phase 8
    # adds immutable structured references alongside them so provenance and
    # citation locators survive brief construction without a promotion step.
    evidence_citation_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    contradicting_citation_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    source_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    claim_refs: list[ResearchBriefClaimReference] = Field(default_factory=list, max_length=200)
    citation_refs: list[ResearchBriefCitationReference] = Field(
        default_factory=list, max_length=200
    )
    contradictions: list[ResearchBriefContradiction] = Field(default_factory=list, max_length=200)
    source_refs: list[ResearchSourceReference] = Field(default_factory=list, max_length=200)
    unknowns: list[ContentText] = Field(default_factory=list, max_length=100)
    next_questions: list[ContentText] = Field(default_factory=list, max_length=100)
    status: ResearchBriefStatus = ResearchBriefStatus.DRAFT

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value, namespace="research_brief", fields=("research_project_id", "brief_id")
        )

    @field_validator(
        "key_findings",
        "evidence_citation_ids",
        "contradicting_citation_ids",
        "unknowns",
        "source_ids",
        "next_questions",
    )
    @classmethod
    def unique_brief_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("research brief values must be unique")
        return value

    @model_validator(mode="after")
    def validate_brief(self) -> "ResearchBriefDocument":
        if self.id != self.identity_id("research_brief", self.research_project_id, self.brief_id):
            raise ValueError("_id does not match research_project_id and brief_id")
        overlap = set(self.evidence_citation_ids) & set(self.contradicting_citation_ids)
        if overlap:
            raise ValueError("a brief citation cannot be both supporting and contradicting")
        claim_ref_ids = [item.claim_id for item in self.claim_refs]
        citation_ref_ids = [item.citation_id for item in self.citation_refs]
        citation_ref_keys = [(item.claim_id, item.citation_id) for item in self.citation_refs]
        contradiction_claim_ids = [item.claim_id for item in self.contradictions]
        source_ref_keys = [item.model_dump_json() for item in self.source_refs]
        if len(claim_ref_ids) != len(set(claim_ref_ids)):
            raise ValueError("brief claim_refs must be unique by claim_id")
        if len(citation_ref_keys) != len(set(citation_ref_keys)):
            raise ValueError("brief citation_refs must be unique by claim_id and citation_id")
        if len(contradiction_claim_ids) != len(set(contradiction_claim_ids)):
            raise ValueError("brief contradictions must be unique by claim_id")
        if len(source_ref_keys) != len(set(source_ref_keys)):
            raise ValueError("brief source_refs must be unique")
        if self.citation_refs and not (
            set(self.evidence_citation_ids) | set(self.contradicting_citation_ids)
        ).issubset(citation_ref_ids):
            raise ValueError("brief citation identifiers must have structured references")
        if self.source_refs and not set(self.source_ids).issubset(
            {item.source_id for item in self.source_refs}
        ):
            raise ValueError("brief source identifiers must have structured references")
        return self


class KnowledgeCandidateDocument(EmbeddingFields, ResearchTaggedDocument):
    """A review-gated proposal; persistence alone cannot promote it anywhere."""

    candidate_id: Identifier
    candidate_type: CandidateType
    target_project_id: Identifier | None = None
    target_skill_id: Identifier | None = None
    current_skill_version: str | None = Field(default=None, min_length=1, max_length=64)
    title: ShortText
    content: ContentText
    source_claim_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    source_citation_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_rationale: str | None = Field(default=None, min_length=1, max_length=4_000)
    freshness_class: FreshnessClass = FreshnessClass.STABLE
    review_after: AwareDatetime | None = None
    revalidation_of_candidate_id: Identifier | None = None
    revalidation_reasons: list[RevalidationReason] = Field(default_factory=list, max_length=20)
    status: CandidateState = CandidateState.PENDING_REVIEW
    reviewer: Identifier | None = None
    reviewed_at: AwareDatetime | None = None
    review_notes: str | None = Field(default=None, min_length=1, max_length=4_000)

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value, namespace="knowledge_candidate", fields=("research_project_id", "candidate_id")
        )

    @field_validator("source_claim_ids", "source_citation_ids", "revalidation_reasons")
    @classmethod
    def unique_candidate_sources(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("candidate source identifiers must be unique")
        return value

    @field_validator("reviewed_at", "review_after")
    @classmethod
    def normalize_reviewed_at(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_mongo_timestamp(value)

    @model_validator(mode="after")
    def validate_candidate(self) -> "KnowledgeCandidateDocument":
        if self.id != self.identity_id(
            "knowledge_candidate", self.research_project_id, self.candidate_id
        ):
            raise ValueError("_id does not match research_project_id and candidate_id")
        if (self.reviewer is None) != (self.reviewed_at is None):
            raise ValueError("reviewer and reviewed_at must be stored together")
        if self.status is CandidateState.PENDING_REVIEW and any(
            value is not None for value in (self.reviewer, self.reviewed_at, self.review_notes)
        ):
            raise ValueError("pending_review candidates cannot have review metadata")
        if self.status in {
            CandidateState.APPROVED,
            CandidateState.REJECTED,
            CandidateState.NEEDS_CHANGES,
        } and (self.reviewer is None or self.reviewed_at is None):
            raise ValueError("reviewed candidates require reviewer and reviewed_at")
        if self.revalidation_of_candidate_id is None and self.revalidation_reasons:
            raise ValueError("revalidation reasons require the original candidate identifier")
        if self.revalidation_of_candidate_id is not None and not self.revalidation_reasons:
            raise ValueError("revalidation candidates require at least one observable reason")
        if self.revalidation_of_candidate_id == self.candidate_id:
            raise ValueError("a revalidation candidate cannot reference itself")
        if (
            self.candidate_type
            in {
                CandidateType.PROJECT_CONTEXT,
                CandidateType.ARCHITECTURE_DECISION,
            }
            and self.target_project_id is None
        ):
            raise ValueError("project-bound candidates require target_project_id")
        if self.candidate_type is CandidateType.SKILL_UPDATE and self.target_skill_id is None:
            raise ValueError("skill_update candidates require target_skill_id")
        if self.candidate_type is not CandidateType.SKILL_UPDATE and (
            self.target_skill_id is not None or self.current_skill_version is not None
        ):
            raise ValueError("skill target fields are only valid for skill_update candidates")
        return self


class SkillUpdateProposalDocument(ResearchScopedDocument):
    """An inert, generated proposal to change canonical Git-backed skill Markdown.

    The proposed patch is untrusted data. Persisting or exporting it never edits
    a canonical Markdown file, invokes Git, imports a skill, or creates a V1
    ``SkillDocument``. A later reviewed Git workflow owns those operations.
    """

    proposal_id: Identifier
    skill_id: Identifier
    current_version: SemanticVersion
    proposed_change: ContentText
    proposed_patch: ContentText
    source_claim_ids: list[Identifier] = Field(min_length=1, max_length=100)
    source_citation_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    reason: ContentText
    status: SkillUpdateProposalStatus = SkillUpdateProposalStatus.PENDING_REVIEW
    trust_level: Literal["generated"] = "generated"

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value,
            namespace="skill_update_proposal",
            fields=("research_project_id", "proposal_id"),
        )

    @field_validator("source_claim_ids", "source_citation_ids")
    @classmethod
    def unique_provenance_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("skill update proposal provenance identifiers must be unique")
        return value

    @model_validator(mode="after")
    def validate_proposal(self) -> "SkillUpdateProposalDocument":
        if self.id != self.identity_id(
            "skill_update_proposal", self.research_project_id, self.proposal_id
        ):
            raise ValueError("_id does not match research_project_id and proposal_id")
        if self.trust_level != "generated":
            raise ValueError("skill update proposals must remain generated until Git review")
        return self


class PromotionEventDocument(ResearchScopedDocument):
    """Append-only audit data for a future reviewed promotion decision."""

    candidate_id: Identifier
    event_id: Identifier
    target_type: CandidateType
    target_id: Identifier | None = None
    reviewer: Identifier
    decision: CandidateState
    decided_at: AwareDatetime
    reason: str | None = Field(default=None, min_length=1, max_length=4_000)

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(
            value, namespace="promotion_event", fields=("candidate_id", "event_id")
        )

    @field_validator("decided_at")
    @classmethod
    def normalize_decided_at(cls, value: datetime) -> datetime:
        return normalize_mongo_timestamp(value)

    @model_validator(mode="after")
    def validate_id(self) -> "PromotionEventDocument":
        if self.id != self.identity_id("promotion_event", self.candidate_id, self.event_id):
            raise ValueError("_id does not match candidate_id and event_id")
        return self


class IngestionJobDocument(ResearchScopedDocument):
    """Persisted job metadata only; it does not create a worker or queue."""

    job_id: Identifier
    source_id: Identifier
    snapshot_id: Identifier | None = None
    status: IngestionJobState = IngestionJobState.PENDING
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    error: str | None = Field(default=None, min_length=1, max_length=4_000)
    documents_created: int = Field(default=0, ge=0)
    chunks_created: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        return populate_deterministic_id(value, namespace="ingestion_job", fields=("job_id",))

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_job_times(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_mongo_timestamp(value)

    @model_validator(mode="after")
    def validate_job(self) -> "IngestionJobDocument":
        if self.id != self.identity_id("ingestion_job", self.job_id):
            raise ValueError("_id does not match job_id")
        if self.completed_at is not None and self.started_at is None:
            raise ValueError("completed_at requires started_at")
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("completed_at must be on or after started_at")
        return self


__all__ = [
    "CandidateState",
    "CandidateType",
    "CitationLocator",
    "CitationRelation",
    "ClaimState",
    "DocumentContentStorage",
    "IngestionJobDocument",
    "IngestionJobState",
    "KnowledgeCandidateDocument",
    "PromotionEventDocument",
    "ResearchAuthorization",
    "ResearchBriefDocument",
    "ResearchBriefStatus",
    "ResearchChunkDocument",
    "ResearchCitationDocument",
    "ResearchClaimDocument",
    "ResearchDocumentDocument",
    "ResearchNoteDocument",
    "ResearchNoteStatus",
    "ResearchProjectDocument",
    "ResearchProjectStatus",
    "ResearchProjectType",
    "ResearchRecordStatus",
    "ResearchSourceDocument",
    "ResearchSourceReference",
    "ResearchSourceType",
    "SkillUpdateProposalDocument",
    "SkillUpdateProposalStatus",
    "SourceClass",
    "SourceSnapshotDocument",
]
