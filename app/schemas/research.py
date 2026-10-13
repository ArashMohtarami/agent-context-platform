"""V3 research transport contracts.

Phases 1 and 2 consume the research-project and source contracts below.  The remaining
aliases make the Phase 0 persistence shapes available to their later, explicitly
requested V3 phases without activating a workflow for them.
"""

from base64 import b64decode
from binascii import Error as BinasciiError
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.actor import ActorProvenance
from app.models.common import ContentText, Identifier, Metadata, ShortText
from app.models.freshness import FreshnessClass, RevalidationReason
from app.models.promotion import PromotionProvenance
from app.models.research import (
    CandidateState,
    CandidateType,
    CitationLocator,
    CitationRelation,
    ClaimState,
    IngestionJobDocument,
    IngestionJobState,
    KnowledgeCandidateDocument,
    PromotionEventDocument,
    ResearchBriefCitationReference,
    ResearchBriefClaimReference,
    ResearchBriefContradiction,
    ResearchBriefDocument,
    ResearchBriefStatus,
    ResearchChunkDocument,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchDocumentDocument,
    ResearchNoteDocument,
    ResearchNoteStatus,
    ResearchProjectDocument,
    ResearchProjectStatus,
    ResearchProjectType,
    ResearchRecordStatus,
    ResearchSourceDocument,
    ResearchSourceReference,
    ResearchSourceType,
    SkillUpdateProposalDocument,
    SkillUpdateProposalStatus,
    SourceClass,
    SourceSnapshotDocument,
)
from app.services.research_evidence_service import (
    CitationEvidence,
    EvidenceConflict,
    EvidencePacket,
)
from app.services.research_search_service import ResearchSearchItem, ResearchSearchResult
from app.services.revalidation_service import RevalidationProposal, SourceChange, StaleKnowledge

type ResearchProjectSchema = ResearchProjectDocument
type ResearchSourceSchema = ResearchSourceDocument
type ResearchDocumentSchema = ResearchDocumentDocument
type ResearchChunkSchema = ResearchChunkDocument
type ResearchNoteSchema = ResearchNoteDocument
type ResearchClaimSchema = ResearchClaimDocument
type ResearchCitationSchema = ResearchCitationDocument
type ResearchBriefSchema = ResearchBriefDocument
type KnowledgeCandidateSchema = KnowledgeCandidateDocument
type SkillUpdateProposalSchema = SkillUpdateProposalDocument
type PromotionEventSchema = PromotionEventDocument
type IngestionJobSchema = IngestionJobDocument
type SourceSnapshotSchema = SourceSnapshotDocument

ResearchProjectPhaseOneStatus = Literal["active", "archived"]


class ResearchPagination(BaseModel):
    """Bounded keyset pagination input shared by V3 endpoints."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=1_024)


class ResearchPageSchema[T](BaseModel):
    """Transport representation of the existing keyset ``Page`` primitive."""

    model_config = ConfigDict(frozen=True)

    items: tuple[T, ...]
    next_cursor: str | None = None


class ResearchProjectCreateRequest(BaseModel):
    """Create only the Phase 1 research-project metadata boundary."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    name: ShortText
    question: ContentText
    type: ResearchProjectType
    related_project_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    status: ResearchProjectPhaseOneStatus = "active"

    @field_validator("related_project_ids")
    @classmethod
    def unique_related_projects(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("related_project_ids must be unique")
        return value


class ResearchProjectUpdateRequest(BaseModel):
    """A revision-guarded Phase 1 metadata update.

    Ownership and authorization metadata remain server-managed: a client can
    only change the linked V1 projects, which causes the service to rebuild the
    persisted authorization project set exactly.
    """

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    name: ShortText | None = None
    question: ContentText | None = None
    type: ResearchProjectType | None = None
    related_project_ids: list[Identifier] | None = Field(default=None, max_length=100)
    status: ResearchProjectPhaseOneStatus | None = None

    @field_validator("related_project_ids")
    @classmethod
    def unique_related_projects(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("related_project_ids must be unique")
        return value

    @model_validator(mode="after")
    def require_change(self) -> "ResearchProjectUpdateRequest":
        if not self.model_fields_set - {"expected_revision"}:
            raise ValueError("at least one research project field must be updated")
        return self


class ResearchProjectAuthorizationResponse(BaseModel):
    """Authorization metadata visible only after every linked V1 project passes."""

    project_ids: tuple[str, ...]
    owner_key_id: str | None


class ResearchProjectResponse(BaseModel):
    """Public V3 Phase 1 metadata without MongoDB internals."""

    research_project_id: str
    name: str
    question: str
    type: ResearchProjectType
    related_project_ids: tuple[str, ...]
    status: ResearchProjectStatus
    authorization: ResearchProjectAuthorizationResponse
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: ResearchProjectDocument) -> "ResearchProjectResponse":
        return cls(
            research_project_id=document.research_project_id,
            name=document.name,
            question=document.question,
            type=document.type,
            related_project_ids=tuple(document.related_project_ids),
            status=document.status,
            authorization=ResearchProjectAuthorizationResponse(
                project_ids=tuple(document.authorization.project_ids),
                owner_key_id=document.authorization.owner_key_id,
            ),
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ResearchProjectListResponse(BaseModel):
    """Cursor-paginated research projects visible to the caller."""

    items: tuple[ResearchProjectResponse, ...]
    next_cursor: str | None


ResearchSourcePhaseTwoStatus = Literal["active", "archived"]


class SourceSnapshotCreateRequest(BaseModel):
    """An immutable, caller-supplied source observation; the API does not acquire it."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: Identifier
    retrieved_at: AwareDatetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    version_identifier: str | None = Field(default=None, min_length=1, max_length=2_048)
    metadata: Metadata = Field(default_factory=dict)


class ResearchSourceCreateRequest(BaseModel):
    """Register metadata only; URI parsing is local and never dereferences the URI."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    source_id: Identifier
    source_type: ResearchSourceType
    uri: str = Field(min_length=1, max_length=2_048)
    title: ShortText
    publisher: str | None = Field(default=None, min_length=1, max_length=500)
    author: str | None = Field(default=None, min_length=1, max_length=500)
    published_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime | None = None
    source_class: SourceClass = SourceClass.UNKNOWN
    trust_metadata: Metadata = Field(default_factory=dict)
    status: ResearchSourcePhaseTwoStatus = "active"
    snapshot: SourceSnapshotCreateRequest | None = None

    @model_validator(mode="after")
    def validate_uri_locally(self) -> "ResearchSourceCreateRequest":
        # Import here to keep transport models free of any acquisition dependency.
        from app.research.uri import canonicalize_source_uri

        canonicalize_source_uri(self.source_type, self.uri)
        return self


class SourceSnapshotResponse(BaseModel):
    """Immutable snapshot metadata, without retained source content."""

    source_id: str
    snapshot_id: str
    retrieved_at: datetime
    content_hash: str
    version_identifier: str | None
    metadata: Metadata
    status: ResearchRecordStatus
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: SourceSnapshotDocument) -> "SourceSnapshotResponse":
        return cls(
            source_id=document.source_id,
            snapshot_id=document.snapshot_id,
            retrieved_at=document.retrieved_at,
            content_hash=document.content_hash,
            version_identifier=document.version_identifier,
            metadata=document.metadata,
            status=document.status,
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ResearchSourceResponse(BaseModel):
    """Registered source provenance, with no retrieval score or truth judgement."""

    research_project_id: str
    source_id: str
    source_type: ResearchSourceType
    uri: str
    canonical_uri: str | None
    title: str
    publisher: str | None
    author: str | None
    published_at: datetime | None
    retrieved_at: datetime | None
    source_class: SourceClass
    trust_metadata: Metadata
    status: ResearchRecordStatus
    authorization: ResearchProjectAuthorizationResponse
    revision: int
    created_at: datetime
    updated_at: datetime
    registered_snapshot: SourceSnapshotResponse | None = None

    @classmethod
    def from_document(
        cls,
        document: ResearchSourceDocument,
        *,
        registered_snapshot: SourceSnapshotDocument | None = None,
    ) -> "ResearchSourceResponse":
        return cls(
            research_project_id=document.research_project_id,
            source_id=document.source_id,
            source_type=document.source_type,
            uri=document.uri,
            canonical_uri=document.canonical_uri,
            title=document.title,
            publisher=document.publisher,
            author=document.author,
            published_at=document.published_at,
            retrieved_at=document.retrieved_at,
            source_class=document.source_class,
            trust_metadata=document.trust_metadata,
            status=document.status,
            authorization=ResearchProjectAuthorizationResponse(
                project_ids=tuple(document.authorization.project_ids),
                owner_key_id=document.authorization.owner_key_id,
            ),
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
            registered_snapshot=(
                SourceSnapshotResponse.from_document(registered_snapshot)
                if registered_snapshot is not None
                else None
            ),
        )


class ResearchSourceListResponse(BaseModel):
    """Cursor-paginated source metadata visible within one authorized research project."""

    items: tuple[ResearchSourceResponse, ...]
    next_cursor: str | None


class ResearchIngestionRequest(BaseModel):
    """Submit bytes or acquire only the source already registered for this ID.

    Neither acquisition field carries a URL, repository, local path, ref, or
    command. The route selects the already-authorized source record; providers
    then enforce their own URL or repository boundary.
    """

    model_config = ConfigDict(extra="forbid")

    fetch_url: bool = False
    fetch_repository: bool = False
    content: ContentText | None = None
    content_base64: str | None = Field(default=None, min_length=1, max_length=940_000)
    content_type: (
        Literal[
            "text/plain",
            "text/markdown",
            "text/x-markdown",
            "application/pdf",
            "application/x-pdf",
        ]
        | None
    ) = None
    title: ShortText | None = None
    language: str = Field(default="und", min_length=1, max_length=64)
    filename: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_submission(self) -> "ResearchIngestionRequest":
        has_uploaded_content = self.content is not None or self.content_base64 is not None
        if self.fetch_url or self.fetch_repository:
            if self.fetch_url and self.fetch_repository:
                raise ValueError("select only one registered source acquisition mode")
            if has_uploaded_content or self.content_type is not None or self.filename is not None:
                raise ValueError(
                    "registered acquisition cannot include submitted content or file metadata"
                )
            return self
        if (self.content is None) == (self.content_base64 is None):
            raise ValueError("provide exactly one of content or content_base64")
        if self.content_type is None:
            raise ValueError("content_type is required for submitted content")
        if self.filename is not None and (
            "/" in self.filename or "\\" in self.filename or "\x00" in self.filename
        ):
            raise ValueError("filename must be a basename, not a path")
        return self

    @property
    def manually_provided(self) -> bool:
        """Whether the request is explicit text rather than a file or registered URL."""
        return self.content is not None

    def submitted_bytes(self) -> bytes:
        """Decode only bounded standard base64; URL acquisition has no request bytes."""
        if self.fetch_url or self.fetch_repository:
            return b""
        if self.content is not None:
            return self.content.encode("utf-8")
        if self.content_base64 is None:
            raise ValueError("content_base64 is required when content is absent")
        try:
            return b64decode(self.content_base64, validate=True)
        except BinasciiError as error:
            raise ValueError("content_base64 must be valid standard base64") from error


class IngestionJobResponse(BaseModel):
    """Persisted lifecycle status without copying retained source content into responses."""

    research_project_id: str
    job_id: str
    source_id: str
    snapshot_id: str | None
    status: IngestionJobState
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    documents_created: int
    chunks_created: int
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: IngestionJobDocument) -> "IngestionJobResponse":
        return cls(
            research_project_id=document.research_project_id,
            job_id=document.job_id,
            source_id=document.source_id,
            snapshot_id=document.snapshot_id,
            status=document.status,
            started_at=document.started_at,
            completed_at=document.completed_at,
            error=document.error,
            documents_created=document.documents_created,
            chunks_created=document.chunks_created,
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ResearchIngestionResponse(BaseModel):
    """One constrained Phase 3 ingestion result, including immutable snapshot provenance."""

    source_id: str
    snapshot: SourceSnapshotResponse
    job: IngestionJobResponse
    idempotent: bool


ResearchRetrievalProfileName = Literal[
    "research_general",
    "research_primary_sources",
    "research_recent",
    "research_content",
    "research_architecture",
    "research_code",
]
ResearchSearchMethod = Literal["vector", "lexical", "hybrid"]
ResearchSearchKind = Literal["chunk", "note", "claim", "skill", "project_context", "memory"]


class ResearchSearchRequest(BaseModel):
    """A bounded Phase 5 retrieval request; returned text remains untrusted data."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    query: str = Field(min_length=1, max_length=500)
    retrieval_profile: ResearchRetrievalProfileName = "research_general"
    method: ResearchSearchMethod = "hybrid"
    phrase: bool = False
    source_classes: list[SourceClass] = Field(default_factory=list, max_length=10)
    source_types: list[ResearchSourceType] = Field(default_factory=list, max_length=9)
    publisher: str | None = Field(default=None, min_length=1, max_length=500)
    domain: str | None = Field(default=None, min_length=1, max_length=253)
    published_after: AwareDatetime | None = None
    published_before: AwareDatetime | None = None
    retrieved_after: AwareDatetime | None = None
    as_of: AwareDatetime | None = None
    related_project_id: Identifier | None = None
    token_budget: int | None = Field(default=None, ge=100, le=16_000)
    limit: int = Field(default=10, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=1_024)

    @field_validator("source_classes", "source_types")
    @classmethod
    def unique_source_filters(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("research search filters must be unique")
        return value

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold()
        if not normalized or "://" in normalized or "/" in normalized or "@" in normalized:
            raise ValueError("domain must be a hostname, not a URL")
        return normalized

    @model_validator(mode="after")
    def validate_time_range(self) -> "ResearchSearchRequest":
        if (
            self.published_after is not None
            and self.published_before is not None
            and self.published_after > self.published_before
        ):
            raise ValueError("published_after must not be after published_before")
        return self


class ResearchSearchItemResponse(BaseModel):
    """One relevance-ranked item; ``retrieval_score`` is explicitly not confidence."""

    kind: ResearchSearchKind
    identifier: str
    source: str
    title: str
    content: str
    status: str | None
    source_id: str | None
    snapshot_id: str | None
    document_id: str | None
    source_type: ResearchSourceType | None
    source_class: SourceClass | None
    publisher: str | None
    domain: str | None
    published_at: datetime | None
    retrieved_at: datetime | None
    locator: dict[str, object] | None
    related_project_ids: tuple[str, ...]
    retrieval_score: float
    methods: tuple[ResearchSearchMethod, ...]
    tokens: int
    updated_at: datetime

    @classmethod
    def from_item(cls, item: ResearchSearchItem) -> "ResearchSearchItemResponse":
        """Serialize the service result without turning a retrieval score into confidence."""
        return cls.model_validate(
            {
                "kind": item.kind,
                "identifier": item.identifier,
                "source": item.source,
                "title": item.title,
                "content": item.content,
                "status": item.status,
                "source_id": item.source_id,
                "snapshot_id": item.snapshot_id,
                "document_id": item.document_id,
                "source_type": item.source_type,
                "source_class": item.source_class,
                "publisher": item.publisher,
                "domain": item.domain,
                "published_at": item.published_at,
                "retrieved_at": item.retrieved_at,
                "locator": None if item.locator is None else dict(item.locator),
                "related_project_ids": item.related_project_ids,
                "retrieval_score": item.retrieval_score,
                "methods": item.methods,
                "tokens": item.tokens,
                "updated_at": item.updated_at,
            }
        )


class ResearchSearchResponse(BaseModel):
    """A token-budgeted, authorized research packet; no factual assertion is implied."""

    research_project_id: str
    retrieval_profile: ResearchRetrievalProfileName
    method: ResearchSearchMethod
    items: tuple[ResearchSearchItemResponse, ...]
    token_budget: int
    tokens_used: int
    next_cursor: str | None

    @classmethod
    def from_result(cls, result: ResearchSearchResult) -> "ResearchSearchResponse":
        return cls(
            research_project_id=result.research_project_id,
            retrieval_profile=result.retrieval_profile,
            method=result.method,
            items=tuple(ResearchSearchItemResponse.from_item(item) for item in result.items),
            token_budget=result.token_budget,
            tokens_used=result.tokens_used,
            next_cursor=result.next_cursor,
        )


class ResearchNoteCreateRequest(BaseModel):
    """Store an authored working note; it is never a durable-memory write."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    note_id: Identifier
    title: ShortText
    body: ContentText
    source_refs: list[ResearchSourceReference] = Field(default_factory=list, max_length=100)
    status: ResearchNoteStatus = ResearchNoteStatus.WORKING

    @field_validator("source_refs")
    @classmethod
    def unique_source_refs(
        cls, value: list[ResearchSourceReference]
    ) -> list[ResearchSourceReference]:
        keys = [item.model_dump_json() for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("source_refs must be unique")
        return value


class ResearchNoteUpdateRequest(BaseModel):
    """Revision-guard an existing note without moving it between research projects."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    expected_revision: int = Field(ge=1)
    title: ShortText | None = None
    body: ContentText | None = None
    source_refs: list[ResearchSourceReference] | None = Field(default=None, max_length=100)
    status: ResearchNoteStatus | None = None

    @field_validator("source_refs")
    @classmethod
    def unique_source_refs(
        cls, value: list[ResearchSourceReference] | None
    ) -> list[ResearchSourceReference] | None:
        if value is None:
            return value
        keys = [item.model_dump_json() for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("source_refs must be unique")
        return value

    @model_validator(mode="after")
    def require_change(self) -> "ResearchNoteUpdateRequest":
        if not self.model_fields_set - {"research_project_id", "expected_revision"}:
            raise ValueError("at least one research note field must be updated")
        return self


class ResearchNoteResponse(BaseModel):
    """One project-scoped working artifact, kept separate from V1 memory."""

    research_project_id: str
    note_id: str
    title: str
    body: str
    source_refs: tuple[ResearchSourceReference, ...]
    created_by: str | None
    status: ResearchNoteStatus
    actor: ActorProvenance
    authorization: ResearchProjectAuthorizationResponse
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: ResearchNoteDocument) -> "ResearchNoteResponse":
        return cls(
            research_project_id=document.research_project_id,
            note_id=document.note_id,
            title=document.title,
            body=document.body,
            source_refs=tuple(document.source_refs),
            created_by=document.created_by,
            status=document.status,
            actor=document.actor,
            authorization=ResearchProjectAuthorizationResponse(
                project_ids=tuple(document.authorization.project_ids),
                owner_key_id=document.authorization.owner_key_id,
            ),
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ResearchNoteSearchRequest(BaseModel):
    """Bounded lexical search of notes for the Phase 6 MCP surface."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    query: str = Field(min_length=1, max_length=500)
    phrase: bool = False
    limit: int = Field(default=10, ge=1, le=50)


class ResearchNoteSearchItemResponse(BaseModel):
    """A note result whose relevance score is deliberately not a claim confidence."""

    note: ResearchNoteResponse
    retrieval_score: float


class ResearchNoteSearchResponse(BaseModel):
    """Bounded authorized note-search output."""

    research_project_id: str
    items: tuple[ResearchNoteSearchItemResponse, ...]


class ResearchClaimCreateRequest(BaseModel):
    """Create an explicit evaluation state without deriving it from retrieval."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    claim_id: Identifier
    statement: ContentText
    status: ClaimState = ClaimState.UNREVIEWED
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_rationale: str | None = Field(default=None, min_length=1, max_length=4_000)
    supporting_citations: list[Identifier] = Field(default_factory=list, max_length=100)
    contradicting_citations: list[Identifier] = Field(default_factory=list, max_length=100)

    @field_validator("supporting_citations", "contradicting_citations")
    @classmethod
    def unique_citations(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("citation identifiers must be unique")
        return value

    @model_validator(mode="after")
    def no_citation_can_oppose_itself(self) -> "ResearchClaimCreateRequest":
        if set(self.supporting_citations) & set(self.contradicting_citations):
            raise ValueError("a citation cannot support and contradict the same claim")
        return self


class ResearchClaimUpdateRequest(BaseModel):
    """Revision-guard a human-supplied claim assessment and its evidence links."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    expected_revision: int = Field(ge=1)
    statement: ContentText | None = None
    status: ClaimState | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_rationale: str | None = Field(default=None, min_length=1, max_length=4_000)
    supporting_citations: list[Identifier] | None = Field(default=None, max_length=100)
    contradicting_citations: list[Identifier] | None = Field(default=None, max_length=100)

    @field_validator("supporting_citations", "contradicting_citations")
    @classmethod
    def unique_citations(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("citation identifiers must be unique")
        return value

    @model_validator(mode="after")
    def require_change(self) -> "ResearchClaimUpdateRequest":
        changed = self.model_fields_set - {"research_project_id", "expected_revision"}
        if not changed:
            raise ValueError("at least one research claim field must be updated")
        if (
            self.supporting_citations is not None
            and self.contradicting_citations is not None
            and set(self.supporting_citations) & set(self.contradicting_citations)
        ):
            raise ValueError("a citation cannot support and contradict the same claim")
        return self


class ResearchClaimResponse(BaseModel):
    """Explicit claim state and citations, with confidence kept distinct from relevance."""

    research_project_id: str
    claim_id: str
    statement: str
    status: ClaimState
    actor: ActorProvenance
    confidence: float | None
    confidence_rationale: str | None
    supporting_citations: tuple[str, ...]
    contradicting_citations: tuple[str, ...]
    authorization: ResearchProjectAuthorizationResponse
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: ResearchClaimDocument) -> "ResearchClaimResponse":
        return cls(
            research_project_id=document.research_project_id,
            claim_id=document.claim_id,
            statement=document.statement,
            status=document.status,
            actor=document.actor,
            confidence=document.confidence,
            confidence_rationale=document.confidence_rationale,
            supporting_citations=tuple(document.supporting_citation_ids),
            contradicting_citations=tuple(document.contradicting_citation_ids),
            authorization=ResearchProjectAuthorizationResponse(
                project_ids=tuple(document.authorization.project_ids),
                owner_key_id=document.authorization.owner_key_id,
            ),
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ResearchClaimListResponse(BaseModel):
    """Cursor-paginated claims visible under the caller's complete V1 grant."""

    items: tuple[ResearchClaimResponse, ...]
    next_cursor: str | None


class ResearchBriefBuildRequest(BaseModel):
    """Build a durable structured brief; prose synthesis remains outside the server."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    brief_id: Identifier
    scope: ContentText
    additional_unknowns: list[ContentText] = Field(default_factory=list, max_length=100)
    next_questions: list[ContentText] = Field(default_factory=list, max_length=100)
    status: ResearchBriefStatus = ResearchBriefStatus.DRAFT

    @field_validator("additional_unknowns", "next_questions")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("brief values must be unique")
        return value


class ResearchBriefResponse(BaseModel):
    """Structured brief evidence; it intentionally contains no generated prose conclusion."""

    research_project_id: str
    brief_id: str
    question: str
    scope: str
    key_findings: tuple[str, ...]
    claim_refs: tuple[ResearchBriefClaimReference, ...]
    citation_refs: tuple[ResearchBriefCitationReference, ...]
    contradictions: tuple[ResearchBriefContradiction, ...]
    unknowns: tuple[str, ...]
    sources: tuple[ResearchSourceReference, ...]
    next_questions: tuple[str, ...]
    status: ResearchBriefStatus
    actor: ActorProvenance
    authorization: ResearchProjectAuthorizationResponse
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: ResearchBriefDocument) -> "ResearchBriefResponse":
        return cls(
            research_project_id=document.research_project_id,
            brief_id=document.brief_id,
            question=document.question,
            scope=document.scope,
            key_findings=tuple(document.key_findings),
            claim_refs=tuple(document.claim_refs),
            citation_refs=tuple(document.citation_refs),
            contradictions=tuple(document.contradictions),
            unknowns=tuple(document.unknowns),
            sources=tuple(document.source_refs),
            next_questions=tuple(document.next_questions),
            status=document.status,
            actor=document.actor,
            authorization=ResearchProjectAuthorizationResponse(
                project_ids=tuple(document.authorization.project_ids),
                owner_key_id=document.authorization.owner_key_id,
            ),
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class KnowledgeCandidateCreateRequest(BaseModel):
    """Propose research-derived knowledge for review; it cannot choose approval."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
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

    @field_validator("source_claim_ids", "source_citation_ids")
    @classmethod
    def unique_sources(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("candidate source identifiers must be unique")
        return value

    @model_validator(mode="after")
    def validate_target(self) -> "KnowledgeCandidateCreateRequest":
        if not self.source_claim_ids:
            raise ValueError("knowledge candidates require at least one source claim")
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


class KnowledgeCandidateReviewRequest(BaseModel):
    """Revision-guard one explicit decision; neither request can promote a candidate."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    expected_revision: int = Field(ge=1)
    reason: str | None = Field(default=None, min_length=1, max_length=4_000)


class KnowledgeCandidateRejectRequest(KnowledgeCandidateReviewRequest):
    """A reviewer may reject outright or return a candidate for changes."""

    decision: CandidateState = CandidateState.REJECTED

    @model_validator(mode="after")
    def reject_decision_only(self) -> "KnowledgeCandidateRejectRequest":
        if self.decision not in {CandidateState.REJECTED, CandidateState.NEEDS_CHANGES}:
            raise ValueError("reject may decide only rejected or needs_changes")
        return self


class KnowledgeCandidatePromotionRequest(BaseModel):
    """Revision-guard an explicit V1 promotion of one already approved candidate."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    expected_revision: int = Field(ge=1)
    supersedes_context_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{24}$",
        description=(
            "An explicit active V1 context record to supersede after creating the replacement."
        ),
    )


class PromotionTargetResponse(BaseModel):
    """The exact V1 target created through controlled promotion."""

    target_type: Literal["memory", "project_context"]
    target_id: str
    provenance: PromotionProvenance


class KnowledgeCandidateResponse(BaseModel):
    """A review-gated proposal and retained research provenance, never a V1 write result."""

    research_project_id: str
    candidate_id: str
    candidate_type: CandidateType
    target_project_id: str | None
    target_skill_id: str | None
    current_skill_version: str | None
    title: str
    content: str
    source_claim_ids: tuple[str, ...]
    source_citation_ids: tuple[str, ...]
    confidence: float | None
    confidence_rationale: str | None
    freshness_class: FreshnessClass
    review_after: datetime | None
    revalidation_of_candidate_id: str | None
    revalidation_reasons: tuple[RevalidationReason, ...]
    status: CandidateState
    actor: ActorProvenance
    reviewer: str | None
    reviewed_at: datetime | None
    review_notes: str | None
    authorization: ResearchProjectAuthorizationResponse
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: KnowledgeCandidateDocument) -> "KnowledgeCandidateResponse":
        return cls(
            research_project_id=document.research_project_id,
            candidate_id=document.candidate_id,
            candidate_type=document.candidate_type,
            target_project_id=document.target_project_id,
            target_skill_id=document.target_skill_id,
            current_skill_version=document.current_skill_version,
            title=document.title,
            content=document.content,
            source_claim_ids=tuple(document.source_claim_ids),
            source_citation_ids=tuple(document.source_citation_ids),
            confidence=document.confidence,
            confidence_rationale=document.confidence_rationale,
            freshness_class=document.freshness_class,
            review_after=document.review_after,
            revalidation_of_candidate_id=document.revalidation_of_candidate_id,
            revalidation_reasons=tuple(document.revalidation_reasons),
            status=document.status,
            actor=document.actor,
            reviewer=document.reviewer,
            reviewed_at=document.reviewed_at,
            review_notes=document.review_notes,
            authorization=ResearchProjectAuthorizationResponse(
                project_ids=tuple(document.authorization.project_ids),
                owner_key_id=document.authorization.owner_key_id,
            ),
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class KnowledgeCandidateListResponse(BaseModel):
    """Cursor-paginated candidate records visible under complete V1 project grants."""

    items: tuple[KnowledgeCandidateResponse, ...]
    next_cursor: str | None


class RevalidationProposalRequest(BaseModel):
    """Request an explicit pending revalidation candidate for promoted knowledge."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    as_of: AwareDatetime | None = None


class SourceChangeResponse(BaseModel):
    """Fixed-field source version comparison; values stay in immutable snapshots."""

    source_id: str
    baseline_snapshot_id: str | None
    observed_snapshot_id: str | None
    reasons: tuple[RevalidationReason, ...]

    @classmethod
    def from_value(cls, value: SourceChange) -> "SourceChangeResponse":
        return cls(
            source_id=value.source_id,
            baseline_snapshot_id=value.baseline_snapshot_id,
            observed_snapshot_id=value.observed_snapshot_id,
            reasons=value.reasons,
        )


class StaleKnowledgeResponse(BaseModel):
    """Potential staleness metadata, not an automatic invalidation verdict."""

    research_project_id: str
    candidate_id: str
    target_type: Literal["memory", "project_context"]
    target_id: str
    title: str
    verified_at: datetime
    source_snapshot_ids: tuple[str, ...]
    review_after: datetime | None
    freshness_class: FreshnessClass
    reasons: tuple[RevalidationReason, ...]
    source_changes: tuple[SourceChangeResponse, ...]

    @classmethod
    def from_value(cls, value: StaleKnowledge) -> "StaleKnowledgeResponse":
        return cls(
            research_project_id=value.research_project_id,
            candidate_id=value.candidate_id,
            target_type=value.target_type,
            target_id=value.target_id,
            title=value.title,
            verified_at=value.verified_at,
            source_snapshot_ids=value.source_snapshot_ids,
            review_after=value.review_after,
            freshness_class=value.freshness_class,
            reasons=value.reasons,
            source_changes=tuple(
                SourceChangeResponse.from_value(item) for item in value.source_changes
            ),
        )


class StaleKnowledgeListResponse(BaseModel):
    """Cursor-paged potentially stale promoted knowledge."""

    items: tuple[StaleKnowledgeResponse, ...]
    next_cursor: str | None


class RevalidationProposalResponse(BaseModel):
    """The new pending review candidate and its non-mutating trigger evidence."""

    candidate: KnowledgeCandidateResponse
    stale_knowledge: StaleKnowledgeResponse

    @classmethod
    def from_value(cls, value: RevalidationProposal) -> "RevalidationProposalResponse":
        return cls(
            candidate=KnowledgeCandidateResponse.from_document(value.candidate),
            stale_knowledge=StaleKnowledgeResponse.from_value(value.stale_knowledge),
        )


class SkillUpdateProposalCreateRequest(BaseModel):
    """Store a generated, review-only Markdown patch proposal.

    This payload is data only. The API never applies the patch, reads a local
    skill file, runs Git, or calls the V1 skill importer.
    """

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    proposal_id: Identifier
    skill_id: Identifier
    current_version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$",
    )
    proposed_change: ContentText
    proposed_patch: ContentText
    source_claim_ids: list[Identifier] = Field(min_length=1, max_length=100)
    source_citation_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    reason: ContentText

    @field_validator("source_claim_ids", "source_citation_ids")
    @classmethod
    def unique_provenance(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("skill update proposal provenance identifiers must be unique")
        return value


class SkillUpdateProposalResponse(BaseModel):
    """A persisted inert patch proposal, always marked generated in Phase 11."""

    research_project_id: str
    proposal_id: str
    skill_id: str
    current_version: str
    proposed_change: str
    proposed_patch: str
    source_claim_ids: tuple[str, ...]
    source_citation_ids: tuple[str, ...]
    reason: str
    status: SkillUpdateProposalStatus
    actor: ActorProvenance
    trust_level: Literal["generated"]
    authorization: ResearchProjectAuthorizationResponse
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: SkillUpdateProposalDocument) -> "SkillUpdateProposalResponse":
        return cls(
            research_project_id=document.research_project_id,
            proposal_id=document.proposal_id,
            skill_id=document.skill_id,
            current_version=document.current_version,
            proposed_change=document.proposed_change,
            proposed_patch=document.proposed_patch,
            source_claim_ids=tuple(document.source_claim_ids),
            source_citation_ids=tuple(document.source_citation_ids),
            reason=document.reason,
            status=document.status,
            actor=document.actor,
            trust_level=document.trust_level,
            authorization=ResearchProjectAuthorizationResponse(
                project_ids=tuple(document.authorization.project_ids),
                owner_key_id=document.authorization.owner_key_id,
            ),
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class SkillUpdateProposalListResponse(BaseModel):
    """Cursor-paginated generated proposals visible under retained grants."""

    items: tuple[SkillUpdateProposalResponse, ...]
    next_cursor: str | None


class SkillUpdateProposalExportResponse(BaseModel):
    """Explicit export payload; returning it never writes a Markdown file."""

    proposal: SkillUpdateProposalResponse
    proposed_patch: str


class PromotionEventResponse(BaseModel):
    """Immutable record of a review decision; it does not indicate promotion."""

    research_project_id: str
    candidate_id: str
    event_id: str
    target_type: CandidateType
    target_id: str | None
    reviewer: str
    decision: CandidateState
    decided_at: datetime
    reason: str | None
    actor: ActorProvenance
    authorization: ResearchProjectAuthorizationResponse
    created_at: datetime

    @classmethod
    def from_document(cls, document: PromotionEventDocument) -> "PromotionEventResponse":
        return cls(
            research_project_id=document.research_project_id,
            candidate_id=document.candidate_id,
            event_id=document.event_id,
            target_type=document.target_type,
            target_id=document.target_id,
            reviewer=document.reviewer,
            decision=document.decision,
            decided_at=document.decided_at,
            reason=document.reason,
            actor=document.actor,
            authorization=ResearchProjectAuthorizationResponse(
                project_ids=tuple(document.authorization.project_ids),
                owner_key_id=document.authorization.owner_key_id,
            ),
            created_at=document.created_at,
        )


class KnowledgeCandidateReviewResponse(BaseModel):
    """One reviewed candidate paired with its immutable decision event."""

    candidate: KnowledgeCandidateResponse
    event: PromotionEventResponse


class KnowledgeCandidatePromotionResponse(BaseModel):
    """A promoted candidate, immutable event, and traceable V1 destination."""

    candidate: KnowledgeCandidateResponse
    event: PromotionEventResponse
    target: PromotionTargetResponse


class ResearchCitationCreateRequest(BaseModel):
    """Author one source-version citation; relation and claim state stay separate."""

    model_config = ConfigDict(extra="forbid")

    research_project_id: Identifier
    citation_id: Identifier
    claim_id: Identifier
    source_id: Identifier
    snapshot_id: Identifier
    chunk_id: Identifier | None = None
    locator: CitationLocator
    evidence_excerpt: ContentText
    relation: CitationRelation


class ResearchCitationResponse(BaseModel):
    """Persisted citation provenance, never a factual-confidence rating."""

    research_project_id: str
    citation_id: str
    claim_id: str
    source_id: str
    snapshot_id: str
    chunk_id: str | None
    source_class: SourceClass
    locator: CitationLocator
    evidence_excerpt: str
    relation: CitationRelation
    status: ResearchRecordStatus
    actor: ActorProvenance
    authorization: ResearchProjectAuthorizationResponse
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: ResearchCitationDocument) -> "ResearchCitationResponse":
        return cls(
            research_project_id=document.research_project_id,
            citation_id=document.citation_id,
            claim_id=document.claim_id,
            source_id=document.source_id,
            snapshot_id=document.snapshot_id,
            chunk_id=document.chunk_id,
            source_class=document.source_class,
            locator=document.locator,
            evidence_excerpt=document.evidence_excerpt,
            relation=document.relation,
            status=document.status,
            actor=document.actor,
            authorization=ResearchProjectAuthorizationResponse(
                project_ids=tuple(document.authorization.project_ids),
                owner_key_id=document.authorization.owner_key_id,
            ),
            revision=document.revision,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class ResearchEvidencePacketRequest(BaseModel):
    """One bounded, authorized request to present retrieved context and citations."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    research_project_id: Identifier
    project_id: Identifier | None = None
    token_budget: int | None = Field(default=None, ge=100, le=16_000)


class ResearchEvidenceSourceResponse(BaseModel):
    """Source provenance shown with evidence; class remains descriptive metadata."""

    source_id: str
    snapshot_id: str
    uri: str
    canonical_uri: str | None
    title: str
    publisher: str | None
    author: str | None
    published_at: datetime | None
    retrieved_at: datetime
    source_type: ResearchSourceType
    source_class: SourceClass

    @classmethod
    def from_evidence(cls, evidence: CitationEvidence) -> "ResearchEvidenceSourceResponse":
        source = evidence.source
        snapshot = evidence.snapshot
        citation = evidence.citation
        return cls(
            source_id=source.source_id,
            snapshot_id=citation.snapshot_id,
            uri=source.uri,
            canonical_uri=source.canonical_uri,
            title=source.title,
            publisher=source.publisher,
            author=source.author,
            published_at=source.published_at,
            retrieved_at=snapshot.retrieved_at,
            source_type=source.source_type,
            source_class=citation.source_class,
        )


class ResearchEvidenceItemResponse(BaseModel):
    """An excerpt plus its source, snapshot, claim statement, and exact locator."""

    citation: ResearchCitationResponse
    claim_id: str
    claim_statement: str
    claim_status: ClaimState
    source: ResearchEvidenceSourceResponse

    @classmethod
    def from_evidence(cls, evidence: CitationEvidence) -> "ResearchEvidenceItemResponse":
        claim = evidence.claim
        citation = evidence.citation
        return cls(
            citation=ResearchCitationResponse.from_document(citation),
            claim_id=claim.claim_id,
            claim_statement=claim.statement,
            claim_status=claim.status,
            source=ResearchEvidenceSourceResponse.from_evidence(evidence),
        )


class ResearchEvidenceConflictResponse(BaseModel):
    """A visible disagreement; neither side is selected or converted into a verdict."""

    claim_id: str
    statement: str
    supporting_evidence: tuple[ResearchEvidenceItemResponse, ...]
    contradicting_evidence: tuple[ResearchEvidenceItemResponse, ...]

    @classmethod
    def from_conflict(cls, conflict: EvidenceConflict) -> "ResearchEvidenceConflictResponse":
        return cls(
            claim_id=conflict.claim_id,
            statement=conflict.statement,
            supporting_evidence=tuple(
                ResearchEvidenceItemResponse.from_evidence(item) for item in conflict.supporting
            ),
            contradicting_evidence=tuple(
                ResearchEvidenceItemResponse.from_evidence(item) for item in conflict.contradicting
            ),
        )


class ResearchClaimEvidenceResponse(BaseModel):
    """All authorized persisted evidence for one explicit claim, grouped by relation."""

    research_project_id: str
    claim_id: str
    supporting_evidence: tuple[ResearchEvidenceItemResponse, ...]
    contradicting_evidence: tuple[ResearchEvidenceItemResponse, ...]
    qualifying_evidence: tuple[ResearchEvidenceItemResponse, ...]
    background_evidence: tuple[ResearchEvidenceItemResponse, ...]

    @classmethod
    def from_evidence(
        cls,
        *,
        research_project_id: str,
        claim_id: str,
        evidence: tuple[CitationEvidence, ...],
    ) -> "ResearchClaimEvidenceResponse":
        def grouped(relation: CitationRelation) -> tuple[ResearchEvidenceItemResponse, ...]:
            return tuple(
                ResearchEvidenceItemResponse.from_evidence(item)
                for item in evidence
                if item.citation.relation is relation
            )

        return cls(
            research_project_id=research_project_id,
            claim_id=claim_id,
            supporting_evidence=grouped(CitationRelation.SUPPORTS),
            contradicting_evidence=grouped(CitationRelation.CONTRADICTS),
            qualifying_evidence=grouped(CitationRelation.QUALIFIES),
            background_evidence=grouped(CitationRelation.BACKGROUND),
        )


class ResearchEvidencePacketResponse(BaseModel):
    """A bounded evidence packet that preserves uncertainty and source conflict."""

    question: str
    research_project_id: str
    relevant_context: tuple[ResearchSearchItemResponse, ...]
    supporting_evidence: tuple[ResearchEvidenceItemResponse, ...]
    contradicting_evidence: tuple[ResearchEvidenceItemResponse, ...]
    qualifying_evidence: tuple[ResearchEvidenceItemResponse, ...]
    background_evidence: tuple[ResearchEvidenceItemResponse, ...]
    source_metadata: tuple[ResearchEvidenceSourceResponse, ...]
    citation_locators: tuple[CitationLocator, ...]
    conflicts: tuple[ResearchEvidenceConflictResponse, ...]
    uncertainties: tuple[str, ...]
    token_budget: int
    tokens_used: int

    @classmethod
    def from_packet(cls, packet: EvidencePacket) -> "ResearchEvidencePacketResponse":
        return cls(
            question=packet.question,
            research_project_id=packet.research_project_id,
            relevant_context=tuple(
                ResearchSearchItemResponse.from_item(item) for item in packet.relevant_context
            ),
            supporting_evidence=tuple(
                ResearchEvidenceItemResponse.from_evidence(item)
                for item in packet.supporting_evidence
            ),
            contradicting_evidence=tuple(
                ResearchEvidenceItemResponse.from_evidence(item)
                for item in packet.contradicting_evidence
            ),
            qualifying_evidence=tuple(
                ResearchEvidenceItemResponse.from_evidence(item)
                for item in packet.qualifying_evidence
            ),
            background_evidence=tuple(
                ResearchEvidenceItemResponse.from_evidence(item)
                for item in packet.background_evidence
            ),
            source_metadata=tuple(
                ResearchEvidenceSourceResponse.from_evidence(item)
                for item in packet.source_metadata
            ),
            citation_locators=tuple(packet.citation_locators),
            conflicts=tuple(
                ResearchEvidenceConflictResponse.from_conflict(item) for item in packet.conflicts
            ),
            uncertainties=tuple(packet.uncertainties),
            token_budget=packet.token_budget,
            tokens_used=packet.tokens_used,
        )


__all__ = [
    "IngestionJobResponse",
    "IngestionJobSchema",
    "KnowledgeCandidateCreateRequest",
    "KnowledgeCandidateListResponse",
    "KnowledgeCandidatePromotionRequest",
    "KnowledgeCandidatePromotionResponse",
    "KnowledgeCandidateRejectRequest",
    "KnowledgeCandidateResponse",
    "KnowledgeCandidateReviewRequest",
    "KnowledgeCandidateReviewResponse",
    "KnowledgeCandidateSchema",
    "PromotionEventResponse",
    "PromotionEventSchema",
    "PromotionTargetResponse",
    "ResearchBriefBuildRequest",
    "ResearchBriefResponse",
    "ResearchBriefSchema",
    "ResearchChunkSchema",
    "ResearchCitationCreateRequest",
    "ResearchCitationResponse",
    "ResearchCitationSchema",
    "ResearchClaimCreateRequest",
    "ResearchClaimEvidenceResponse",
    "ResearchClaimListResponse",
    "ResearchClaimResponse",
    "ResearchClaimSchema",
    "ResearchClaimUpdateRequest",
    "ResearchDocumentSchema",
    "ResearchEvidenceConflictResponse",
    "ResearchEvidenceItemResponse",
    "ResearchEvidencePacketRequest",
    "ResearchEvidencePacketResponse",
    "ResearchEvidenceSourceResponse",
    "ResearchIngestionRequest",
    "ResearchIngestionResponse",
    "ResearchNoteCreateRequest",
    "ResearchNoteResponse",
    "ResearchNoteSchema",
    "ResearchNoteSearchItemResponse",
    "ResearchNoteSearchRequest",
    "ResearchNoteSearchResponse",
    "ResearchNoteUpdateRequest",
    "ResearchPageSchema",
    "ResearchPagination",
    "ResearchProjectAuthorizationResponse",
    "ResearchProjectCreateRequest",
    "ResearchProjectListResponse",
    "ResearchProjectPhaseOneStatus",
    "ResearchProjectResponse",
    "ResearchProjectSchema",
    "ResearchProjectUpdateRequest",
    "ResearchRetrievalProfileName",
    "ResearchSearchItemResponse",
    "ResearchSearchKind",
    "ResearchSearchMethod",
    "ResearchSearchRequest",
    "ResearchSearchResponse",
    "ResearchSourceCreateRequest",
    "ResearchSourceListResponse",
    "ResearchSourcePhaseTwoStatus",
    "ResearchSourceResponse",
    "ResearchSourceSchema",
    "RevalidationProposalRequest",
    "RevalidationProposalResponse",
    "SkillUpdateProposalCreateRequest",
    "SkillUpdateProposalExportResponse",
    "SkillUpdateProposalListResponse",
    "SkillUpdateProposalResponse",
    "SkillUpdateProposalSchema",
    "SourceChangeResponse",
    "SourceSnapshotCreateRequest",
    "SourceSnapshotResponse",
    "SourceSnapshotSchema",
    "StaleKnowledgeListResponse",
    "StaleKnowledgeResponse",
]
