"""Public schemas for skill-chunk retrieval and for unified knowledge search."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.common import Identifier, Metadata, Tags
from app.models.memory import MemoryScope
from app.models.project_context import ContextType
from app.models.retrieval_log import RetrievalMethod as LoggedMethod
from app.models.skill import SemanticVersion, SkillTrustLevel
from app.retrieval.context_builder import ContextItem, RetrievalContext
from app.retrieval.types import ALL_SOURCES, MIN_TOKEN_BUDGET, SourceType
from app.schemas.skill import SearchQuery, SkillChunkResponse, SkillSummary
from app.search.types import KnowledgeSearchResult, RetrievalTrace
from app.skills.types import RetrievalMethod, SkillChunkSearchResult

MAX_CONTEXT_TYPES = 14
MAX_MEMORY_TYPES = 20


def _unique(value: list[str]) -> list[str]:
    """Reject repeated values in a filter list."""
    if len(value) != len(set(value)):
        raise ValueError("values must be unique")
    return value


class SkillSearchFilters(BaseModel):
    """Skill metadata filters shared by every retrieval mode."""

    model_config = ConfigDict(extra="forbid")

    query: SearchQuery
    skill_id: Identifier | None = None
    skill_version: SemanticVersion | None = Field(
        default=None,
        description="Target one exact version, including a superseded one.",
    )
    category: Identifier | None = None
    tags: Tags = Field(default_factory=list)
    trust_level: SkillTrustLevel | None = None
    active_only: bool = Field(
        default=True,
        description="Restrict to active versions. Ignored when skill_version is given.",
    )
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        """Reject repeated search filters."""
        if len(value) != len(set(value)):
            raise ValueError("tags must be unique")
        return value

    @model_validator(mode="after")
    def validate_version_scope(self) -> "SkillSearchFilters":
        """Require the skill a requested version belongs to."""
        if self.skill_version is not None and self.skill_id is None:
            raise ValueError("skill_version requires skill_id")
        return self


class SkillVectorSearchRequest(SkillSearchFilters):
    """Bounded semantic query with skill metadata filters."""


class SkillLexicalSearchRequest(SkillSearchFilters):
    """Bounded lexical query with skill metadata filters."""

    phrase: bool = Field(
        default=False,
        description="Require the query words to appear together, in order.",
    )


class SkillChunkSearchResultResponse(BaseModel):
    """One scored chunk with the metadata of the skill that produced it."""

    source: Literal["skill_chunk"] = "skill_chunk"
    method: RetrievalMethod
    score: float
    skill: SkillSummary
    chunk: SkillChunkResponse

    @classmethod
    def from_result(cls, result: SkillChunkSearchResult) -> "SkillChunkSearchResultResponse":
        """Build a result without exposing vectors or persistence internals."""
        return cls(
            method=result.method,
            score=result.score,
            skill=SkillSummary.from_document(result.skill),
            chunk=SkillChunkResponse.from_document(result.chunk),
        )


class SkillVectorSearchResponse(BaseModel):
    """Bounded semantic results in descending similarity order."""

    query: str
    embedding_model: str
    embedding_dimensions: int
    results: tuple[SkillChunkSearchResultResponse, ...]


class SkillLexicalSearchResponse(BaseModel):
    """Bounded lexical results in descending relevance order."""

    query: str
    phrase: bool
    results: tuple[SkillChunkSearchResultResponse, ...]


class SkillHybridSearchRequest(SkillSearchFilters):
    """Bounded hybrid query with metadata filters and a token budget."""

    project_id: Identifier | None = Field(
        default=None,
        description="Scope the request to one project. Global knowledge is always visible.",
    )
    minimum_importance: float = Field(default=0.0, ge=0.0, le=1.0)
    token_budget: int | None = Field(
        default=None,
        ge=MIN_TOKEN_BUDGET,
        description="Context tokens to return at most. Clamped to the configured maximum.",
    )
    explain: bool = Field(
        default=False,
        description="Include the ranking breakdown behind each result.",
    )


class ScoreBreakdownResponse(BaseModel):
    """The normalized signals behind one result's score."""

    vector: float
    lexical: float
    importance: float
    recency: float
    source_priority: float
    final: float
    methods: tuple[RetrievalMethod, ...]


class RetrievalDiagnosticsResponse(BaseModel):
    """What the retrieval pipeline did with the candidates it found."""

    methods: tuple[RetrievalMethod, ...]
    weights: dict[str, float]
    vector_matches: int
    lexical_matches: int
    merged: int
    filtered_out: int
    duplicates_removed: int
    ranked: int
    dropped_for_budget: int


class ContextItemResponse(BaseModel):
    """One piece of retrieved context with the source it came from.

    `key` identifies the result stably across queries, so a caller can say
    which result it used and a retrieval log can record the same thing.
    """

    key: str
    source: SourceType
    title: str
    content: str
    score: float
    tokens: int
    metadata: Metadata
    explanation: ScoreBreakdownResponse | None = None

    @classmethod
    def from_item(cls, item: ContextItem, *, explain: bool) -> "ContextItemResponse":
        """Build a compact item, explaining its score only when asked."""
        return cls(
            key=item.key,
            source=item.source,
            title=item.title,
            content=item.content,
            score=item.score,
            tokens=item.tokens,
            metadata=dict(item.metadata),
            explanation=ScoreBreakdownResponse(
                vector=item.explanation.vector,
                lexical=item.explanation.lexical,
                importance=item.explanation.importance,
                recency=item.explanation.recency,
                source_priority=item.explanation.source_priority,
                final=item.explanation.final,
                methods=item.explanation.methods,
            )
            if explain
            else None,
        )


class SkillHybridSearchResponse(BaseModel):
    """Budgeted context in descending combined-score order."""

    query: str
    project_id: str | None
    token_count: int
    token_budget: int
    results: tuple[ContextItemResponse, ...]
    diagnostics: RetrievalDiagnosticsResponse | None = None

    @classmethod
    def from_context(
        cls,
        context: RetrievalContext,
        *,
        explain: bool,
    ) -> "SkillHybridSearchResponse":
        """Build a response whose retrieval metadata stays internal by default."""
        return cls(
            query=context.query,
            project_id=context.project_id,
            token_count=context.token_count,
            token_budget=context.token_budget,
            results=tuple(
                ContextItemResponse.from_item(item, explain=explain) for item in context.items
            ),
            diagnostics=_diagnostics(context) if explain else None,
        )


class KnowledgeSearchRequest(BaseModel):
    """One question asked of every source a request is allowed to see.

    Naming no source searches everything retrievable, which is what makes this
    the one endpoint a caller needs. Every filter narrows the answer; none of
    them can widen it past the project the request named.
    """

    model_config = ConfigDict(extra="forbid")

    query: SearchQuery
    project_id: Identifier | None = Field(
        default=None,
        description="Scope the request to one project. Global knowledge stays visible.",
    )
    sources: list[SourceType] | None = Field(
        default=None,
        max_length=len(ALL_SOURCES),
        description="Restrict which sources answer. All retrievable ones by default.",
    )
    skill_id: Identifier | None = None
    skill_version: SemanticVersion | None = Field(
        default=None,
        description="Target one exact version, including a superseded one.",
    )
    category: Identifier | None = None
    context_types: list[ContextType] = Field(default_factory=list, max_length=MAX_CONTEXT_TYPES)
    memory_types: list[Identifier] = Field(default_factory=list, max_length=MAX_MEMORY_TYPES)
    memory_scopes: list[MemoryScope] | None = Field(
        default=None,
        max_length=4,
        description="Restrict memory to shared and authenticated-client scopes.",
    )
    tags: Tags = Field(default_factory=list)
    trust_level: SkillTrustLevel | None = None
    minimum_importance: float = Field(default=0.0, ge=0.0, le=1.0)
    active_only: bool = Field(
        default=True,
        description="Restrict to active content. Ignored when skill_version is given.",
    )
    phrase: bool = Field(
        default=False,
        description="Require the query words to appear together, in order.",
    )
    token_budget: int | None = Field(
        default=None,
        ge=MIN_TOKEN_BUDGET,
        description="Context tokens to return at most. Clamped to the configured maximum.",
    )
    limit: int = Field(default=10, ge=1, le=50)
    explain: bool = Field(
        default=False,
        description="Include the ranking breakdown behind each result.",
    )

    @field_validator("sources", "context_types", "memory_types", "memory_scopes", "tags")
    @classmethod
    def unique_values(cls, value: list[str] | None) -> list[str] | None:
        """Reject repeated search filters."""
        return None if value is None else _unique(value)

    @model_validator(mode="after")
    def validate_scope(self) -> "KnowledgeSearchRequest":
        """Reject a request whose filters could not be satisfied as written."""
        if self.skill_version is not None and self.skill_id is None:
            raise ValueError("skill_version requires skill_id")
        if self.sources is not None and not self.sources:
            raise ValueError("provide at least one source to search")
        if self.memory_scopes is not None:
            if not self.memory_scopes:
                raise ValueError("provide at least one memory scope to search")
            if (
                any(scope in {"project", "project_agent"} for scope in self.memory_scopes)
                and self.project_id is None
            ):
                raise ValueError("searching project-scoped memory requires project_id")
        return self


class RetrievalTraceResponse(BaseModel):
    """What one search did, for debugging the results it returned.

    A source that was asked for but not searched appears under
    `skipped_sources`, which turns "nothing came back" into an answer: either
    the source found nothing, or no query ran against it at all.
    """

    query_id: str
    retrieval_method: LoggedMethod
    requested_sources: tuple[SourceType, ...]
    searched_sources: tuple[SourceType, ...]
    skipped_sources: tuple[SourceType, ...]
    latency_ms: float
    diagnostics: RetrievalDiagnosticsResponse

    @classmethod
    def from_trace(
        cls,
        trace: RetrievalTrace,
        diagnostics: RetrievalDiagnosticsResponse,
    ) -> "RetrievalTraceResponse":
        """Build the trace a unified search always reports."""
        return cls(
            query_id=trace.query_id,
            retrieval_method=trace.retrieval_method,
            requested_sources=trace.requested_sources,
            searched_sources=trace.searched_sources,
            skipped_sources=trace.skipped_sources,
            latency_ms=trace.latency_ms,
            diagnostics=diagnostics,
        )


class KnowledgeSearchResponse(BaseModel):
    """Budgeted context from every source, in descending combined-score order.

    The retrieval metadata is always present, because a unified answer drawn
    from several sources is only debuggable if the caller can see which ones
    contributed. Per-result score breakdowns stay behind `explain`: they are
    the part that grows with the answer.
    """

    query: str
    project_id: str | None
    token_count: int
    token_budget: int
    results: tuple[ContextItemResponse, ...]
    retrieval: RetrievalTraceResponse

    @classmethod
    def from_result(
        cls,
        result: KnowledgeSearchResult,
        *,
        explain: bool,
    ) -> "KnowledgeSearchResponse":
        """Build a compact response from one finished search."""
        context = result.context
        return cls(
            query=context.query,
            project_id=context.project_id,
            token_count=context.token_count,
            token_budget=context.token_budget,
            results=tuple(
                ContextItemResponse.from_item(item, explain=explain) for item in context.items
            ),
            retrieval=RetrievalTraceResponse.from_trace(
                result.trace,
                _diagnostics(context),
            ),
        )


def _diagnostics(context: RetrievalContext) -> RetrievalDiagnosticsResponse:
    """Describe what the pipeline did with the candidates it found."""
    diagnostics = context.diagnostics
    return RetrievalDiagnosticsResponse(
        methods=diagnostics.methods,
        weights=dict(diagnostics.weights),
        vector_matches=diagnostics.vector_matches,
        lexical_matches=diagnostics.lexical_matches,
        merged=diagnostics.merged,
        filtered_out=diagnostics.filtered_out,
        duplicates_removed=diagnostics.duplicates_removed,
        ranked=diagnostics.ranked,
        dropped_for_budget=diagnostics.dropped_for_budget,
    )
