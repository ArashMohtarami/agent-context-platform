"""Environment-driven application configuration."""

from functools import lru_cache
from ipaddress import ip_network
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.embeddings.types import (
    MAX_EMBEDDING_ATTEMPTS,
    MAX_EMBEDDING_BATCH_SIZE,
    MAX_EMBEDDING_BATCH_TEXTS,
    MAX_EMBEDDING_DIMENSIONS,
    MAX_EMBEDDING_MODEL_NAME_LENGTH,
    MAX_EMBEDDING_RETRY_BACKOFF_SECONDS,
    MAX_EMBEDDING_TEXT_CHARACTERS,
    EmbeddingDevice,
)
from app.mcp.auth import Authorization, build_authorization
from app.models.common import validate_identifier
from app.retrieval.ranking import RankingWeights
from app.retrieval.types import (
    DEFAULT_TOKEN_BUDGET,
    MAX_TOKEN_BUDGET,
    MIN_TOKEN_BUDGET,
)

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]


def _split(value: str | None) -> list[str] | None:
    """Read a comma-separated setting, treating an empty one as unset."""
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


class McpSettings(BaseSettings):
    """What the MCP server needs, and deliberately nothing else.

    The MCP process reaches the platform through the Context API, so it holds
    no database credentials: the component an agent talks to directly is the
    one that should be able to do the least.

    `Settings` extends this, so a deployment configures one surface and the API
    process still reads every MCP value from the same place.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    log_level: LogLevel = "INFO"

    # The MCP server reaches the platform through the Context API rather than
    # the database, so this is the only address it needs. It serves stdio, so
    # it binds no port of its own.
    mcp_server_name: str = Field(default="agent-context-platform", min_length=1, max_length=128)
    mcp_api_base_url: str = "http://127.0.0.1:8000"
    mcp_api_token: SecretStr | None = None
    mcp_api_token_file: Path | None = None
    mcp_request_timeout_seconds: float = Field(default=120.0, gt=0.0, le=300.0)
    # Per-client gateway controls are process-local in this self-hosted,
    # single-instance deployment.  Their adapter is deliberately separate so
    # a shared backend can replace it if the deployment topology changes.
    mcp_requests_per_minute: int = Field(default=120, ge=1, le=100_000)
    mcp_concurrent_calls: int = Field(default=8, ge=1, le=1_000)
    mcp_writes_per_minute: int = Field(default=30, ge=1, le=100_000)
    mcp_research_ingestions_per_hour: int = Field(default=10, ge=1, le=10_000)
    mcp_long_running_jobs_per_client: int = Field(default=2, ge=1, le=1_000)
    mcp_fast_timeout_seconds: float = Field(default=10.0, gt=0.0, le=300.0)
    mcp_standard_timeout_seconds: float = Field(default=30.0, gt=0.0, le=900.0)
    mcp_long_running_timeout_seconds: float = Field(default=120.0, gt=0.0, le=3_600.0)
    mcp_idempotency_retention_seconds: int = Field(default=86_400, ge=60, le=2_592_000)
    # Comma-separated. Leaving any of these empty grants every scope, project,
    # or creator profile, which is the documented local case: a stdio server
    # is reachable only by the process that spawned it.
    mcp_scopes: str | None = None
    mcp_project_ids: str | None = None
    mcp_profile_ids: str | None = None
    # A launcher may associate one MCP process with an already-created V4
    # session.  Phase 2 only propagates it as provenance; it does not create
    # or manage sessions.
    mcp_session_id: str | None = None

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, value: object) -> LogLevel:
        """Normalize and validate supported standard-library log levels."""
        if not isinstance(value, str):
            raise ValueError("LOG_LEVEL must be a string")
        normalized = value.upper()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("LOG_LEVEL must be CRITICAL, ERROR, WARNING, INFO, or DEBUG")
        return cast(LogLevel, normalized)

    @field_validator("mcp_api_base_url")
    @classmethod
    def validate_api_base_url(cls, value: str) -> str:
        """Require a plain HTTP(S) origin without embedded credentials."""
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("MCP_API_BASE_URL must be an http or https URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("MCP_API_BASE_URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("MCP_API_BASE_URL must not carry a query or fragment")
        return value

    @field_validator("mcp_api_token_file", mode="before")
    @classmethod
    def empty_mcp_token_file_is_unconfigured(cls, value: object) -> object:
        """Treat Compose's empty optional file setting as absent, not `.`."""
        return None if value == "" else value

    @field_validator("mcp_session_id")
    @classmethod
    def validate_mcp_session_id(cls, value: str | None) -> str | None:
        """Keep optional gateway provenance identifiers safe for headers and logs."""
        return None if value is None else validate_identifier(value)

    @model_validator(mode="after")
    def validate_mcp_token_source(self) -> "McpSettings":
        """Require at most one API-token source."""
        if self.mcp_api_token is not None and self.mcp_api_token_file is not None:
            raise ValueError("configure at most one of MCP_API_TOKEN or MCP_API_TOKEN_FILE")
        return self

    @model_validator(mode="after")
    def validate_mcp_authorization(self) -> "McpSettings":
        """Reject a grant that could not be enforced as written."""
        self.mcp_authorization()
        return self

    def mcp_authorization(self) -> Authorization:
        """Build the grant the MCP server runs under."""
        return build_authorization(
            _split(self.mcp_scopes),
            _split(self.mcp_project_ids),
            _split(self.mcp_profile_ids),
        )

    def mcp_api_token_value(self) -> str | None:
        """Read the API token without exposing it in settings output.

        Missing tokens never establish organization authority. Every data tool
        still performs fresh authenticated API preflight and fails closed.
        """
        if self.mcp_api_token is not None:
            value = self.mcp_api_token.get_secret_value()
        elif self.mcp_api_token_file is not None:
            try:
                value = self.mcp_api_token_file.read_text(encoding="utf-8").rstrip("\r\n")
            except OSError as error:
                raise ValueError("MCP_API_TOKEN_FILE cannot be read") from error
        else:
            return None
        if not value:
            raise ValueError("the MCP API token must not be empty")
        return value


class Settings(McpSettings):
    """The full platform configuration, loaded by the API and the tooling."""

    v6_tenant_foundation: bool = False
    v6_tenant_isolation: bool = False
    v6_local_identity: bool = False
    v6_sso: bool = False
    v6_management_policy: bool = False
    v6_human_management: bool = False
    v6_managed_agents: bool = False
    v6_console_enabled: bool = False
    v6_oidc_registrations_file: Path | None = None
    v6_browser_origin: str | None = None
    v6_mfa_keyring_file: Path | None = None
    v6_mail_sink_directory: Path | None = None

    @field_validator(
        "v6_mfa_keyring_file", "v6_mail_sink_directory", "v6_oidc_registrations_file", mode="before"
    )
    @classmethod
    def optional_identity_path(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def local_identity_configuration(self) -> "Settings":
        if self.v6_console_enabled and not self.v6_managed_agents:
            raise ValueError("console requires verified Phase7/8 managed identity and isolation")
        if self.v6_managed_agents and not self.v6_human_management:
            raise ValueError("managed agents require Phase 6 human management")
        if self.v6_human_management and not self.v6_management_policy:
            raise ValueError("human management requires Phase 5 policy")
        if self.v6_management_policy and not self.v6_sso:
            raise ValueError("management policy requires verified Phase 4 configuration")
        if self.v6_sso and (not self.v6_local_identity or self.v6_oidc_registrations_file is None):
            raise ValueError("SSO requires local identity and mounted approved registrations")
        if not self.v6_local_identity:
            return self
        if not (self.v6_tenant_foundation and self.v6_tenant_isolation and self.api_auth_enabled):
            raise ValueError("local identity requires authenticated tenant isolation")
        origin = urlsplit(self.v6_browser_origin or "")
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.path
            or origin.query
            or origin.fragment
            or origin.username
            or origin.password
        ):
            raise ValueError("local identity requires an exact HTTPS browser origin")
        if self.v6_mfa_keyring_file is None or self.v6_mail_sink_directory is None:
            raise ValueError(
                "local identity requires mounted MFA keys and an explicit local mail sink"
            )
        return self

    operator_metrics_token_file: Path | None = None

    @field_validator("operator_metrics_token_file", mode="before")
    @classmethod
    def optional_operator_metrics_path(cls, value: object) -> object:
        return None if value == "" else value

    app_env: str = "development"
    app_name: str = "Agent Context Platform"

    mongodb_uri: str
    mongodb_database: str = Field(min_length=1)
    mongodb_username: str = Field(min_length=1)
    mongodb_password: SecretStr | None = None
    mongodb_password_file: Path | None = None
    mongodb_replica_set: str = Field(default="rs0", min_length=1)
    mongodb_app_name: str = Field(default="agent-context-platform", min_length=1)
    mongodb_server_selection_timeout_ms: int = Field(default=5_000, gt=0)
    mongodb_connect_timeout_ms: int = Field(default=5_000, gt=0)
    # How long any one database operation may take before it is abandoned.
    # Without this a request can hang for as long as the server holds it: a
    # search issued while the search coordinator is down was measured taking
    # forty seconds, because connecting had already succeeded and nothing
    # bounded what came after.
    #
    # It subsumes server selection rather than adding to it, so this — not
    # `mongodb_server_selection_timeout_ms` — is also how long a request waits
    # when MongoDB is unreachable entirely. That is the trade being made: a
    # slower answer in the case where the platform is already down, in exchange
    # for a bounded one in every case. Readiness is the fast signal, answering
    # inside its own budget so an orchestrator drains traffic long before a
    # request here gives up.
    #
    # Raise it for a deployment whose startup index creation or migrations
    # legitimately take longer; nothing on the serving path comes close.
    mongodb_timeout_ms: int = Field(default=15_000, ge=1_000, le=600_000)
    # Bounded so a burst cannot open connections until the server refuses them.
    # One API process serves retrieval concurrently but not unboundedly, and a
    # pool larger than the work in flight only moves the queue into MongoDB.
    mongodb_max_pool_size: int = Field(default=50, ge=1, le=500)

    # Where backups are written, and how long each tier is kept. The directory
    # is created private and must not be inside anything the platform serves:
    # an archive is every document the deployment holds, in one file.
    backup_directory: Path = Path("backups")
    backup_retain_daily: int = Field(default=7, ge=0, le=365)
    backup_retain_weekly: int = Field(default=4, ge=0, le=520)
    backup_retain_monthly: int = Field(default=3, ge=0, le=120)
    backup_timeout_seconds: float = Field(default=3_600.0, gt=0.0, le=86_400.0)

    skills_directory: Path = Path("skills")
    skill_max_file_bytes: int = Field(default=1_000_000, ge=1, le=10_000_000)
    skill_chunk_min_tokens: int = Field(default=500, ge=100, le=2_000)
    skill_chunk_target_tokens: int = Field(default=650, ge=100, le=2_000)
    skill_chunk_max_tokens: int = Field(default=800, ge=100, le=2_000)
    skill_chunk_overlap_tokens: int = Field(default=75, ge=0, le=200)

    embedding_enabled: bool = True
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        min_length=1,
        max_length=MAX_EMBEDDING_MODEL_NAME_LENGTH,
    )
    embedding_dimensions: int = Field(default=384, ge=1, le=MAX_EMBEDDING_DIMENSIONS)
    embedding_device: EmbeddingDevice = "auto"
    embedding_batch_size: int = Field(default=32, ge=1, le=MAX_EMBEDDING_BATCH_SIZE)
    embedding_max_batch_texts: int = Field(default=256, ge=1, le=MAX_EMBEDDING_BATCH_TEXTS)
    embedding_max_text_characters: int = Field(
        default=20_000,
        ge=1,
        le=MAX_EMBEDDING_TEXT_CHARACTERS,
    )
    embedding_normalize: bool = True
    embedding_max_attempts: int = Field(default=3, ge=1, le=MAX_EMBEDDING_ATTEMPTS)
    embedding_retry_backoff_seconds: float = Field(
        default=0.5,
        ge=0.0,
        le=MAX_EMBEDDING_RETRY_BACKOFF_SECONDS,
    )
    embedding_model_cache_directory: Path | None = None
    embedding_allow_downloads: bool = True

    # Approximate-search candidate pool. The repository additionally clamps the
    # value to the limit MongoDB itself accepts.
    vector_search_candidate_multiplier: int = Field(default=10, ge=1, le=100)
    vector_search_max_candidates: int = Field(default=1_000, ge=1, le=5_000)

    # Hybrid ranking. These weights express relative influence and are
    # normalized before use, so they need not sum to one. They are starting
    # values for retrieval evaluation, not measured optima.
    retrieval_weight_vector: float = Field(default=0.45, ge=0.0, le=1.0)
    retrieval_weight_lexical: float = Field(default=0.25, ge=0.0, le=1.0)
    retrieval_weight_importance: float = Field(default=0.15, ge=0.0, le=1.0)
    retrieval_weight_recency: float = Field(default=0.10, ge=0.0, le=1.0)
    retrieval_weight_source_priority: float = Field(default=0.05, ge=0.0, le=1.0)
    retrieval_recency_half_life_days: float = Field(default=90.0, gt=0.0, le=3_650.0)
    retrieval_duplicate_overlap: float = Field(default=0.85, gt=0.0, le=1.0)
    retrieval_token_budget: int = Field(
        default=DEFAULT_TOKEN_BUDGET,
        ge=MIN_TOKEN_BUDGET,
        le=MAX_TOKEN_BUDGET,
    )
    retrieval_max_token_budget: int = Field(
        default=MAX_TOKEN_BUDGET,
        ge=MIN_TOKEN_BUDGET,
        le=MAX_TOKEN_BUDGET,
    )
    # Every search is reported to the structured log without its query text.
    # Enabling this additionally stores one `retrieval_logs` record per search,
    # query included, which is a deliberate retention decision rather than a
    # default: it is the one field that can describe who asked what.
    retrieval_log_enabled: bool = False

    # V3 research has a larger normal context packet, while the same hard ceiling
    # remains enforced by the shared context-budget primitive.
    research_retrieval_token_budget: int = Field(
        default=8_000, ge=MIN_TOKEN_BUDGET, le=MAX_TOKEN_BUDGET
    )
    research_retrieval_max_token_budget: int = Field(
        default=MAX_TOKEN_BUDGET, ge=MIN_TOKEN_BUDGET, le=MAX_TOKEN_BUDGET
    )
    # Acquisition is deliberately bounded independently of request concurrency:
    # parsing, embedding, and repository inspection are materially more
    # expensive than ordinary metadata requests.
    research_max_sources_per_project: int = Field(default=500, ge=1, le=100_000)
    research_max_ingestion_concurrency: int = Field(default=2, ge=1, le=64)
    research_max_chunks_per_ingestion: int = Field(default=500, ge=1, le=5_000)

    # V2 content retrieval has its own weights and budget.  It reuses the
    # ranking primitive, but changing creator/history ranking must never tune
    # the established engineering-search behavior above.
    content_retrieval_weight_vector: float = Field(default=0.45, ge=0.0, le=1.0)
    content_retrieval_weight_lexical: float = Field(default=0.25, ge=0.0, le=1.0)
    content_retrieval_weight_importance: float = Field(default=0.15, ge=0.0, le=1.0)
    content_retrieval_weight_recency: float = Field(default=0.10, ge=0.0, le=1.0)
    content_retrieval_weight_source_priority: float = Field(default=0.05, ge=0.0, le=1.0)
    content_retrieval_token_budget: int = Field(
        default=DEFAULT_TOKEN_BUDGET, ge=MIN_TOKEN_BUDGET, le=MAX_TOKEN_BUDGET
    )
    content_creation_priority_project_context: float = Field(default=1.00, ge=0.0, le=1.0)
    content_creation_priority_memory: float = Field(default=0.95, ge=0.0, le=1.0)
    content_creation_priority_technical_skill: float = Field(default=0.85, ge=0.0, le=1.0)
    content_creation_priority_content_skill: float = Field(default=0.85, ge=0.0, le=1.0)
    content_creation_priority_published_content: float = Field(default=0.70, ge=0.0, le=1.0)
    content_creation_priority_conversation: float = Field(default=0.40, ge=0.0, le=1.0)
    # A deployment may choose a current LinkedIn export ceiling without
    # embedding an external provider's limits into application code. ``None``
    # leaves manual plain-text export unbounded by this layer.
    content_linkedin_max_characters: int | None = Field(default=None, ge=1, le=200_000)
    # Phase 8's deterministic scanner has no authority to decide confidentiality.
    # These deployment controls identify material that always needs a human to
    # resolve before approval; matching values are never placed in logs or API
    # review responses.
    content_publication_block_high_severity: bool = True
    content_publication_sensitive_terms: tuple[str, ...] = Field(default_factory=tuple)
    content_publication_sensitive_project_ids: tuple[str, ...] = Field(default_factory=tuple)

    # Authentication. On by default: an endpoint that serves a project's private
    # knowledge should not be reachable without a credential because a setting
    # was left unset. Turning it off is a deployment decision that is logged at
    # startup and reported by readiness, so an open platform is never a quiet
    # one. `contextctl keys create` mints the first key without needing the API.
    api_auth_enabled: bool = True
    # HTTPS terminates at a reverse proxy in production.  Uvicorn must never
    # believe arbitrary client-supplied X-Forwarded-* headers: opt in only with
    # an explicit, non-wildcard proxy address allow-list.
    api_proxy_headers: bool = False
    api_forwarded_allow_ips: str | None = None
    # Every authorization decision is recorded. Turning this off keeps the
    # enforcement and loses only the trail.
    audit_log_enabled: bool = True
    # Bounded before the body is read. The largest documented body is one
    # context item, whose content the model already caps at 200 000 characters.
    api_max_request_bytes: int = Field(default=1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    # Remote MCP accepts plaintext HTTP only from a direct loopback peer while
    # running an explicitly local development/test deployment.  Production
    # must terminate TLS before the application and configure trusted proxy
    # forwarding below when that proxy speaks HTTP to Uvicorn.
    mcp_remote_allow_loopback_http: bool = True
    mcp_remote_max_sessions: int = Field(default=100, ge=1, le=10_000)

    # Whether this deployment publishes its counters and latency summaries at
    # `/metrics`, to a scraper holding `system:read`. On by default: a
    # deployment nobody can measure is one whose first sign of trouble is a
    # person noticing. Turning it off removes the endpoint rather than serving
    # an empty one, and stops the driver publishing a timing for every command,
    # which is the only part of this whose cost is worth a choice at all.
    metrics_enabled: bool = True

    # How long a shutdown waits for requests already in flight. Long enough for
    # a retrieval to finish, short enough that a deploy is never held up by one
    # call; past it the process exits rather than letting the orchestrator's own
    # kill timeout be what ends it.
    api_shutdown_timeout_seconds: int = Field(default=20, ge=1, le=300)
    # Requests in flight at once, past which the server answers 503 rather than
    # accepting work it cannot finish. Each retrieval holds an embedding model
    # and a database connection, so unbounded acceptance turns a burst into an
    # out-of-memory kill instead of a queue. Kept at or below the connection
    # pool, so the ceiling is the one that is actually reached.
    api_max_concurrent_requests: int = Field(default=50, ge=1, le=1_000)
    # How long an idle keep-alive connection is held.
    api_keep_alive_seconds: int = Field(default=5, ge=1, le=300)
    # The whole of a readiness check, not one probe of it. Measured before this
    # existed: with the search coordinator down, readiness took exactly 180
    # seconds — six index probes, one after another, each waiting out the
    # database operation timeout. A health endpoint that hangs is worse than
    # one that says no, because nothing watching it learns anything while it
    # does. Past this budget the answer is "not ready", which is both true and
    # immediately useful. Kept under the container health check's own timeout,
    # because a readiness answer that arrives after the thing asking for it has
    # given up is not an answer.
    api_readiness_timeout_seconds: float = Field(default=3.0, gt=0.0, le=120.0)

    @model_validator(mode="after")
    def validate_remote_transport(self) -> "Settings":
        """Refuse unsafe proxy/TLS combinations before the API starts."""
        environment = self.app_env.lower()
        if environment == "production" and self.mcp_remote_allow_loopback_http:
            raise ValueError("MCP_REMOTE_ALLOW_LOOPBACK_HTTP must be false in production")
        forwarded = self.api_forwarded_allow_ips
        if self.api_proxy_headers:
            if not forwarded:
                raise ValueError(
                    "API_FORWARDED_ALLOW_IPS is required when API_PROXY_HEADERS is enabled"
                )
            entries = [item.strip() for item in forwarded.split(",") if item.strip()]
            if not entries or "*" in entries:
                raise ValueError("API_FORWARDED_ALLOW_IPS must name explicit proxy IP ranges")
            try:
                for entry in entries:
                    ip_network(entry, strict=False)
            except ValueError as error:
                raise ValueError(
                    "API_FORWARDED_ALLOW_IPS must contain comma-separated IP addresses or CIDRs"
                ) from error
        elif forwarded:
            raise ValueError("API_FORWARDED_ALLOW_IPS requires API_PROXY_HEADERS=true")
        return self

    @field_validator("mongodb_uri")
    @classmethod
    def validate_mongodb_uri(cls, value: str) -> str:
        """Require a MongoDB URI without embedded credentials."""
        parsed = urlsplit(value)
        if parsed.scheme not in {"mongodb", "mongodb+srv"} or not parsed.hostname:
            raise ValueError("MONGODB_URI must be a valid MongoDB URI")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("MONGODB_URI must not contain credentials")
        return value

    @field_validator("mongodb_database")
    @classmethod
    def validate_database_name(cls, value: str) -> str:
        """Reject characters MongoDB does not permit in database names."""
        if any(character in value for character in '/\\. "$*<>:|?'):
            raise ValueError("MONGODB_DATABASE contains an invalid character")
        return value

    @field_validator("embedding_model")
    @classmethod
    def validate_embedding_model(cls, value: str) -> str:
        """Require a usable local path or model repository identifier."""
        if value != value.strip():
            raise ValueError("EMBEDDING_MODEL must not start or end with whitespace")
        if any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError("EMBEDDING_MODEL must not contain whitespace or control characters")
        return value

    @model_validator(mode="after")
    def validate_password_source(self) -> "Settings":
        """Require exactly one application-password source."""
        if (self.mongodb_password is None) == (self.mongodb_password_file is None):
            raise ValueError("configure exactly one of MONGODB_PASSWORD or MONGODB_PASSWORD_FILE")
        return self

    @model_validator(mode="after")
    def validate_skill_chunking(self) -> "Settings":
        """Require coherent section-aware chunk sizing."""
        if not (
            self.skill_chunk_min_tokens
            <= self.skill_chunk_target_tokens
            <= self.skill_chunk_max_tokens
        ):
            raise ValueError(
                "SKILL_CHUNK_MIN_TOKENS must be <= SKILL_CHUNK_TARGET_TOKENS "
                "<= SKILL_CHUNK_MAX_TOKENS"
            )
        if self.skill_chunk_overlap_tokens >= self.skill_chunk_min_tokens:
            raise ValueError("SKILL_CHUNK_OVERLAP_TOKENS must be smaller than the minimum")
        return self

    @model_validator(mode="after")
    def validate_backup_retention(self) -> "Settings":
        """Refuse a retention policy that would delete every backup.

        Caught at startup rather than when a scheduled backup runs, because the
        first time you would otherwise notice is the morning you needed one.
        """
        if self.backup_retain_daily + self.backup_retain_weekly + self.backup_retain_monthly == 0:
            raise ValueError(
                "BACKUP_RETAIN_DAILY, BACKUP_RETAIN_WEEKLY, and BACKUP_RETAIN_MONTHLY "
                "must not all be zero"
            )
        return self

    @model_validator(mode="after")
    def validate_retrieval_budget(self) -> "Settings":
        """Require a default token budget the hard maximum can hold."""
        if self.retrieval_token_budget > self.retrieval_max_token_budget:
            raise ValueError("RETRIEVAL_TOKEN_BUDGET must not exceed RETRIEVAL_MAX_TOKEN_BUDGET")
        if self.research_retrieval_token_budget > self.research_retrieval_max_token_budget:
            raise ValueError(
                "RESEARCH_RETRIEVAL_TOKEN_BUDGET must not exceed "
                "RESEARCH_RETRIEVAL_MAX_TOKEN_BUDGET"
            )
        return self

    @model_validator(mode="after")
    def validate_retrieval_weights(self) -> "Settings":
        """Reject a weighting that could not produce a ranking."""
        self.ranking_weights()
        self.content_ranking_weights()
        return self

    def ranking_weights(self) -> RankingWeights:
        """Build the configured hybrid ranking weights."""
        return RankingWeights(
            vector=self.retrieval_weight_vector,
            lexical=self.retrieval_weight_lexical,
            importance=self.retrieval_weight_importance,
            recency=self.retrieval_weight_recency,
            source_priority=self.retrieval_weight_source_priority,
        )

    def content_ranking_weights(self) -> RankingWeights:
        """Build V2 content-only ranking weights without touching V1 defaults."""
        return RankingWeights(
            vector=self.content_retrieval_weight_vector,
            lexical=self.content_retrieval_weight_lexical,
            importance=self.content_retrieval_weight_importance,
            recency=self.content_retrieval_weight_recency,
            source_priority=self.content_retrieval_weight_source_priority,
        )

    def content_creation_priorities(self) -> dict[str, float]:
        """Return the configurable initial priorities for content creation retrieval."""
        return {
            "project_context": self.content_creation_priority_project_context,
            "memory": self.content_creation_priority_memory,
            "technical_skill": self.content_creation_priority_technical_skill,
            "content_skill": self.content_creation_priority_content_skill,
            "published_content": self.content_creation_priority_published_content,
            "conversation": self.content_creation_priority_conversation,
        }

    @model_validator(mode="after")
    def validate_embedding_batching(self) -> "Settings":
        """Require a batch size the configured per-call text limit can hold."""
        if self.embedding_batch_size > self.embedding_max_batch_texts:
            raise ValueError("EMBEDDING_BATCH_SIZE must not exceed EMBEDDING_MAX_BATCH_TEXTS")
        return self

    def mongodb_password_value(self) -> str:
        """Read the application password without exposing it in settings output."""
        if self.mongodb_password is not None:
            value = self.mongodb_password.get_secret_value()
        else:
            password_file = self.mongodb_password_file
            if password_file is None:  # pragma: no cover - enforced by model validation
                raise ValueError("MongoDB password source is not configured")
            try:
                value = password_file.read_text(encoding="utf-8").rstrip("\r\n")
            except OSError as error:
                raise ValueError("MONGODB_PASSWORD_FILE cannot be read") from error

        if not value:
            raise ValueError("MongoDB password must not be empty")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    return Settings()  # type: ignore[call-arg]


@lru_cache
def get_mcp_settings() -> McpSettings:
    """Return the settings the MCP process needs, without any database ones.

    Loaded separately so the MCP server starts in a container holding no
    MongoDB credentials at all, rather than being handed unused ones it would
    then have to be trusted not to use.
    """
    return McpSettings()
