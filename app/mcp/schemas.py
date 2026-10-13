"""Tool arguments, and the JSON Schema every tool advertises.

An MCP tool must publish a schema for its arguments, so these models are not a
second copy of the API's validation — they are the protocol's own requirement,
built from the same shared constraints the API schemas use. What a field may
contain is stated once, in `app.models.common`; what a *record* may do stays in
the services, where the platform already enforces it.

Every model forbids unknown fields. A caller that misspells an argument is told
so, rather than having it silently ignored and getting an answer to a question
it did not ask.
"""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.content.discovery import DiscoveryTimeRange
from app.models.agent import validate_session_metadata
from app.models.common import ContentText, Identifier, Metadata, ShortText, Tag
from app.models.content import (
    ContentBriefStatus,
    ContentOrigin,
    ContentPlatform,
    ContentPublicationProvider,
    ContentStatus,
    CreatorProfileStatus,
    CreatorVoice,
)
from app.models.memory import MemoryScope
from app.models.project_context import ContextType
from app.models.research import (
    CandidateState,
    CandidateType,
    ClaimState,
    ResearchBriefStatus,
    ResearchNoteStatus,
    ResearchProjectType,
    ResearchSourceType,
    SourceClass,
)
from app.models.skill import LifecycleStatus, SemanticVersion, SkillTrustLevel
from app.retrieval.types import MIN_TOKEN_BUDGET, SourceType

MAX_TAGS = 50
MAX_TYPES = 20
# Retrieval answers are read by a model inside a prompt, so a tool asks for
# fewer results than the API's ceiling by default. The ceiling is still the
# API's; this is only a more useful starting point.
DEFAULT_RETRIEVAL_LIMIT = 5
MAX_RETRIEVAL_LIMIT = 25
DEFAULT_PAGE_LIMIT = 20

ObjectIdHex = Annotated[str, Field(pattern=r"^[0-9a-f]{24}$")]
Query = Annotated[str, Field(min_length=1, max_length=500)]
TagList = Annotated[list[Tag], Field(max_length=MAX_TAGS)]
Fraction = Annotated[float, Field(ge=0.0, le=1.0)]


def _unique(value: list[str] | None) -> list[str] | None:
    """Reject repeated values in a filter or tag list."""
    if value is not None and len(value) != len(set(value)):
        raise ValueError("values must be unique")
    return value


class ToolArguments(BaseModel):
    """Base for every tool's arguments."""

    model_config = ConfigDict(extra="forbid")


class GetPlatformCapabilitiesArguments(ToolArguments):
    """No arguments: the gateway derives all caller facts from its credential."""


class BeginAgentSessionArguments(ToolArguments):
    """Small operational labels for a client-owned lifecycle session."""

    project_id: Identifier | None = None
    repository: str | None = Field(default=None, min_length=1, max_length=2_048)
    metadata: Metadata = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def small_operational_metadata(cls, value: Metadata) -> Metadata:
        return validate_session_metadata(value)


class GetAgentSessionArguments(ToolArguments):
    """One previously created session belonging to the authenticated client."""

    session_id: Identifier


class UpdateAgentSessionArguments(ToolArguments):
    """Revision-guarded session association or scalar diagnostic update."""

    session_id: Identifier
    expected_revision: int = Field(ge=1)
    project_id: Identifier | None = None
    repository: str | None = Field(default=None, min_length=1, max_length=2_048)
    metadata: Metadata = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def small_operational_metadata(cls, value: Metadata) -> Metadata:
        return validate_session_metadata(value)

    @model_validator(mode="after")
    def require_change(self) -> "UpdateAgentSessionArguments":
        if not ({"project_id", "repository", "metadata"} & self.model_fields_set):
            raise ValueError("provide at least one session field to update")
        return self


class EndAgentSessionArguments(ToolArguments):
    """Close one active session at the revision the client reviewed."""

    session_id: Identifier
    expected_revision: int = Field(ge=1)


class ProjectScoped(ToolArguments):
    """Arguments that name the project they act inside.

    The project is required and never defaulted. A tool that guessed which
    project a caller meant would be inferring an isolation boundary, which is
    the one thing this layer must never do.
    """

    project_id: Identifier = Field(description="The project this call acts inside. Required.")


class ListSkillsArguments(ToolArguments):
    """Filters for listing skill versions."""

    status: LifecycleStatus | None = Field(
        default="active",
        description="Lifecycle to list. Defaults to active versions.",
    )
    category: Identifier | None = None
    tag: Identifier | None = None
    trust_level: SkillTrustLevel | None = None
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=512)


class GetSkillArguments(ToolArguments):
    """One skill version, with its history and a page of chunks."""

    skill_id: Identifier
    version: SemanticVersion | None = Field(
        default=None,
        description="An exact version, including a superseded one. Defaults to the active one.",
    )
    chunk_limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=50)
    chunk_cursor: str | None = Field(default=None, max_length=512)


class SearchSkillsArguments(ToolArguments):
    """A retrieval query answered from skills alone."""

    query: Query
    project_id: Identifier | None = Field(
        default=None,
        description="Optional. Skills are global knowledge and are not project-owned.",
    )
    category: Identifier | None = None
    tags: TagList = Field(default_factory=list)
    trust_level: SkillTrustLevel | None = None
    limit: int = Field(default=DEFAULT_RETRIEVAL_LIMIT, ge=1, le=MAX_RETRIEVAL_LIMIT)
    token_budget: int | None = Field(default=None, ge=MIN_TOKEN_BUDGET)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str] | None:
        """Reject repeated filters."""
        return _unique(value)


class ListProjectsArguments(ToolArguments):
    """Bounded listing of project boundaries."""

    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=512)


class GetProjectArguments(ToolArguments):
    """One project boundary."""

    project_id: Identifier


class ResolveProjectArguments(ToolArguments):
    """Explicit client-supplied metadata for deterministic project resolution."""

    project_id: Identifier | None = None
    git_remote: str | None = Field(default=None, min_length=1, max_length=2_048)
    repository_url: str | None = Field(default=None, min_length=1, max_length=2_048)
    repository_name: str | None = Field(default=None, min_length=1, max_length=2_048)
    workspace_hint: str | None = Field(default=None, min_length=1, max_length=2_048)
    # ``workspace_hint`` is the established V4 MCP spelling.  Keep it intact
    # while accepting the binding's public name for MCP-compatible clients.
    workspace_alias: str | None = Field(default=None, min_length=1, max_length=2_048)

    @model_validator(mode="after")
    def require_identifier(self) -> "ResolveProjectArguments":
        if (
            self.workspace_hint is not None
            and self.workspace_alias is not None
            and self.workspace_hint != self.workspace_alias
        ):
            raise ValueError("workspace_hint and workspace_alias must match when both are provided")
        if all(
            value is None
            for value in (
                self.project_id,
                self.git_remote,
                self.repository_url,
                self.repository_name,
                self.workspace_hint,
                self.workspace_alias,
            )
        ):
            raise ValueError(
                "provide an explicit project_id or one repository/workspace identifier"
            )
        return self


class SearchContextArguments(ProjectScoped):
    """A retrieval query answered from one project's own context."""

    query: Query
    types: Annotated[list[ContextType], Field(max_length=MAX_TYPES)] = Field(default_factory=list)
    tags: TagList = Field(default_factory=list)
    status: LifecycleStatus | None = "active"
    minimum_importance: Fraction = 0.0
    phrase: bool = Field(
        default=False,
        description="Require the query words to appear together, in order.",
    )
    limit: int = Field(default=DEFAULT_RETRIEVAL_LIMIT, ge=1, le=MAX_RETRIEVAL_LIMIT)

    @field_validator("tags", "types")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str] | None:
        """Reject repeated filters."""
        return _unique(value)


class GetContextArguments(ProjectScoped):
    """One stored context item, addressed from inside its own project."""

    context_id: ObjectIdHex


class SaveContextArguments(ProjectScoped):
    """One durable context item to store in a project."""

    type: ContextType
    title: ShortText
    content: ContentText
    importance: Fraction = Field(description="How much this should outrank other context, 0..1.")
    source: Identifier = Field(
        default="mcp",
        description="Where this came from. Defaults to the MCP server itself.",
    )
    tags: TagList = Field(default_factory=list)
    status: LifecycleStatus = "active"
    external_id: Identifier | None = Field(
        default=None,
        description="Idempotency key. Repeating a save with it returns what is stored.",
    )
    idempotency_key: Identifier | None = Field(
        default=None,
        description=(
            "Optional retry key for this client. The same key and request replays the original "
            "result; never use a secret as a key."
        ),
    )

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str] | None:
        """Reject ambiguous duplicate tags."""
        return _unique(value)

    @model_validator(mode="after")
    def compatible_idempotency_identifiers(self) -> "SaveContextArguments":
        if (
            self.external_id is not None
            and self.idempotency_key is not None
            and self.external_id != self.idempotency_key
        ):
            raise ValueError("external_id and idempotency_key must match when both are provided")
        return self


class SearchMemoryArguments(ToolArguments):
    """A retrieval query answered from the memory this caller may see."""

    query: Query
    project_id: Identifier | None = Field(
        default=None,
        description="Naming a project searches its memory and global memory together.",
    )
    scopes: Annotated[list[MemoryScope], Field(max_length=4)] | None = Field(
        default=None,
        description="Restrict shared and authenticated-client memory scopes.",
    )
    types: Annotated[list[Identifier], Field(max_length=MAX_TYPES)] = Field(default_factory=list)
    tags: TagList = Field(default_factory=list)
    status: LifecycleStatus | None = "active"
    minimum_importance: Fraction = 0.0
    phrase: bool = False
    limit: int = Field(default=DEFAULT_RETRIEVAL_LIMIT, ge=1, le=MAX_RETRIEVAL_LIMIT)

    @field_validator("tags", "types", "scopes")
    @classmethod
    def unique_values(cls, value: list[str] | None) -> list[str] | None:
        """Reject repeated filters."""
        return _unique(value)

    @model_validator(mode="after")
    def validate_scopes(self) -> "SearchMemoryArguments":
        """Require a project whenever project memory is searched."""
        if (
            self.scopes is not None
            and any(scope in {"project", "project_agent"} for scope in self.scopes)
            and self.project_id is None
        ):
            raise ValueError("searching project-scoped memory requires project_id")
        return self


class SaveMemoryArguments(ToolArguments):
    """One memory to store on purpose.

    Memory is never written by watching a conversation. It exists because
    something asked for it, which is what this call is.
    """

    type: Identifier = Field(description="What kind of memory, e.g. preference or convention.")
    content: ContentText
    importance: Fraction
    confidence: Fraction = Field(description="How sure the writer is of this, 0..1.")
    source: Identifier = Field(
        default="mcp",
        description="Where this came from. Defaults to the MCP server itself.",
    )
    scope: MemoryScope | None = Field(
        default=None,
        description=(
            "Optional memory visibility. Agent scopes derive their client identity from "
            "the authenticated gateway credential."
        ),
    )
    project_id: Identifier | None = Field(
        default=None,
        description="Owning project. Omit for a global memory every project may see.",
    )
    summary: ShortText | None = Field(
        default=None,
        description="The memory in one line, used as its title in retrieved context.",
    )
    tags: TagList = Field(default_factory=list)
    status: LifecycleStatus = "active"
    external_id: Identifier | None = Field(
        default=None,
        description="Idempotency key. Repeating a save with it returns what is stored.",
    )
    idempotency_key: Identifier | None = Field(
        default=None,
        description=(
            "Optional retry key for this client. The same key and request replays the original "
            "result; never use a secret as a key."
        ),
    )
    supersedes: ObjectIdHex | None = Field(
        default=None,
        description="The memory this one corrects. It is marked superseded, never deleted.",
    )

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str] | None:
        """Reject ambiguous duplicate tags."""
        return _unique(value)

    @model_validator(mode="after")
    def validate_scope_address(self) -> "SaveMemoryArguments":
        if (
            self.external_id is not None
            and self.idempotency_key is not None
            and self.external_id != self.idempotency_key
        ):
            raise ValueError("external_id and idempotency_key must match when both are provided")
        if self.scope is None:
            return self
        requires_project = self.scope in {"project", "project_agent"}
        if (self.project_id is not None) != requires_project:
            raise ValueError(f"{self.scope} memory has an invalid project_id")
        return self


class UpdateMemoryArguments(ToolArguments):
    """A partial update to one stored memory.

    Scope and both ends of a supersession link are identity rather than
    content, so neither can be changed here: a memory never moves between
    projects and never loses its history.
    """

    memory_id: ObjectIdHex
    project_id: Identifier | None = Field(
        default=None,
        description="The scope the memory lives in. Omit for a global memory.",
    )
    scope: MemoryScope | None = Field(
        default=None,
        description="Scope the memory lives in; agent scopes use the authenticated client.",
    )
    type: Identifier | None = None
    content: ContentText | None = None
    summary: ShortText | None = None
    importance: Fraction | None = None
    confidence: Fraction | None = None
    tags: TagList | None = None
    status: LifecycleStatus | None = None
    expected_revision: int = Field(
        ge=1,
        description=(
            "Required revision last read. A mismatch reports a conflict instead of overwriting."
        ),
    )

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str] | None) -> list[str] | None:
        """Reject ambiguous duplicate tags."""
        return _unique(value)

    @model_validator(mode="after")
    def require_a_change(self) -> "UpdateMemoryArguments":
        """Reject an update that would change nothing."""
        changed = (
            self.type,
            self.content,
            self.summary,
            self.importance,
            self.confidence,
            self.tags,
            self.status,
        )
        if all(value is None for value in changed):
            raise ValueError("provide at least one field to update")
        return self


class SearchKnowledgeArguments(ToolArguments):
    """One question, answered from every source the caller may see."""

    query: Query
    project_id: Identifier | None = Field(
        default=None,
        description="Scope the search to one project. Global knowledge stays visible.",
    )
    sources: Annotated[list[SourceType], Field(max_length=4)] | None = Field(
        default=None,
        description="Restrict which sources answer. All retrievable ones by default.",
    )
    context_types: Annotated[list[ContextType], Field(max_length=MAX_TYPES)] = Field(
        default_factory=list
    )
    memory_types: Annotated[list[Identifier], Field(max_length=MAX_TYPES)] = Field(
        default_factory=list
    )
    memory_scopes: Annotated[list[MemoryScope], Field(max_length=4)] | None = None
    tags: TagList = Field(default_factory=list)
    trust_level: SkillTrustLevel | None = None
    minimum_importance: Fraction = 0.0
    phrase: bool = False
    token_budget: int | None = Field(
        default=None,
        ge=MIN_TOKEN_BUDGET,
        description="Context tokens to return at most. Clamped to the platform's maximum.",
    )
    limit: int = Field(default=DEFAULT_RETRIEVAL_LIMIT, ge=1, le=MAX_RETRIEVAL_LIMIT)

    @field_validator("tags", "sources", "context_types", "memory_types", "memory_scopes")
    @classmethod
    def unique_values(cls, value: list[str] | None) -> list[str] | None:
        """Reject repeated filters."""
        return _unique(value)

    @model_validator(mode="after")
    def validate_scope(self) -> "SearchKnowledgeArguments":
        """Reject a request whose filters could not be satisfied as written."""
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


class SystemStatusArguments(ToolArguments):
    """No arguments: readiness describes the platform, not a request."""


class GetCreatorProfileArguments(ToolArguments):
    """One creator profile, always addressed explicitly."""

    profile_id: Identifier


class UpdateCreatorProfileArguments(ToolArguments):
    """A revision-guarded partial update to creator preference data."""

    profile_id: Identifier
    expected_revision: int = Field(ge=1)
    name: ShortText | None = None
    status: CreatorProfileStatus | None = None
    audiences: list[ShortText] | None = Field(default=None, max_length=50)
    domains: list[ShortText] | None = Field(default=None, max_length=50)
    voice: CreatorVoice | None = None
    writing_preferences: list[ShortText] | None = Field(default=None, max_length=50)
    formatting_preferences: list[ShortText] | None = Field(default=None, max_length=50)
    terminology_preferences: list[ShortText] | None = Field(default=None, max_length=50)
    language_preferences: list[ShortText] | None = Field(default=None, max_length=50)
    tone_preferences: list[ShortText] | None = Field(default=None, max_length=50)
    topics_of_interest: list[ShortText] | None = Field(default=None, max_length=50)
    topics_to_avoid: list[ShortText] | None = Field(default=None, max_length=50)
    content_objectives: list[ShortText] | None = Field(default=None, max_length=50)
    tags: TagList | None = None

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
        """Reject ambiguous duplicate preference values."""
        return _unique(value)

    @model_validator(mode="after")
    def require_a_change(self) -> "UpdateCreatorProfileArguments":
        changed = self.model_fields_set - {"profile_id", "expected_revision"}
        if not changed:
            raise ValueError("provide at least one field to update")
        return self


class ListContentPillarsArguments(ToolArguments):
    """Bounded pillar listing inside one explicit creator profile."""

    profile_id: Identifier
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=512)


class SearchContentArguments(ToolArguments):
    """Search only the current V2 content records in one creator profile."""

    profile_id: Identifier
    query: Query
    retrieval_profile: Literal[
        "content_creation", "content_history", "content_ideation", "content_repurposing"
    ] = "content_history"
    method: Literal["vector", "lexical", "hybrid"] = "hybrid"
    platform: ContentPlatform | None = None
    project_id: Identifier | None = None
    status: ContentStatus | ContentBriefStatus | None = None
    pillar_id: Identifier | None = None
    tags: TagList = Field(default_factory=list)
    token_budget: int | None = Field(default=None, ge=MIN_TOKEN_BUDGET)
    limit: int = Field(default=DEFAULT_RETRIEVAL_LIMIT, ge=1, le=MAX_RETRIEVAL_LIMIT)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str] | None:
        return _unique(value)


class GetContentArguments(ToolArguments):
    """Read one current content item through its creator-profile boundary."""

    profile_id: Identifier
    content_id: Identifier


class GetContentHistoryArguments(ToolArguments):
    """Read append-only history for one current item in one creator profile."""

    profile_id: Identifier
    content_id: Identifier
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=512)


class GetContentMetricsArguments(ToolArguments):
    """Read generic, newest-first metrics for one content item."""

    profile_id: Identifier
    content_id: Identifier
    platform: ContentPlatform | None = None
    captured_after: AwareDatetime | None = None
    captured_before: AwareDatetime | None = None
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def valid_time_range(self) -> "GetContentMetricsArguments":
        if (
            self.captured_after is not None
            and self.captured_before is not None
            and self.captured_after > self.captured_before
        ):
            raise ValueError("captured_after must be on or before captured_before")
        return self


class ReviewContentForPublicationArguments(ToolArguments):
    """Run the authorized, redacted Phase 8 safety review for one current item."""

    profile_id: Identifier
    content_id: Identifier


class ReviewContentFactsArguments(ToolArguments):
    """Map one saved draft to explicit V3 claim evidence without changing it."""

    profile_id: Identifier
    content_id: Identifier
    research_project_id: Identifier | None = None
    claim_ids: list[Identifier] = Field(default_factory=list, max_length=50)
    claim_statements: list[ContentText] = Field(default_factory=list, max_length=100)

    @field_validator("claim_ids", "claim_statements")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str] | None:
        return _unique(value)


class MarkContentPublishedArguments(ToolArguments):
    """Explicitly record publication of one approved stored content item only."""

    profile_id: Identifier
    content_id: Identifier
    expected_revision: int = Field(ge=1)
    provider: ContentPublicationProvider = ContentPublicationProvider.MANUAL
    idempotency_key: Identifier | None = None


class DiscoverContentIdeasArguments(ToolArguments):
    """Assemble recent, source-backed idea material for one creator profile."""

    profile_id: Identifier
    project_id: Identifier | None = None
    pillar_id: Identifier | None = None
    time_range: DiscoveryTimeRange | None = None
    platform: ContentPlatform | None = None
    limit: int = Field(default=DEFAULT_RETRIEVAL_LIMIT, ge=1, le=50)


class SaveContentIdeaArguments(ToolArguments):
    """Store agent-reviewed idea data; discovery never saves it automatically."""

    profile_id: Identifier
    idea_id: Identifier
    title: ShortText
    description: ContentText
    pillar_id: Identifier | None = None
    platforms: Annotated[list[ContentPlatform], Field(min_length=1, max_length=16)]
    origin: ContentOrigin
    tags: TagList = Field(default_factory=list)
    priority: Fraction

    @field_validator("platforms", "tags")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str] | None:
        return _unique(value)

    @property
    def project_id(self) -> str | None:
        """Expose nested provenance to MCP's standard project-grant gate."""
        return self.origin.project_id


class SearchContentIdeasArguments(ToolArguments):
    """Search the Phase 4 corpus but return only existing idea records."""

    profile_id: Identifier
    query: Query
    project_id: Identifier | None = None
    pillar_id: Identifier | None = None
    platform: ContentPlatform | None = None
    tags: TagList = Field(default_factory=list)
    token_budget: int | None = Field(default=None, ge=MIN_TOKEN_BUDGET)
    limit: int = Field(default=DEFAULT_RETRIEVAL_LIMIT, ge=1, le=MAX_RETRIEVAL_LIMIT)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str] | None:
        return _unique(value)


class PrepareContentBriefArguments(ToolArguments):
    """Build a non-persistent evidence brief for one saved idea and platform."""

    profile_id: Identifier
    idea_id: Identifier
    platform: ContentPlatform
    project_id: Identifier | None = None
    pillar_id: Identifier | None = None


class CheckContentNoveltyArguments(ToolArguments):
    """Compare a proposed topic with visible creator-content history."""

    profile_id: Identifier
    title: ShortText
    description: ContentText
    tags: TagList = Field(default_factory=list)
    pillar_id: Identifier | None = None
    platform: ContentPlatform | None = None
    project_id: Identifier | None = None
    method: Literal["vector", "lexical", "hybrid"] = "hybrid"
    token_budget: int | None = Field(default=None, ge=MIN_TOKEN_BUDGET)
    limit: int = Field(default=DEFAULT_RETRIEVAL_LIMIT, ge=1, le=MAX_RETRIEVAL_LIMIT)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str] | None:
        return _unique(value)


class PrepareRepurposePlanArguments(ToolArguments):
    """Prepare a non-generative plan for one stored content, brief, or idea source."""

    profile_id: Identifier
    content_id: Identifier
    source_kind: Literal["idea", "brief", "content"] = "content"
    targets: Annotated[list[ContentPlatform], Field(min_length=1, max_length=16)]
    method: Literal["vector", "lexical", "hybrid"] = "hybrid"
    token_budget: int | None = Field(default=None, ge=MIN_TOKEN_BUDGET)
    limit: int = Field(default=DEFAULT_RETRIEVAL_LIMIT, ge=1, le=MAX_RETRIEVAL_LIMIT)

    @field_validator("targets")
    @classmethod
    def unique_targets(cls, value: list[ContentPlatform]) -> list[ContentPlatform]:
        if len(value) != len(set(value)):
            raise ValueError("repurpose target platforms must be unique")
        return value


class CreateResearchProjectArguments(ToolArguments):
    """Arguments for creating one explicit Phase 1 research question."""

    research_project_id: Identifier
    name: ShortText
    question: ContentText
    type: ResearchProjectType
    related_project_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    status: Literal["active", "archived"] = "active"
    idempotency_key: Identifier | None = Field(
        default=None,
        description="Optional retry key for this client; never use a secret as a key.",
    )

    @field_validator("related_project_ids")
    @classmethod
    def unique_related_projects(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("related_project_ids must be unique")
        return value


class GetResearchProjectArguments(ToolArguments):
    """Address one research project by its stable public identifier."""

    research_project_id: Identifier


class ListResearchProjectsArguments(ToolArguments):
    """Bounded filters for the research-project metadata directory."""

    status: Literal["active", "archived"] | None = None
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=512)


class SourceSnapshotArguments(ToolArguments):
    """Caller-supplied immutable source-version metadata; MCP never obtains it itself."""

    snapshot_id: Identifier
    retrieved_at: AwareDatetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    version_identifier: str | None = Field(default=None, min_length=1, max_length=2_048)
    metadata: dict[str, object] = Field(default_factory=dict)


class AddResearchSourceArguments(ToolArguments):
    """Arguments for one inert V3 source registry entry."""

    research_project_id: Identifier
    source_id: Identifier
    source_type: ResearchSourceType
    uri: Annotated[str, Field(min_length=1, max_length=2_048)]
    title: ShortText
    publisher: str | None = Field(default=None, min_length=1, max_length=500)
    author: str | None = Field(default=None, min_length=1, max_length=500)
    published_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime | None = None
    source_class: SourceClass = SourceClass.UNKNOWN
    trust_metadata: dict[str, object] = Field(default_factory=dict)
    status: Literal["active", "archived"] = "active"
    snapshot: SourceSnapshotArguments | None = None


class GetResearchSourceArguments(ToolArguments):
    """Address one registered source by its stable source identifier."""

    source_id: Identifier


class IngestResearchSourceArguments(ToolArguments):
    """Manual text or one registered repository; MCP cannot name paths, URLs, or commands."""

    source_id: Identifier
    content: ContentText | None = None
    content_type: Literal["text/plain", "text/markdown"] | None = None
    title: ShortText | None = None
    language: Annotated[str, Field(min_length=1, max_length=64)] = "und"
    fetch_repository: bool = False
    idempotency_key: Identifier | None = Field(
        default=None,
        description="Optional retry key for this client; never use a secret as a key.",
    )

    @model_validator(mode="after")
    def validate_ingestion_mode(self) -> "IngestResearchSourceArguments":
        if self.fetch_repository:
            if self.content is not None or self.content_type is not None or self.title is not None:
                raise ValueError("fetch_repository cannot include manual source content")
            return self
        if self.content is None:
            raise ValueError("content is required unless fetch_repository is selected")
        return self


class SearchResearchArguments(ToolArguments):
    """Bounded Phase 5 research search; source text remains untrusted data."""

    research_project_id: Identifier
    query: Query
    retrieval_profile: Literal[
        "research_general",
        "research_primary_sources",
        "research_recent",
        "research_content",
        "research_architecture",
        "research_code",
    ] = "research_general"
    method: Literal["vector", "lexical", "hybrid"] = "hybrid"
    phrase: bool = False
    source_classes: Annotated[list[SourceClass], Field(max_length=10)] = Field(default_factory=list)
    source_types: Annotated[list[ResearchSourceType], Field(max_length=9)] = Field(
        default_factory=list
    )
    publisher: str | None = Field(default=None, min_length=1, max_length=500)
    domain: str | None = Field(default=None, min_length=1, max_length=253)
    published_after: AwareDatetime | None = None
    published_before: AwareDatetime | None = None
    retrieved_after: AwareDatetime | None = None
    as_of: AwareDatetime | None = None
    related_project_id: Identifier | None = None
    token_budget: int | None = Field(default=None, ge=MIN_TOKEN_BUDGET, le=16_000)
    limit: int = Field(default=DEFAULT_RETRIEVAL_LIMIT, ge=1, le=MAX_RETRIEVAL_LIMIT)
    cursor: str | None = Field(default=None, max_length=1_024)

    @field_validator("source_classes", "source_types")
    @classmethod
    def unique_source_filters(cls, value: list[str]) -> list[str]:
        return _unique(value) or []

    @field_validator("domain")
    @classmethod
    def hostname_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold()
        if not normalized or "://" in normalized or "/" in normalized or "@" in normalized:
            raise ValueError("domain must be a hostname, not a URL")
        return normalized

    @model_validator(mode="after")
    def valid_published_range(self) -> "SearchResearchArguments":
        if (
            self.published_after is not None
            and self.published_before is not None
            and self.published_after > self.published_before
        ):
            raise ValueError("published_after must not be after published_before")
        return self


class ResearchSourceReferenceArguments(ToolArguments):
    """One already-registered provenance reference; MCP cannot read a file or URL."""

    source_id: Identifier
    snapshot_id: Identifier | None = None
    document_id: Identifier | None = None
    chunk_id: Identifier | None = None


class SaveResearchNoteArguments(ToolArguments):
    """Create one working research artifact, explicitly separate from durable memory."""

    research_project_id: Identifier
    note_id: Identifier
    title: ShortText
    body: ContentText
    source_refs: list[ResearchSourceReferenceArguments] = Field(
        default_factory=list, max_length=100
    )
    status: ResearchNoteStatus = ResearchNoteStatus.WORKING

    @field_validator("source_refs")
    @classmethod
    def unique_source_refs(
        cls, value: list[ResearchSourceReferenceArguments]
    ) -> list[ResearchSourceReferenceArguments]:
        keys = [item.model_dump_json() for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("source_refs must be unique")
        return value


class SearchResearchNotesArguments(ToolArguments):
    """Bounded lexical search of authorized working research notes."""

    research_project_id: Identifier
    query: Query
    phrase: bool = False
    limit: int = Field(default=DEFAULT_RETRIEVAL_LIMIT, ge=1, le=MAX_RETRIEVAL_LIMIT)


class CreateResearchClaimArguments(ToolArguments):
    """Create one explicit claim assessment; no semantic score is accepted as confidence."""

    research_project_id: Identifier
    claim_id: Identifier
    statement: ContentText
    status: ClaimState = ClaimState.UNREVIEWED
    confidence: Fraction | None = None
    confidence_rationale: Annotated[str, Field(min_length=1, max_length=4_000)] | None = None
    supporting_citations: list[Identifier] = Field(default_factory=list, max_length=100)
    contradicting_citations: list[Identifier] = Field(default_factory=list, max_length=100)

    @field_validator("supporting_citations", "contradicting_citations")
    @classmethod
    def unique_citations(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("citation identifiers must be unique")
        return value

    @model_validator(mode="after")
    def non_overlapping_citations(self) -> "CreateResearchClaimArguments":
        if set(self.supporting_citations) & set(self.contradicting_citations):
            raise ValueError("a citation cannot support and contradict the same claim")
        return self


class UpdateResearchClaimArguments(ToolArguments):
    """Revision-guard one explicit claim edit; this tool cannot promote anything."""

    research_project_id: Identifier
    claim_id: Identifier
    expected_revision: int = Field(ge=1)
    statement: ContentText | None = None
    status: ClaimState | None = None
    confidence: Fraction | None = None
    confidence_rationale: Annotated[str, Field(min_length=1, max_length=4_000)] | None = None
    supporting_citations: list[Identifier] | None = Field(default=None, max_length=100)
    contradicting_citations: list[Identifier] | None = Field(default=None, max_length=100)

    @field_validator("supporting_citations", "contradicting_citations")
    @classmethod
    def unique_citations(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("citation identifiers must be unique")
        return value

    @model_validator(mode="after")
    def require_change(self) -> "UpdateResearchClaimArguments":
        if not self.model_fields_set - {"research_project_id", "claim_id", "expected_revision"}:
            raise ValueError("provide at least one claim field to update")
        if (
            self.supporting_citations is not None
            and self.contradicting_citations is not None
            and set(self.supporting_citations) & set(self.contradicting_citations)
        ):
            raise ValueError("a citation cannot support and contradict the same claim")
        return self


class BuildResearchBriefArguments(ToolArguments):
    """Build structured, cited research material; it neither writes knowledge nor prose."""

    research_project_id: Identifier
    brief_id: Identifier
    scope: ContentText
    additional_unknowns: list[ContentText] = Field(default_factory=list, max_length=100)
    next_questions: list[ContentText] = Field(default_factory=list, max_length=100)
    status: ResearchBriefStatus = ResearchBriefStatus.DRAFT

    @field_validator("additional_unknowns", "next_questions")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        return _unique(value) or []


class BuildEvidencePacketArguments(ToolArguments):
    """Build a bounded packet from authored citations and authorized research retrieval."""

    query: Query
    research_project_id: Identifier
    project_id: Identifier | None = Field(
        default=None,
        description="Optional linked V1 project to constrain the evidence packet.",
    )
    token_budget: int | None = Field(default=None, ge=MIN_TOKEN_BUDGET, le=16_000)


class GetClaimEvidenceArguments(ToolArguments):
    """Read authored citation relations for one claim; it never evaluates the claim."""

    research_project_id: Identifier
    claim_id: Identifier


class ProposeKnowledgeCandidateArguments(ToolArguments):
    """Create a pending proposal backed by explicit project-local research evidence."""

    research_project_id: Identifier
    candidate_id: Identifier
    candidate_type: CandidateType
    target_project_id: Identifier | None = None
    target_skill_id: Identifier | None = None
    current_skill_version: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    title: ShortText
    content: ContentText
    source_claim_ids: list[Identifier] = Field(min_length=1, max_length=100)
    source_citation_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    confidence: Fraction | None = None
    confidence_rationale: Annotated[str, Field(min_length=1, max_length=4_000)] | None = None
    idempotency_key: Identifier | None = Field(
        default=None,
        description="Optional retry key for this client; never use a secret as a key.",
    )

    @field_validator("source_claim_ids", "source_citation_ids")
    @classmethod
    def unique_provenance(cls, value: list[str]) -> list[str]:
        return _unique(value) or []

    @model_validator(mode="after")
    def validate_target(self) -> "ProposeKnowledgeCandidateArguments":
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


class ProposeSkillUpdateArguments(ToolArguments):
    """Create a cited, generated Markdown-patch proposal without applying it."""

    research_project_id: Identifier
    proposal_id: Identifier
    skill_id: Identifier
    current_version: SemanticVersion
    proposed_change: ContentText
    proposed_patch: ContentText
    source_claim_ids: list[Identifier] = Field(min_length=1, max_length=100)
    source_citation_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    reason: ContentText

    @field_validator("source_claim_ids", "source_citation_ids")
    @classmethod
    def unique_provenance(cls, value: list[str]) -> list[str]:
        return _unique(value) or []


class ListKnowledgeCandidatesArguments(ToolArguments):
    """Bounded listing of research proposals visible through preserved grants."""

    research_project_id: Identifier
    status: CandidateState | None = None
    target_project_id: Identifier | None = None
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=512)


class ApproveKnowledgeCandidateArguments(ToolArguments):
    """Approve a pending candidate without promoting it to a V1 target."""

    research_project_id: Identifier
    candidate_id: Identifier
    expected_revision: int = Field(ge=1)
    reason: Annotated[str, Field(min_length=1, max_length=4_000)] | None = None


class PromoteKnowledgeCandidateArguments(ToolArguments):
    """Explicitly promote one approved candidate through the authorized V1 target."""

    research_project_id: Identifier
    candidate_id: Identifier
    expected_revision: int = Field(ge=1)
    supersedes_context_id: ObjectIdHex | None = Field(
        default=None,
        description="Explicit active V1 context to supersede after a replacement is stored.",
    )


class RejectKnowledgeCandidateArguments(ApproveKnowledgeCandidateArguments):
    """Reject a candidate or return it for changes, without promotion."""

    decision: Literal["rejected", "needs_changes"] = "rejected"


def json_schema(model: type[BaseModel]) -> dict[str, object]:
    """Render one arguments model as the JSON Schema its tool advertises.

    `extra="forbid"` becomes `additionalProperties: false`, so the protocol
    layer rejects an unknown argument before this server ever sees it.
    """
    schema = model.model_json_schema()
    schema.setdefault("additionalProperties", False)
    return schema


ToolArgumentModel = Literal[
    "list_skills",
    "get_skill",
    "search_skills",
    "list_projects",
    "get_project",
    "search_context",
    "get_context",
    "save_context",
    "search_memory",
    "save_memory",
    "update_memory",
    "search_knowledge",
    "get_system_status",
    "get_creator_profile",
    "update_creator_profile",
    "list_content_pillars",
    "search_content",
    "get_content",
    "get_content_history",
    "get_content_metrics",
    "discover_content_ideas",
    "save_content_idea",
    "search_content_ideas",
    "prepare_content_brief",
    "check_content_novelty",
    "prepare_repurpose_plan",
    "create_research_project",
    "get_research_project",
    "list_research_projects",
    "add_research_source",
    "get_research_source",
    "ingest_research_source",
    "search_research",
    "save_research_note",
    "search_research_notes",
    "create_research_claim",
    "update_research_claim",
    "build_evidence_packet",
    "get_claim_evidence",
    "build_research_brief",
    "propose_knowledge_candidate",
    "propose_skill_update",
    "list_knowledge_candidates",
    "approve_knowledge_candidate",
    "reject_knowledge_candidate",
    "promote_knowledge_candidate",
]
