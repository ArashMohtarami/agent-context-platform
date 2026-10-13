# Agent Context Platform — codebase onboarding

This is a self-contained guide to the code in this repository. It is written to
be read beside the linked source files in an editor. It explains the purpose of
each subsystem, the important functions and classes, normal execution paths,
and the database design.

The platform is an asynchronous Python service that gives agents durable,
project-isolated context. It stores engineering knowledge, canonical Markdown
skills, memories, creator preferences, content records, and historical content
performance observations, research evidence, client identity, sessions, and
audit history in MongoDB. This guide explains the core knowledge and creator
subsystems in depth; use [architecture.md](architecture.md),
[research.md](research.md), and [client-profiles.md](client-profiles.md) for the
research and shared-client workflows. MongoDB
Search and the local embedding provider support lexical, vector, and hybrid
retrieval. FastAPI, MCP, and the CLI are three ways to reach the same service
layer.

## 1. The architectural rule

A request should move in one direction:

~~~text
REST client / MCP client / CLI
             ↓
route or adapter
             ↓
service: business rules, authorization, workflow
             ↓
repository: MongoDB persistence and queries
             ↓
MongoDB collections, indexes, migrations, Search indexes
~~~

Keep these responsibilities separate:

| Layer | Owns | Must not own |
| --- | --- | --- |
| Model/schema | Data shape, enum values, serialization validation | MongoDB queries or authorization policy |
| Route | HTTP parsing, status codes, response encoding | Workflow decisions or raw database access |
| MCP/CLI adapter | Small argument contract, API call, human/agent formatting | Duplicated business rules or MongoDB access |
| Service | Lifecycle, security policy, provenance, orchestration | HTTP details or ad-hoc BSON construction |
| Repository | Collection filters, writes, transactions, pagination | API permissions and user-facing prose |
| Database package | Connection, migrations, validators, indexes, readiness | Feature-specific business workflows |

This organization is more than aesthetic. It makes the same rules apply whether
a caller uses REST, MCP, a command, or a test.

### Terms used throughout the code

| Term | Meaning here |
| --- | --- |
| Project isolation | A principal can only access documents from projects it has been granted. It is a security boundary. |
| Profile isolation | V2 creator/content documents belong to profiles, and profile access is also authorized. |
| Provenance | IDs and references showing the knowledge sources behind a result or content record. |
| Canonical skill | A reviewed Markdown file in Git. MongoDB stores its imported search representation. |
| Revision | A number incremented on change for optimistic concurrency control. |
| Current representation | The current meaningful version of an idea, brief, or content item, which may be embedded for search. |
| mongot | MongoDB Search's sidecar search process. The application communicates with MongoDB, not mongot directly. |
| Retrieved text | Data to inspect and cite, never instructions to execute automatically. |

## 2. Program entry points and startup

| File | Role |
| --- | --- |
| [app/main.py](../app/main.py) | FastAPI application factory, lifespan, router registration, startup subsystem assembly. |
| [app/run.py](../app/run.py) | Uvicorn configuration and normal HTTP server entry point. |
| [app/run_mcp.py](../app/run_mcp.py) | MCP process entry point. |
| [app/core/config.py](../app/core/config.py) | Typed application settings loaded from the environment. |
| [app/core/logging.py](../app/core/logging.py) | Logging setup and structured logging conventions. |

### app/main.py

The central function is create_app(settings=None). It creates FastAPI rather
than relying on an irreversible global singleton, which allows tests to create
an application with test settings.

Its lifespan follows this sequence:

1. Configure logs and record build information.
2. Create and connect the asynchronous MongoDB client.
3. Check database, replica-set, and search availability.
4. Run migrations and ensure standard MongoDB indexes.
5. Reconcile lexical MongoDB Search indexes.
6. Build the local embedding service and reconcile vector indexes.
7. Store shared settings, database, embeddings, authentication, and audit
   services on app.state for dependency providers.
8. Close database resources on shutdown.

The helper functions start_lexical_search, start_embeddings, and
start_authentication deliberately isolate startup concerns. Search/embedding
unavailability is observable through health/logging rather than turning a
collection-creation mistake into a hidden failure.

### app/run.py

options(settings) configures host, port, logging, graceful limits, and worker
behavior. The service is deliberately conservative about workers because the
local embedding model consumes process memory. A worker count is an operational
choice tied to that model, not just a throughput setting.

### app/core/config.py

Settings are the one acceptable boundary for environment-derived values. A
service receives a typed setting or a constructed dependency; it should not
call os.environ itself.

When adding configuration:

1. Add a typed, validated setting field.
2. Give it a documented default only if that default is safe.
3. Pass it to the construction boundary needing it.
4. Test it through a settings fixture.
5. Avoid creating an undocumented environment lookup inside a route/service.

## 3. HTTP API, dependencies, and middleware

The HTTP layer is in [app/api](../app/api). It turns a network request into a
typed service call and turns the typed result back into a network response.

| File | Responsibility |
| --- | --- |
| [app/api/dependencies.py](../app/api/dependencies.py) | Creates service/repository dependencies from app.state. |
| [app/api/security.py](../app/api/security.py) | Extracts HTTP credentials and enforces scopes. |
| [app/api/middleware.py](../app/api/middleware.py) | Request IDs, timing, and exception-to-HTTP translation. |
| [app/api/routes/health.py](../app/api/routes/health.py) | Liveness/readiness and component health routes. |
| [app/api/routes/metrics.py](../app/api/routes/metrics.py) | Operational metrics route. |
| [app/api/routes/projects.py](../app/api/routes/projects.py) | Project API operations. |
| [app/api/routes/context.py](../app/api/routes/context.py) | Project-context API operations. |
| [app/api/routes/memory.py](../app/api/routes/memory.py) | Memory API operations. |
| [app/api/routes/skills.py](../app/api/routes/skills.py) | Skill import/list/get operations. |
| [app/api/routes/search.py](../app/api/routes/search.py) | V1 unified search/retrieval endpoints. |
| [app/api/routes/creator.py](../app/api/routes/creator.py) | Creator profile and content-pillar endpoints. |
| [app/api/routes/content.py](../app/api/routes/content.py) | V2 content, retrieval, discovery, novelty, repurpose, export, publication-safety, publishing, and metrics endpoints. |

### Reading one route handler

A typical handler has this shape:

~~~text
Pydantic request body / path / query parameters
             ↓
authenticated Principal dependency
             ↓
specific service dependency
             ↓
service method
             ↓
Pydantic response model and HTTP status
~~~

Routes answer protocol questions: which path, which status code, how to decode a
cursor, and how to serialize JSON. Services answer domain questions: whether a
profile is accessible, whether a content transition is legal, and whether a body
change must create a version.

[app/api/dependencies.py](../app/api/dependencies.py) is the request-time
composition root. It retrieves shared infrastructure from app.state and wires
repositories into services. This is why route functions should not instantiate
a Mongo client, an embedding model, or a repository directly.

[app/api/security.py](../app/api/security.py) converts a valid API key into a
Principal. It verifies both a coarse permission, such as content:read, and
resource grants such as permitted profile/project IDs.

[app/api/middleware.py](../app/api/middleware.py) attaches request context and
maps expected domain errors to stable HTTP responses. It should not hide
unexpected programming errors as successful results.

## 4. Models and schemas

Models in [app/models](../app/models) are Pydantic data contracts. They validate
input and document shapes, but they do not save themselves. Keeping models
separate from repositories avoids accidental database writes from validation
code.

| File | Main purpose |
| --- | --- |
| [app/models/common.py](../app/models/common.py) | UTC helpers, deterministic IDs, shared document fields, tags, cursor encoding/decoding, Page. |
| [app/models/project.py](../app/models/project.py) | Project documents and project request/response schemas. |
| [app/models/project_context.py](../app/models/project_context.py) | Project-context documents, types, source/status/importance schemas. |
| [app/models/memory.py](../app/models/memory.py) | Memory scope/type/status and memory API contracts. |
| [app/models/skill.py](../app/models/skill.py) | Skill identity, trust level, version, status, and metadata. |
| [app/models/skill_chunk.py](../app/models/skill_chunk.py) | Searchable chunk content, section, token, and embedding fields. |
| [app/models/embedding.py](../app/models/embedding.py) | Stored vector, model ID, dimensions, and generation timestamp. |
| [app/models/conversation.py](../app/models/conversation.py) | Conversation-turn storage model. |
| [app/models/retrieval_log.py](../app/models/retrieval_log.py) | Retrieval query/result audit data. |
| [app/models/api_key.py](../app/models/api_key.py) | API-key metadata, grants, status, expiration contracts. |
| [app/models/audit_log.py](../app/models/audit_log.py) | Immutable audit event contracts. |
| [app/models/content.py](../app/models/content.py) | V2 creator/content enums, documents, publication receipt fields, generic metric observations, pagination, and search types. |

### Common document behavior

MongoDocumentModel in [app/models/common.py](../app/models/common.py) provides
the common persistence fields:

~~~text
_id          MongoDB primary key
created_at   UTC creation timestamp
updated_at   UTC last-change timestamp
revision     optimistic-concurrency number
~~~

TaggedDocument adds tag validation. Page is the common cursor-pagination result:
items plus a next cursor. Cursor pagination is preferable to deep offset/skip
pagination because it is stable when later documents are inserted and avoids
an ever-growing skip cost.

The deterministic ObjectId helpers create stable application identities where an
import or create operation needs idempotency. These public IDs complement
MongoDB's internal _id; they do not replace it.

### Content lifecycle and platform validation

ContentStatus in [app/models/content.py](../app/models/content.py) admits only:

~~~text
idea → selected → researching → brief_ready → drafting → review
     → approved → published → archived
~~~

The enum catches invalid vocabulary at the API/document boundary. The service,
not the enum, decides whether an arrow is allowed. For example, published to
drafting requires an explicit revision workflow; it is not a normal PATCH
transition.

ContentPlatform currently admits linkedin, medium, and github. This lets invalid
platform values fail before they reach persistence or platform formatting.
Adding youtube, x, reddit, newsletter, or blog requires a deliberate enum,
adapter, test, and possibly skill change.

### Generic metric validation and identity

`ContentMetricDocument` in [app/models/content.py](../app/models/content.py) is
the persisted model for a single observation. Its deterministic MongoDB ID is
derived from the profile ID and `metric_id`, so retrying the same logical metric
insert produces a conflict instead of a duplicate. It has the normal creation
and revision fields, but metric observations are append-only: the API exposes
creation and history reads, not an edit operation.

The `metrics` map is deliberately generic rather than a fixed table of social
network columns. Model validation requires a non-empty map whose keys are safe
identifiers and whose values are finite, non-negative numbers. `source` tells a
reviewer where the observation came from, while `provider_metadata` is a bounded
structured place for provider-specific descriptive data. Neither field grants
the server permission to contact that provider.

[app/schemas/content.py](../app/schemas/content.py) keeps the HTTP contract
separate through `ContentMetricCreateRequest`, `ContentMetricResponse`, and
`ContentMetricListResponse`. This is why the route can accept a create request
without exposing MongoDB `_id`, and can return a cursor page without letting a
caller submit persistence-only fields.

### Public request and response schemas

The API-specific Pydantic contracts are intentionally separate from MongoDB
documents. A request model controls what a caller may submit; a response model
controls what the API exposes. The service maps between request schemas and
domain documents.

| File | Contract family |
| --- | --- |
| [app/schemas/project.py](../app/schemas/project.py) | Project create/update/list/detail request and response contracts. |
| [app/schemas/context.py](../app/schemas/context.py) | Project-context create/update/search/list contracts. |
| [app/schemas/memory.py](../app/schemas/memory.py) | Memory create/update/search/list contracts. |
| [app/schemas/skill.py](../app/schemas/skill.py) | Skill front matter, import/list/detail/search contracts. |
| [app/schemas/search.py](../app/schemas/search.py) | Unified engineering search request/result contracts. |
| [app/schemas/creator.py](../app/schemas/creator.py) | Creator profile and content-pillar create/update/list/detail contracts. |
| [app/schemas/content.py](../app/schemas/content.py) | Content lifecycle, versions, search, discovery, novelty, repurpose, export, publication, and metrics contracts. |

Request schemas reject forbidden lifecycle/platform values and malformed
references before a service is called. Document models additionally protect the
data that can actually be persisted. Do not expose a database document directly
just because it happens to serialize.

## 5. Database infrastructure

The files in [app/db](../app/db) own the reusable mechanics of persistence. They
do not contain creator or content workflow policy.

| File | Responsibility |
| --- | --- |
| [app/db/client.py](../app/db/client.py) | Async PyMongo configuration, connection lifecycle, readiness checks, timeouts, pool and replica-set settings. |
| [app/db/client_types.py](../app/db/client_types.py) | Mongo client/session protocols used by production and tests. |
| [app/db/collections.py](../app/db/collections.py) | Collection names and idempotent validated collection creation. |
| [app/db/schema.py](../app/db/schema.py) | Strict MongoDB JSON Schema validators. |
| [app/db/indexes.py](../app/db/indexes.py) | Normal B-tree, compound, and unique index definitions/reconciliation. |
| [app/db/initializer.py](../app/db/initializer.py) | Startup order: migrations, then standard indexes. |
| [app/db/health.py](../app/db/health.py) | MongoDB and search component probes. |
| [app/db/errors.py](../app/db/errors.py) | Database-specific exception types. |
| [app/db/text_indexes.py](../app/db/text_indexes.py) | Common lexical Search-index construction helpers. |
| [app/db/vector_indexes.py](../app/db/vector_indexes.py) | Common vector Search-index construction helpers. |
| [app/db/context_indexes.py](../app/db/context_indexes.py) | Context lexical/vector search definition. |
| [app/db/memory_indexes.py](../app/db/memory_indexes.py) | Memory lexical/vector search definition. |
| [app/db/content_indexes.py](../app/db/content_indexes.py) | Content lexical/vector definitions and equivalent prefilter mapping. |
| [app/db/search_indexes.py](../app/db/search_indexes.py) | SearchIndexState plus describe/create/update/reconcile behavior. |
| [app/db/migrations/base.py](../app/db/migrations/base.py) | Migration protocol and migration metadata. |
| [app/db/migrations/runner.py](../app/db/migrations/runner.py) | Exactly-once, race-tolerant migration execution. |
| [app/db/migrations/v001_initial_schema.py](../app/db/migrations/v001_initial_schema.py) | Original V1 schema. |
| [app/db/migrations/v002_skill_chunk_embeddings.py](../app/db/migrations/v002_skill_chunk_embeddings.py) | Skill-chunk embedding evolution. |
| [app/db/migrations/v003_project_context_embeddings.py](../app/db/migrations/v003_project_context_embeddings.py) | Project-context embedding evolution. |
| [app/db/migrations/v004_memory_records.py](../app/db/migrations/v004_memory_records.py) | Memory records, embeddings, and supersession evolution. |
| [app/db/migrations/v005_api_keys_and_audit.py](../app/db/migrations/v005_api_keys_and_audit.py) | API-key and audit persistence. |
| [app/db/migrations/v006_creator_content_schema.py](../app/db/migrations/v006_creator_content_schema.py) | Creator/content collection introduction. |
| [app/db/migrations/v007_creator_profile_access.py](../app/db/migrations/v007_creator_profile_access.py) | Profile grants on API keys. |
| [app/db/migrations/v008_content_audit_action.py](../app/db/migrations/v008_content_audit_action.py) | Content audit action evolution. |
| [app/db/migrations/v009_content_retrieval_embeddings.py](../app/db/migrations/v009_content_retrieval_embeddings.py) | Searchable current content representations. |
| [app/db/migrations/v010_content_approval_audit_action.py](../app/db/migrations/v010_content_approval_audit_action.py) | Adds the auditable content-approval action without changing V1 documents. |
| [app/db/migrations/v011_content_publication_receipt.py](../app/db/migrations/v011_content_publication_receipt.py) | Backfills publication-provider, external-ID, and idempotency-receipt fields for content items. |
| [app/db/migrations/v012_content_metric_source_metadata.py](../app/db/migrations/v012_content_metric_source_metadata.py) | Backfills generic metric source and provider metadata, then restores the strict validator. |

### Startup order and idempotency

initialize_database in [app/db/initializer.py](../app/db/initializer.py) retains
this order:

~~~text
run migrations → ensure normal MongoDB indexes → reconcile Search indexes
~~~

A migration changes durable historical state. The migration runner records
completed versions in _schema_migrations using a unique version identity. It is
safe to run at every startup and handles concurrent startup attempts. Never
alter the intended effect of a migration that might already have run in an
environment. Add the next migration instead.

Collection creation in [app/db/collections.py](../app/db/collections.py) is
idempotent. Existing collections are expected during a repeated startup.
Validators in [app/db/schema.py](../app/db/schema.py) are strict: unexpected
stored fields are rejected with additionalProperties false. This catches schema
drift early, but it means a durable field addition must update model, validator,
migration, repository, and tests together.

### Standard index vs Search index

Normal indexes in [app/db/indexes.py](../app/db/indexes.py) serve uniqueness,
filtering, sorting, cursor pagination, and ordinary lookup. An example is the
unique compound identity for a content version:
profile_id plus content_id plus version.

MongoDB Search indexes are derived search resources:

| Index kind | Good at |
| --- | --- |
| Text/lexical | Exact terms such as Outbox, Keycloak, identifiers, and names |
| Vector | Semantically related phrasing and concepts |
| Hybrid | Combining both candidate sets before policy ranking |

The application uses MongoDB's Search-index management API through
[app/db/search_indexes.py](../app/db/search_indexes.py). MongoDB coordinates
mongot. The application does not connect directly to mongot.

Content index code is careful to translate profile, platform, project, status,
pillar, and tag filters into equivalent lexical and vector prefilters. A
security filter must behave identically in every retrieval mode.

## 6. Repositories

A repository is the only code that turns domain values into collection filters,
BSON updates, aggregation pipelines, sessions, and cursor bounds. Repository
methods accept/return typed data; they do not return loose database documents to
routes.

| File | Persistence responsibility |
| --- | --- |
| [app/repositories/base.py](../app/repositories/base.py) | MongoRepository shared CRUD, pagination, search, errors, timestamps, and revision-aware updates. |
| [app/repositories/project_repository.py](../app/repositories/project_repository.py) | Project identity and persistence. |
| [app/repositories/context_repository.py](../app/repositories/context_repository.py) | Project context and context search candidates. |
| [app/repositories/memory_repository.py](../app/repositories/memory_repository.py) | Memory scope, recency, search, and supersession persistence. |
| [app/repositories/skill_repository.py](../app/repositories/skill_repository.py) | Skill metadata and active-version queries. |
| [app/repositories/skill_chunk_repository.py](../app/repositories/skill_chunk_repository.py) | Chunk writes and lexical/vector candidate queries. |
| [app/repositories/conversation_repository.py](../app/repositories/conversation_repository.py) | Conversation turns. |
| [app/repositories/retrieval_repository.py](../app/repositories/retrieval_repository.py) | V1 retrieval read models. |
| [app/repositories/vectors.py](../app/repositories/vectors.py) | Shared vector data/query support. |
| [app/repositories/api_key_repository.py](../app/repositories/api_key_repository.py) | Active/revoked/expired key persistence. |
| [app/repositories/audit_repository.py](../app/repositories/audit_repository.py) | Append-only audit events. |
| [app/repositories/content_repository.py](../app/repositories/content_repository.py) | Creator profile, pillar, idea, brief, item, version, link, metric, campaign, and content search persistence. |
| [app/repositories/backup_repository.py](../app/repositories/backup_repository.py) | Persistence-side backup/restore integration. |
| [app/repositories/errors.py](../app/repositories/errors.py) | Not-found, conflict, validation, and concurrency persistence errors. |

### The base repository methods

MongoRepository in [app/repositories/base.py](../app/repositories/base.py)
centralizes the repeated safe patterns:

| Method | What it is for |
| --- | --- |
| _create | Serialize and insert a validated model; translate duplicate identity to conflict. |
| _get | Fetch exactly one safe filter and translate missing documents. |
| _page | Apply stable ordering and cursor bounds, then return Page. |
| _recent | Bounded recent-document query used by discovery/retrieval. |
| _scored_search | Run a score-producing search query/pipeline and return typed candidates. |
| _search | Shared filter/search dispatch. |
| _update | Revision-aware update, timestamp update, revision increment. |
| _update_fields | Safe partial field update helper. |
| _delete | Explicit deletion only for resources whose lifecycle allows it. |

Optimistic concurrency is based on a filter like this:

~~~text
update_one(
  { _id: id, revision: expected_revision },
  { $set: changed_fields, $inc: { revision: 1 } }
)
~~~

If the matched count is zero, a caller used an obsolete revision or no longer
has a valid target. The code reports a conflict instead of silently erasing a
newer edit. No long lock is held while a person or agent is deciding what to
write.

### Content versions use a MongoDB transaction

MongoContentItemRepository in
[app/repositories/content_repository.py](../app/repositories/content_repository.py) uses a MongoDB
session with a transaction for initial content creation and version append. It
changes these two representations atomically:

~~~text
content_items current title/body/status/current_version
                         +
content_versions immutable snapshot insert
~~~

If either write fails, neither becomes visible. This prevents a current content
item from referring to a missing version and prevents orphaned version records.

## 7. Services and business rules

Services are the center of the application. They coordinate repositories,
enforce lifecycle/authorization policy, create embeddings, preserve provenance,
and record audit events.

| File | Domain responsibility |
| --- | --- |
| [app/services/project_service.py](../app/services/project_service.py) | Project creation, reading, updates, and access policy. |
| [app/services/context_service.py](../app/services/context_service.py) | Project-context workflow, validation, persistence, embeddings. |
| [app/services/memory_service.py](../app/services/memory_service.py) | Memory lifecycle, scopes, confidence/importance, supersession. |
| [app/services/skill_service.py](../app/services/skill_service.py) | Skill import/version lifecycle and canonical-source policy. |
| [app/services/embedding_service.py](../app/services/embedding_service.py) | Local embedding batching, model metadata, provider isolation. |
| [app/services/retrieval_service.py](../app/services/retrieval_service.py) | V1 candidate gathering, policy ranking, token budgets, provenance. |
| [app/services/search_service.py](../app/services/search_service.py) | Unified V1 search facade and retrieval logging. |
| [app/services/retrieval_log_service.py](../app/services/retrieval_log_service.py) | Retrieval log management. |
| [app/services/auth_service.py](../app/services/auth_service.py) | API-key principal loading and scopes/project/profile authorization. |
| [app/services/api_key_service.py](../app/services/api_key_service.py) | Key creation, rotation, revocation, expiration handling. |
| [app/services/audit_service.py](../app/services/audit_service.py) | Security/operational audit-event recording. |
| [app/services/backup_service.py](../app/services/backup_service.py) | Backup/restore workflow, manifests, retention, safety checks. |
| [app/services/creator_service.py](../app/services/creator_service.py) | Creator profiles and content pillars with profile authorization. |
| [app/services/content_service.py](../app/services/content_service.py) | Ideas, briefs, content lifecycle, versions, rollback, provenance, embeddings. |
| [app/services/content_retrieval_service.py](../app/services/content_retrieval_service.py) | Content lexical/vector/hybrid retrieval profiles, filters, budgets. |
| [app/services/content_discovery_service.py](../app/services/content_discovery_service.py) | Structured evidence-first idea discovery and brief preparation. |
| [app/services/content_novelty_service.py](../app/services/content_novelty_service.py) | Similar-content overlap and non-authoritative novelty classification. |
| [app/services/content_repurpose_service.py](../app/services/content_repurpose_service.py) | Platform-aware repurpose plans with provenance. |
| [app/services/content_export_service.py](../app/services/content_export_service.py) | Platform validation/format/export; never external publication. |
| [app/services/content_publication_service.py](../app/services/content_publication_service.py) | Deterministic safety review and auditable review-to-approved transition. |
| [app/services/content_publishing_service.py](../app/services/content_publishing_service.py) | Explicit, safety-gated, idempotent provider delivery and durable publication receipt. |
| [app/services/content_metrics_service.py](../app/services/content_metrics_service.py) | Explicit generic metric snapshots and profile-isolated time-series retrieval; no analytics client or strategy mutation. |

### V1 context, memory, and skills

ContextService handles explicitly supplied project facts, decisions, documents,
and technical context. MemoryService handles remembered information and checks
scope/project/client ownership. Shared scopes are `global` and `project`;
operational scopes are `agent` and `project_agent`. Agent identity is derived
from authentication, and project scopes require an explicit project. Supersession links an
old memory to newer knowledge instead of deleting important history.

SkillService delegates Markdown parsing/chunking to app/skills, records imported
versions/chunks, and selects active versions. Canonical skill files remain in
Git. MongoDB stores the indexed copy used by retrieval, not a second authoring
source.

### Retrieval is evidence assembly

RetrievalService gathers candidates from authorized project context, memory,
skills, and conversation sources. It then uses pure deterministic functions in
app/retrieval to merge, filter, score, de-duplicate, and trim them to a token
budget. It returns source IDs and metadata so callers can explain where an
answer came from.

A retrieval result is not an instruction channel. Agents must treat content in
a returned skill, memory, or conversation as evidence/data.

### V2 creator and content guarantees

CreatorService supports multiple active/inactive profiles. A creator profile is
data describing audiences, voice, terminology, formatting, language, interests,
and objectives. It is not hidden system instructions.

ContentService guarantees:

- Ideas, briefs, items, and versions retain source references.
- Body changes create/record immutable versions rather than overwriting history.
- Rollback restores a previous representation through service logic.
- Invalid transitions are rejected.
- Published content cannot casually move back to drafting.
- Archived records can remain searchable when the request allows them.
- Current meaningful representations, not every historical version, are embedded.

Discovery, novelty, and repurposing services return structured material for a
human or agent to judge and write. They do not add an LLM dependency to the
server and do not fabricate project experiences.

Publication review is an advisory safety layer, not a confidentiality guarantee.
It returns redacted, explainable indicators and leaves the source content
unchanged. Approval rescans the current revision, so a stale review cannot
approve a changed document. Publishing is a separate explicit action on an
already approved item; a provider receives stored content, never arbitrary
prose submitted to a publishing endpoint.

ContentMetricsService stores descriptive observations only. It does not fetch
third-party analytics, infer a content strategy, rewrite creator preferences,
change pillars, or optimize toward engagement. A future provider integration
may submit the same validated observation shape, but it remains optional.

## 8. Pure retrieval and ranking modules

The [app/retrieval](../app/retrieval) package avoids FastAPI/MongoDB dependencies
so its behavior can be tested as deterministic functions.

| File | What to learn from it |
| --- | --- |
| [app/retrieval/types.py](../app/retrieval/types.py) | Candidate, match, source, score, and result structures. |
| [app/retrieval/query.py](../app/retrieval/query.py) | Query normalization and query validation. |
| [app/retrieval/filters.py](../app/retrieval/filters.py) | Shared filtering rules. |
| [app/retrieval/ranking.py](../app/retrieval/ranking.py) | Source priority, recency, configurable weights, final scoring. |
| [app/retrieval/hybrid.py](../app/retrieval/hybrid.py) | Merge/deduplicate lexical and vector candidates. |
| [app/retrieval/context_builder.py](../app/retrieval/context_builder.py) | Token-budget-aware selection and final context assembly. |

Content retrieval adds named profiles in
[app/services/content_retrieval_service.py](../app/services/content_retrieval_service.py) without
changing engineering ranking globally:

| Profile | Purpose |
| --- | --- |
| content_creation | Combine project context, memory, technical/content skills, published history, and conversation with configurable priorities. |
| content_history | Find authored content and its history. |
| content_ideation | Find ideas, pillars, recent evidence, and unpublished overlap. |
| content_repurposing | Find source content plus relevant historical content and platform guidance. |

The content_creation starting source priorities are designed to prefer private,
relevant engineering evidence over generic text:

~~~text
project_context   1.00
memory            0.95
technical_skill   0.85
content_skill     0.85
published_content 0.70
conversation      0.40
~~~

Those values are policy configuration, not globally forced V1 behavior.

## 9. Embeddings and MongoDB Search

| File | Purpose |
| --- | --- |
| [app/embeddings/base.py](../app/embeddings/base.py) | Embedding provider protocol. |
| [app/embeddings/types.py](../app/embeddings/types.py) | Input/output types for embeddings. |
| [app/embeddings/config.py](../app/embeddings/config.py) | Provider configuration contract. |
| [app/embeddings/local.py](../app/embeddings/local.py) | Local model implementation/loading/inference. |
| [app/embeddings/errors.py](../app/embeddings/errors.py) | Explicit provider failures. |
| [app/search/types.py](../app/search/types.py) | Search result/diagnostic data types. |
| [app/search/logging.py](../app/search/logging.py) | Structured search logging. |

Services depend on the small EmbeddingProvider protocol, not directly on a model
library. Persisted vectors carry the model identifier, dimensions, and
generation time. That metadata makes it possible to notice model incompatibility
rather than mixing unrelated vector spaces silently.

Lexical search is valuable for exact technical vocabulary. Vector search is
valuable when a query and source express the same concept in different words.
Hybrid retrieval gathers both candidates, applies authorization and filters, and
then uses explicit source/recency policy ranking. It is not merely an average of
two scores from different systems.

## 10. Canonical skill import pipeline

| File | Purpose |
| --- | --- |
| [app/skills/parser.py](../app/skills/parser.py) | Reads front matter and Markdown into typed skill metadata/content. |
| [app/skills/chunker.py](../app/skills/chunker.py) | Splits content into section-aware retrieval chunks. |
| [app/skills/importer.py](../app/skills/importer.py) | Coordinates parse, identity/hash, persistence, chunk creation, embeddings. |
| [app/skills/types.py](../app/skills/types.py) | Typed import/chunk contracts. |
| [app/skills/errors.py](../app/skills/errors.py) | Actionable parser/import error types. |

Content-related canonical skills are Markdown in Git:

| File | Covers |
| --- | --- |
| [skills/content/linkedin-writing.md](../skills/content/linkedin-writing.md) | Strong technical LinkedIn posts. |
| [skills/content/medium-writing.md](../skills/content/medium-writing.md) | Long-form Medium structure/depth. |
| [skills/content/github-writing.md](../skills/content/github-writing.md) | README, ADR, architecture, setup, and troubleshooting documentation. |
| [skills/content/technical-storytelling.md](../skills/content/technical-storytelling.md) | Setup, tension, learning, resolution, lesson. |
| [skills/content/hooks.md](../skills/content/hooks.md) | Opening and hook techniques. |
| [skills/content/editing.md](../skills/content/editing.md) | Editing/revision guidance. |
| [skills/content/technical-explanation.md](../skills/content/technical-explanation.md) | Clear first-principles explanations. |
| [skills/content/content-repurposing.md](../skills/content/content-repurposing.md) | Platform-specific repurposing constraints. |
| [skills/content/content-review.md](../skills/content/content-review.md) | Content-review checklist. |
| [skills/content/content-ideation.md](../skills/content/content-ideation.md) | Evidence-driven ideation guidance. |

The parser validates metadata, the chunker preserves useful sections, and the
importer uses identity/content hash/version semantics so reimporting unchanged
skills does not create duplicate versions. Trusted is a review classification for
canonical Git content; it never means all retrieved text is safe to execute.

## 11. Platform abstraction and exporting

The content package formats already-written content. It does not create core
prose and it does not publish externally.

| File | Responsibility |
| --- | --- |
| [app/content/errors.py](../app/content/errors.py) | Content-platform domain errors. |
| [app/content/discovery.py](../app/content/discovery.py) | Structured discovery input/result types. |
| [app/content/novelty.py](../app/content/novelty.py) | Overlap and novelty result types. |
| [app/content/repurposing.py](../app/content/repurposing.py) | Repurpose-plan input/result types. |
| [app/content/platforms/base.py](../app/content/platforms/base.py) | ContentPlatform protocol: validate, format, export. |
| [app/content/platforms/common.py](../app/content/platforms/common.py) | Shared text and Markdown helpers. |
| [app/content/platforms/linkedin.py](../app/content/platforms/linkedin.py) | LinkedIn spacing, plaintext/export validation. |
| [app/content/platforms/medium.py](../app/content/platforms/medium.py) | Medium long-form Markdown/article representation. |
| [app/content/platforms/github.py](../app/content/platforms/github.py) | GitHub Markdown, code fences, and file-path suggestions. |
| [app/content/platforms/__init__.py](../app/content/platforms/__init__.py) | Platform registry/factory. |

ContentPlatform is the extension seam:

~~~text
validate(content)     reject invalid platform-specific material
format(content)       create the platform representation
export(content, mode) return stdout/file/Markdown representation
~~~

To add a new platform, extend the enum, implement this protocol, register it,
test format/Unicode/length/code constraints, and add a canonical skill if
writing guidance is needed. Keep the adapter deterministic and side-effect free.

### Publication safety, explicit receipts, and metrics

These are deliberately separate operations. A safety review is read-only;
approval is a revision-aware lifecycle transition; publishing is an explicit
publication-receipt request with the shipped manual adapter; and a metric snapshot is descriptive data captured after the
fact. None of them generates prose or automatically changes a creator profile.

| File | Classes and methods to study | What the code teaches |
| --- | --- | --- |
| [app/content/publication.py](../app/content/publication.py) | `PublicationSafetyPolicy`, `PublicationReview`, `PublicationFinding`, `review_content_for_publication()` | A deterministic pattern scanner can return a finding type, severity, field, and line/column without returning or logging the matched secret. It is advisory, bounded, and policy-configurable. |
| [app/services/content_publication_service.py](../app/services/content_publication_service.py) | `ContentPublicationService.review_content_for_publication()`, `review_item_for_publication()`, `approve_content()` | Approval requires `review` status and the expected current revision, performs a fresh scan, blocks configured high-severity findings, and persists only the legal `review → approved` transition. |
| [app/content/publishers/base.py](../app/content/publishers/base.py) | `Publisher` protocol, `PublisherRegistry`, `PublicationReceipt` | Provider adapters have one narrow contract: validate a connection, publish a stored item with an idempotency key, and return redacted status. The registry contains only explicitly configured providers. |
| [app/content/publishers/manual.py](../app/content/publishers/manual.py) | `ManualPublisher` | The safe default integration pattern: record a human-confirmed publication locally without credentials, network traffic, or an implicit external post. |
| [app/services/content_publishing_service.py](../app/services/content_publishing_service.py) | `ContentPublishingService.publish_content()` | Publishing rereads and rescans the current item, requires `approved`, generates or accepts an idempotency key, and persists the receipt with a revision-aware update. A retry with the same durable receipt is safe. |
| [app/services/content_metrics_service.py](../app/services/content_metrics_service.py) | `ContentMetricsService.create_metric()`, `list_metrics()` | Metric capture checks profile and content ownership before it creates an immutable observation. Retrieval validates time bounds and returns one content item plus a newest-first page. |
| [app/repositories/content_repository.py](../app/repositories/content_repository.py) | `MongoContentMetricRepository.list_for_content()` | Time-series pagination uses the tuple `(captured_at, _id)` as its descending cursor. That avoids skipped or duplicated snapshots when equal capture times occur. |
| [app/db/migrations/v012_content_metric_source_metadata.py](../app/db/migrations/v012_content_metric_source_metadata.py) | `ContentMetricSourceMigration.up()` | A strict-schema migration temporarily permits legacy documents, backfills safe defaults, then reapplies the final validator. Its `down()` refuses to delete durable provenance. |

#### The approval and publication sequence

~~~text
draft → review ──POST publication-review──> redacted findings (no mutation)
              └─POST approve, expected_revision──> approved + audit event
approved ──POST export──> formatted representation only
approved ──POST publish, expected_revision + provider──> provider receipt
                                                    ↓
                                         published + durable receipt
~~~

A review response can contain project IDs and source references so an authorized
reviewer can make a decision, but it does not echo a detected secret. The
scanner checks current title/body, public URL data, and configured sensitive
project/provenance policy. It can find likely API keys, credentials, tokens,
private URLs and hosts, internal network addresses, customer identifiers, PII,
and sensitive repository/architecture references. Detection is intentionally
not a claim that a document is safe to disclose.

`POST /api/v2/content/{id}/publication-review` requires `content:read`;
`POST /api/v2/content/{id}/approve` requires `content:write`;
`POST /api/v2/content/{id}/publish` requires the separate `content:publish`
scope. All three first
check profile access and every project referenced by the current item or its
provenance. The publish endpoint receives an ID, provider, revision, and
idempotency key—not a free-form body—so it cannot be used as a broad arbitrary
text publisher.

#### Generic content-metric time series

A `ContentMetricDocument` is a deterministic, profile-owned observation:

~~~text
metric_id          caller-supplied deterministic identity within a profile
content_id         existing content item that owns this observation
platform           linkedin | medium | github (extensible enum)
captured_at        when the numbers were observed, not when they were written
metrics            non-empty map of identifier keys to finite, non-negative numbers
source             provenance label; migration v012 defaults legacy records to manual
provider_metadata  provider-specific descriptive object; no provider call occurs
~~~

The public API is intentionally small:

- `POST /api/v2/content/{id}/metrics` requires `content:write` and stores one
  explicit snapshot.
- `GET /api/v2/content/{id}/metrics` requires `content:read` and accepts an
  optional platform, capture-time range, limit, and cursor.
- MCP `get_content_metrics` calls that GET endpoint through the normal MCP HTTP
  client; it never reads MongoDB directly.

Metric keys are generic, so `views`, `impressions`, `likes`, `comments`,
`shares`, `clicks`, `bookmarks`, and `followers_gained` fit without creating a
new schema column. A numeric value must be finite and non-negative; booleans,
negative counts, `NaN`, and `Infinity` are rejected. The service does not add an
analytics provider, collect remote data, produce automatic strategy insights,
or alter creator voice, pillars, or content. For the database mechanics behind
this design, read [the MongoDB tutorial](mongodb-tutorial.md) alongside the
source files above.

## 12. Security and audit

| File | Purpose |
| --- | --- |
| [app/security/keys.py](../app/security/keys.py) | Secure key generation/parsing/hashing/verification helpers. |
| [app/security/principal.py](../app/security/principal.py) | Principal scope, project, and profile grant methods. |
| [app/security/scopes.py](../app/security/scopes.py) | Canonical scope vocabulary and validation. |
| [app/services/auth_service.py](../app/services/auth_service.py) | Loads active API keys and enforces permissions. |
| [app/services/api_key_service.py](../app/services/api_key_service.py) | Key lifecycle. |
| [app/services/audit_service.py](../app/services/audit_service.py) | Audit-event service. |

Authentication answers who made a request. Authorization answers whether that
principal may perform the operation on the target resource. Both are necessary.

V2 uses scopes including creator:read, creator:write, content:read,
content:write, and the separate content:publish grant. A scope alone is insufficient: a service also verifies the
profile/project grant before it creates or returns data. Search filters must be
pre-authorized, not trusted as client-provided security constraints. The same
content scopes govern publication review, approval, explicit publishing, and
metric history: the route verifies the profile and every project in the current
content item or source reference before it reads or changes a record.

Plaintext API keys are only shown during creation. The database stores hashes
and non-secret metadata. Audit records identify key IDs/names and request
outcomes, never secret key material.

## 13. MCP server

MCP is a thin typed HTTP client over the REST service. It deliberately has no
MongoDB repositories.

| File | Responsibility |
| --- | --- |
| [app/mcp/server.py](../app/mcp/server.py) | MCP server assembly and tool registration. |
| [app/mcp/client.py](../app/mcp/client.py) | HTTP calls from MCP to the platform API. |
| [app/mcp/auth.py](../app/mcp/auth.py) | MCP credential/authorization safeguards. |
| [app/mcp/schemas.py](../app/mcp/schemas.py) | MCP tool input/output contracts. |
| [app/mcp/formatting.py](../app/mcp/formatting.py) | Agent-readable output formatting. |
| [app/mcp/errors.py](../app/mcp/errors.py) | Stable MCP error translation. |
| [app/mcp/tools/base.py](../app/mcp/tools/base.py) | Shared tool declaration/call helpers. |
| [app/mcp/tools/projects.py](../app/mcp/tools/projects.py) | Project tools. |
| [app/mcp/tools/context.py](../app/mcp/tools/context.py) | Context tools. |
| [app/mcp/tools/memory.py](../app/mcp/tools/memory.py) | Memory tools. |
| [app/mcp/tools/skills.py](../app/mcp/tools/skills.py) | Skill tools. |
| [app/mcp/tools/search.py](../app/mcp/tools/search.py) | V1 search tools. |
| [app/mcp/tools/creator.py](../app/mcp/tools/creator.py) | Creator-profile/pillar tools. |
| [app/mcp/tools/content.py](../app/mcp/tools/content.py) | Content search/history/discovery/novelty/repurpose/export, publication review, publishing receipt, and metrics tools. |
| [app/mcp/tools/system.py](../app/mcp/tools/system.py) | System/health tools. |
| [app/mcp/tools/__init__.py](../app/mcp/tools/__init__.py) | Tool registry. |

Each tool validates a small argument object, calls the REST API with
MCPClient, and formats its typed result. This preserves the same authorization,
audit, lifecycle, and project isolation as normal API users. For example,
`get_content_metrics` only calls the content-metrics GET route, while
`review_content_for_publication` and `mark_content_published` pass an existing
content ID and typed state—not arbitrary prose—to their API routes.

## 14. Command-line interface

The contextctl CLI is a transport and presentation layer. Normal commands call
the API rather than reproducing service/repository rules locally.

| File | Responsibility |
| --- | --- |
| [app/cli/contextctl.py](../app/cli/contextctl.py) | Root parser and command dispatch. |
| [app/cli/api.py](../app/cli/api.py) | HTTP transport. |
| [app/cli/config.py](../app/cli/config.py) | Endpoint/key configuration lookup. |
| [app/cli/output.py](../app/cli/output.py) | Human and JSON renderers. |
| [app/cli/errors.py](../app/cli/errors.py) | Error messages and exit behavior. |
| [app/cli/import_skills.py](../app/cli/import_skills.py) | Operator skill-import entry point. |
| [app/cli/commands/projects.py](../app/cli/commands/projects.py) | Project commands. |
| [app/cli/commands/memory.py](../app/cli/commands/memory.py) | Memory commands. |
| [app/cli/commands/skills.py](../app/cli/commands/skills.py) | Skill commands. |
| [app/cli/commands/search.py](../app/cli/commands/search.py) | Search commands. |
| [app/cli/commands/creator.py](../app/cli/commands/creator.py) | Creator commands. |
| [app/cli/commands/content.py](../app/cli/commands/content.py) | Content lifecycle, search, novelty, repurpose, export commands. |
| [app/cli/commands/keys.py](../app/cli/commands/keys.py) | Operator key-management commands. |
| [app/cli/commands/backup.py](../app/cli/commands/backup.py) | Operator backup/restore commands. |
| [app/cli/commands/health.py](../app/cli/commands/health.py) | Health/diagnostic commands. |

Administrative commands are explicit operator workflows. They still use the
same core services instead of embedding feature-specific MongoDB queries in
argument parser code.

## 15. Observability

| File | Responsibility |
| --- | --- |
| [app/observability/context.py](../app/observability/context.py) | Request-scoped IDs/context for diagnostics. |
| [app/observability/instruments.py](../app/observability/instruments.py) | Metric counter/histogram/gauge definitions. |
| [app/observability/metrics.py](../app/observability/metrics.py) | Registry, collection, and metric exposition. |
| [app/observability/mongo.py](../app/observability/mongo.py) | PyMongo command listener and Mongo command telemetry. |

The `/metrics` route exposes operational measurements; it does not decide what
to measure. Metric definitions live in instruments.py. The Mongo listener
observes commands at the client layer, avoiding hand-written timing code in
every repository.

Do not confuse operational telemetry with V2 content metrics. Operational
telemetry answers questions such as request latency and MongoDB command health;
`content_metrics` holds authorized, user-supplied performance snapshots of a
specific content item. The first is emitted by the running
service; the second is durable product data retrieved through the content API.

Correlate a request through request ID, API logs, audit records, and metrics.
Do not log raw API keys, sensitive source bodies, or vector contents merely for
debugging convenience.

## 16. Backup and restore

| File | Responsibility |
| --- | --- |
| [app/backup/naming.py](../app/backup/naming.py) | Safe/deterministic archive names. |
| [app/backup/manifest.py](../app/backup/manifest.py) | Manifest format, checksums, collection inventory. |
| [app/backup/retention.py](../app/backup/retention.py) | Retention-policy calculation. |
| [app/backup/tools.py](../app/backup/tools.py) | Controlled wrappers around MongoDB backup tools. |
| [app/services/backup_service.py](../app/services/backup_service.py) | Backup/restore orchestration and validation. |
| [app/repositories/backup_repository.py](../app/repositories/backup_repository.py) | Backup persistence support. |

A persistent collection is not fully implemented until the backup configuration
and restore validation include it. A manifest records the archive inventory and
integrity information. Search indexes are derived resources which can be
reconciled/rebuilt; source documents and content version history are durable
data that must be preserved. `content_metrics` is an existing content collection,
so migration v012 extends its validator rather than creating a
second database or backup path. The whole MongoDB database archive therefore
continues to include metric history and its source metadata.

## 17. Small supporting packages

| File | Responsibility |
| --- | --- |
| [app/projects/types.py](../app/projects/types.py) | Project-domain value types. |
| [app/projects/errors.py](../app/projects/errors.py) | Project-domain exceptions. |
| [app/memory/types.py](../app/memory/types.py) | Memory-domain value types. |
| [app/memory/errors.py](../app/memory/errors.py) | Memory-domain exceptions. |
| [app/creator/errors.py](../app/creator/errors.py) | Creator-domain exceptions. |

Most package initializer files only mark a directory as a Python package. The
notable behavior-bearing initializers are
[app/content/platforms/__init__.py](../app/content/platforms/__init__.py), which
registers platform services, and
[app/mcp/tools/__init__.py](../app/mcp/tools/__init__.py), which holds the tool
registry.

## 18. Safe change checklists

### Adding a field to an existing stored document

1. Extend the relevant Pydantic model in [app/models](../app/models).
2. Extend the matching validator in [app/db/schema.py](../app/db/schema.py).
3. Add an append-only migration if existing persisted documents need alteration,
   backfill, or validator evolution.
4. Add a normal index only when a real query, sort, or uniqueness constraint
   needs it.
5. Extend the matching repository serializer/filter/update.
6. Add business validation to the service.
7. Extend public API schemas/routes only if it is an API contract field.
8. Extend backup/restore configuration for a new collection.
9. Test validation, migration, persistence, authorization, and API behavior.

### Adding a searchable field/resource

Perform the previous steps, then:

1. Choose the embedding text representation deliberately.
2. Store embedding model, dimensions, and generated timestamp.
3. Add lexical and vector definitions to the correct DB index module.
4. Make lexical/vector prefilters semantically identical.
5. Reconcile the Search indexes at startup.
6. Test exact lexical hits, semantic hits, hybrid merge, filters, profile/project
   isolation, and archived behavior if applicable.

### Adding an API/MCP/CLI operation

1. Define Pydantic input/output schemas.
2. Add a service method; do not hide the rule in a route.
3. Add/extend repository queries only as needed.
4. Enforce the narrowest scope and resource ownership.
5. Add the route using existing dependencies.
6. Add audit behavior where it is security-sensitive.
7. Add an MCP wrapper only if agents need it; it calls the API.
8. Add a CLI command only if humans need it; it calls API/service boundaries.
9. Add focused tests at each relevant layer.

### Extending content metrics safely

1. Keep `ContentMetricDocument` as an append-only observation; never overwrite a
   historical capture merely because newer numbers arrive.
2. Use a stable `metric_id` for a retryable capture and create a new ID for a
   distinct observation.
3. Validate raw values before persistence: identifier keys, finite non-negative
   numeric values, timezone-aware `captured_at`, and bounded metadata.
4. Query through `list_for_content()` so the profile/content filter and
   `(captured_at, _id)` cursor stay paired with the index.
5. Preserve the route/service authorization sequence: profile grant, current
   content lookup, then all referenced project grants.
6. If an optional provider is added later, implement a narrow adapter that
   supplies this explicit shape. Do not make startup, reads, or manual entry
   depend on provider credentials or availability.
7. Add model, migration, repository, service, API, MCP, authorization, and
   no-provider tests; document any new durable field and backup consequence.

### Adding a platform

1. Extend ContentPlatform in [app/models/content.py](../app/models/content.py).
2. Implement the protocol under [app/content/platforms](../app/content/platforms).
3. Register the adapter in the platform registry.
4. Keep validation/format/export deterministic and non-publishing.
5. Test invalid platform values, Unicode, long content, and code formatting.
6. Add canonical writing guidance when it improves retrieval.

## 19. Testing and reading order

Use focused tests while changing one subsystem. Broad end-to-end and MCP scenario
tests are important integration confidence, but they should not be the only
feedback loop for a small change. Use Ruff for style/lint issues and mypy for
type safety. Do not weaken validation or security just to satisfy a test.

For the publication and metric service boundaries, start with
[tests/unit/test_content_publication.py](../tests/unit/test_content_publication.py),
[tests/unit/test_content_publishing.py](../tests/unit/test_content_publishing.py),
[tests/unit/test_content_metrics.py](../tests/unit/test_content_metrics.py),
and their migration tests:
[tests/unit/test_content_publication_migration.py](../tests/unit/test_content_publication_migration.py)
and
[tests/unit/test_content_metric_migration.py](../tests/unit/test_content_metric_migration.py).
Then read the content MCP tests in
[tests/unit/test_content_mcp.py](../tests/unit/test_content_mcp.py). Together they
show the intended redaction, approval, idempotency, invalid-value, authorization,
pagination, migration, and thin-MCP behavior without requiring every long-running
end-to-end MCP scenario.

A practical reading order is:

1. [app/main.py](../app/main.py) and
   [app/api/dependencies.py](../app/api/dependencies.py).
2. [app/models/common.py](../app/models/common.py) and one domain model such as
   [app/models/content.py](../app/models/content.py).
3. [app/repositories/base.py](../app/repositories/base.py), then a domain
   repository such as [app/repositories/content_repository.py](../app/repositories/content_repository.py).
4. A matching service such as
   [app/services/content_service.py](../app/services/content_service.py), then its API route.
5. [app/db/initializer.py](../app/db/initializer.py),
   [app/db/schema.py](../app/db/schema.py), and the migration runner.
6. [app/retrieval/hybrid.py](../app/retrieval/hybrid.py) plus
   [app/services/retrieval_service.py](../app/services/retrieval_service.py).
7. The matching MCP/CLI adapter to verify it remains thin.

## 20. UML model

This UML-style component/class diagram shows dependency direction and the
important extension seams. It does not attempt to duplicate every field.

~~~mermaid
classDiagram
    direction TB

    class FastAPIApp {
        +create_app(settings)
        +lifespan()
    }
    class Route {
        +parse_request()
        +serialize_response()
    }
    class DependencyProvider {
        +get_service()
        +get_principal()
    }
    class Principal {
        +permits_scope(scope)
        +permits_project(project_id)
        +permits_profile(profile_id)
    }
    class Service {
        <<abstract role>>
        +validate_policy()
        +orchestrate()
    }
    class MongoRepository {
        +_create(document)
        +_get(filter)
        +_page(query, cursor)
        +_update(expected_revision)
    }
    class MongoDBClient {
        +connect()
        +close()
        +require_search_ready()
    }
    class Model {
        <<Pydantic>>
        +validate()
        +serialize()
    }
    class EmbeddingProvider {
        <<Protocol>>
        +embed(texts)
    }
    class LocalEmbeddingProvider {
        +embed(texts)
    }
    class RetrievalService {
        +retrieve()
        +build_context()
    }
    class CreatorService {
        +create_profile()
        +update_profile()
        +list_pillars()
    }
    class ContentService {
        +create_idea()
        +create_brief()
        +create_item()
        +append_version()
        +rollback()
    }
    class ContentRetrievalService {
        +search()
        +get_history()
    }
    class ContentPlatform {
        <<Protocol>>
        +validate(content)
        +format(content)
        +export(content, mode)
    }
    class LinkedInPlatform
    class MediumPlatform
    class GitHubPlatform
    class ContentPublicationService {
        +review_content_for_publication()
        +approve_content(expected_revision)
    }
    class ContentPublishingService {
        +publish_content(expected_revision, provider, idempotency_key)
    }
    class Publisher {
        <<Protocol>>
        +validate_connection()
        +publish(item, idempotency_key)
        +get_publication_status()
    }
    class ManualPublisher
    class ContentMetricsService {
        +create_metric()
        +list_metrics(platform, captured_after, captured_before, cursor)
    }
    class MongoContentMetricRepository {
        +list_for_content()
    }
    class MCPTool {
        +validate_arguments()
        +call_api()
        +format_result()
    }
    class CLICommand {
        +parse_arguments()
        +call_api()
        +render_output()
    }
    class BackupService {
        +backup()
        +restore()
    }

    FastAPIApp --> Route : registers
    Route --> DependencyProvider : requests
    DependencyProvider --> Principal : authenticates
    DependencyProvider --> Service : constructs
    Service --> MongoRepository : queries/persists
    MongoRepository --> MongoDBClient : uses
    Service --> Model : validates
    RetrievalService --|> Service
    CreatorService --|> Service
    ContentService --|> Service
    ContentRetrievalService --|> Service
    Service --> EmbeddingProvider : embeds through
    LocalEmbeddingProvider ..|> EmbeddingProvider
    ContentRetrievalService --> RetrievalService : reuses ranking
    ContentService --> ContentPlatform : export through
    LinkedInPlatform ..|> ContentPlatform
    MediumPlatform ..|> ContentPlatform
    GitHubPlatform ..|> ContentPlatform
    ContentPublicationService --|> Service
    ContentPublishingService --|> Service
    ContentMetricsService --|> Service
    ContentPublicationService --> ContentService : lifecycle update
    ContentPublishingService --> ContentService : persist receipt
    ContentPublishingService --> Publisher : explicit delivery
    ManualPublisher ..|> Publisher
    ContentMetricsService --> MongoContentMetricRepository : append/page observations
    MCPTool --> Route : HTTP client
    CLICommand --> Route : HTTP client
    BackupService --|> Service
    BackupService --> MongoRepository
~~~

## 21. Database schema and relationships

All platform data uses the existing MongoDB deployment. MongoDB does not impose
foreign keys, so services validate referenced IDs and enforce authorization. The
following ER diagram shows intended logical relationships. Main persistent
documents share _id, created_at, updated_at, and revision unless a collection is
explicitly internal.

~~~mermaid
erDiagram
    PROJECTS ||--o{ PROJECT_CONTEXT : contains
    PROJECTS ||--o{ MEMORIES : scopes
    PROJECTS ||--o{ CONVERSATIONS : records
    PROJECTS ||--o{ CONTENT_ITEMS : references
    SKILLS ||--o{ SKILL_CHUNKS : splits_into
    API_KEYS ||--o{ AUDIT_LOGS : produces
    CREATOR_PROFILES ||--o{ CONTENT_PILLARS : owns
    CREATOR_PROFILES ||--o{ CONTENT_IDEAS : owns
    CREATOR_PROFILES ||--o{ CONTENT_BRIEFS : owns
    CREATOR_PROFILES ||--o{ CONTENT_ITEMS : owns
    CREATOR_PROFILES ||--o{ CONTENT_VERSIONS : owns
    CREATOR_PROFILES ||--o{ CONTENT_LINKS : owns
    CREATOR_PROFILES ||--o{ CONTENT_METRICS : owns
    CREATOR_PROFILES ||--o{ CONTENT_CAMPAIGNS : owns
    CONTENT_PILLARS ||--o{ CONTENT_IDEAS : categorizes
    CONTENT_PILLARS ||--o{ CONTENT_ITEMS : categorizes
    CONTENT_IDEAS ||--o{ CONTENT_BRIEFS : informs
    CONTENT_BRIEFS ||--o{ CONTENT_ITEMS : guides
    CONTENT_ITEMS ||--o{ CONTENT_VERSIONS : has_history
    CONTENT_ITEMS ||--o{ CONTENT_LINKS : has_relations
    CONTENT_ITEMS ||--o{ CONTENT_METRICS : measures
    CONTENT_CAMPAIGNS }o--o{ CONTENT_ITEMS : groups

    PROJECTS {
        string project_id UK
        string name
        string description
        string repository
        string environment
        array tags
        int revision
    }
    PROJECT_CONTEXT {
        string project_id
        string type
        string title
        string content
        int importance
        array tags
        string source
        string status
        string external_id
        array embedding
    }
    MEMORIES {
        string scope
        string project_id
        string type
        string content
        string summary
        int importance
        float confidence
        array tags
        string status
        string supersedes
        string superseded_by
        array embedding
    }
    CONVERSATIONS {
        string project_id
        string session_id
        string role
        string content
        object metadata
    }
    SKILLS {
        string skill_id UK
        string name
        string version
        string description
        string category
        array tags
        string trust_level
        string status
        string source_path
        string content_hash
    }
    SKILL_CHUNKS {
        string skill_id
        string skill_version
        string chunk_id
        string content
        string section
        int chunk_index
        int tokens
        object metadata
        array embedding
        string embedding_model
    }
    API_KEYS {
        string key_id UK
        string key_hash
        string name
        array scopes
        array projects
        array profiles
        string status
        datetime expires_at
        datetime last_used_at
    }
    AUDIT_LOGS {
        string action
        string outcome
        string key_id
        string key_name
        string project_id
        string profile_id
        string scope
        string method
        string path
        string request_id
        string reason
    }
    CREATOR_PROFILES {
        string profile_id UK
        string name
        string status
        array audiences
        array domains
        object voice
        object writing_preferences
        object formatting_preferences
        object terminology_preferences
        object language_preferences
        object tone_preferences
        array topics_of_interest
        array topics_to_avoid
        array content_objectives
    }
    CONTENT_PILLARS {
        string profile_id
        string pillar_id
        string name
        string description
        array tags
        int priority
        string status
    }
    CONTENT_IDEAS {
        string profile_id
        string idea_id
        string title
        string description
        string pillar_id
        array platforms
        string origin
        array tags
        int priority
        string status
        array embedding
    }
    CONTENT_BRIEFS {
        string profile_id
        string brief_id
        string idea_id
        string platform
        string objective
        string audience
        string primary_message
        array supporting_points
        array source_refs
        array skills
        array avoid_repetition_with
        array tags
        string status
        array embedding
    }
    CONTENT_ITEMS {
        string profile_id
        string content_id
        string brief_id
        string platform
        string content_type
        string title
        string body
        string status
        array topic_tags
        string pillar_id
        array project_ids
        array source_refs
        int current_version
        datetime published_at
        string external_url
        string publication_provider
        string external_id
        string publication_idempotency_key
        array embedding
    }
    CONTENT_VERSIONS {
        string profile_id
        string content_id
        int version
        string title
        string body
        string status
        string change_reason
        string created_by
        array tags
    }
    CONTENT_LINKS {
        string profile_id
        string link_id
        string content_id
        string source_type
        string source_id
        string project_id
        string relation
        string status
    }
    CONTENT_METRICS {
        string profile_id
        string metric_id
        string content_id
        string platform
        datetime captured_at
        object metrics
        string source
        object provider_metadata
        string status
    }
    CONTENT_CAMPAIGNS {
        string profile_id
        string campaign_id
        string name
        string description
        array content_ids
        array platforms
        string status
    }
~~~

### Collection reference

| Collection | Identity/important indexes | Essential document fields |
| --- | --- | --- |
| skills | Unique skill_id/version; partial active-version identity | skill ID/name/version, description/category/tags, trust/status, source path, content hash. |
| skill_chunks | Unique skill ID/version/chunk ID | Content, section, chunk position, token count, metadata, embedding/model/dimensions/generated time. |
| projects | Unique project_id | Name, description, repository, environment, tags. |
| project_context | Project/filter/recency plus Search indexes | Project ID, type, title, content, importance, tags, source, status, external ID, embedding metadata. |
| memories | Scope/project/status/recency plus Search indexes | Scope, optional project, type, content/summary, importance/confidence, tags/source/status, supersession, embedding metadata. |
| conversations | Project/session/recency indexes | Project ID, session ID, role, content, metadata. |
| retrieval_logs | Time/project indexes | Query, requested sources, selected IDs, result count, latency, retrieval method. |
| api_keys | Unique key_id; active/expiry lookup | Key hash, name, scopes, permitted project/profile lists, status, expiration, last use. |
| audit_logs | Time/key/project/profile indexes | Action/outcome, key metadata, project/profile/scope, method/path, request ID, reason. |
| creator_profiles | Unique profile_id | Name/status/tags, audiences/domains, voice and preference objects, interests/avoidance/objectives. |
| content_pillars | Unique profile_id/pillar_id | Profile, name/description, tags, priority, status. |
| content_ideas | Unique profile_id/idea_id | Profile/title/description/pillar/platforms/origin/tags/priority/status/current embedding. |
| content_briefs | Unique profile_id/brief_id | Idea/platform/objective/audience/message/points/source refs/skills/avoidance/tags/status/current embedding. |
| content_items | Unique profile_id/content_id; profile/platform/status/pillar/tag/project indexes | Brief/platform/type/title/body/status/tags/pillar/projects/provenance/current version/publication fields/current embedding. |
| content_versions | Unique profile_id/content_id/version | Immutable title/body/status snapshot, reason, author, tags, creation time. |
| content_links | Profile/link identity and content/source indexes | Typed source relation: content ID, source type/ID, optional project, relation, status. |
| content_metrics | Profile/content/platform/capture-time indexes; newest-first `(captured_at, _id)` cursor | Immutable platform metric snapshot: capture time, generic metrics object, source, provider metadata, status. |
| content_campaigns | Unique profile_id/campaign_id | Name/description, content IDs, platforms, status. |
| _schema_migrations | Unique version | Internal version/description/applied time history. |

These relationships are logical MongoDB references rather than database-enforced
foreign keys. Services authorize the profile/project and verify referenced
resources before a relationship is created. Normal indexes enforce local
identity/query speed, JSON schema enforces shape, and services enforce
cross-collection rules, lifecycle, and provenance.
