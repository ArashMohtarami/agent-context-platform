# Database contracts and migrations

MongoDB is the source of truth for the platform's runtime data. The database
contains core knowledge, creator content, research evidence, client identity,
sessions, credentials, and audit history. Search indexes are derived state;
local model files live in a separate cache.

## Collections and validation

The core knowledge collections are:

| Collection | Purpose | Ownership |
| --- | --- | --- |
| `skills` | Versioned canonical-skill metadata | Global |
| `skill_chunks` | Runtime chunks for one skill version | Global |
| `projects` | Software-system boundaries | Authorized project directory |
| `project_context` | Durable technical decisions and context | One project |
| `memories` | Intentional shared or client-operational memory | Global, project, agent, or project-agent |
| `conversations` | Conversation records kept separate from memory | One project; not indexed for retrieval |
| `retrieval_logs` | Optional query diagnostics without result bodies | Global or one project, with caller provenance |

Creator/content collection contracts are documented in
[content-schema.md](content-schema.md), research evidence and review workflows in
[research.md](research.md), and client-profile administration in
[client-profiles.md](client-profiles.md). The current validator inventory is
[app/db/schema.py](../app/db/schema.py); the collection and index appendix in
[codebase onboarding](onboarding.md) provides navigation to each domain.

Application models reject unknown fields. MongoDB also enforces strict
`$jsonSchema` validators with `validationAction: error`, so a write that bypasses
a repository cannot silently introduce a structurally invalid record. Records
use BSON `_id` values, UTC timestamps, and revision metadata. Mutable operations
use revision checks; immutable versions, citations, review events, and metric
observations preserve their own historical records.

Embeddable records retain `embedding`, `embedding_model`,
`embedding_dimensions`, and `embedding_generated_at` together. A vector is
usable only with a compatible model and dimensions. See
[embeddings.md](embeddings.md), [retrieval.md](retrieval.md), and
[content-retrieval.md](content-retrieval.md) for vector policy and derived indexes.
Conversation records are not automatically embedded or promoted into memory.

Skills, context, and memory use `active`, `archived`, and `superseded` lifecycle
states. A partial unique index permits at most one active version of a skill ID.
Canonical skill Markdown remains in Git; MongoDB stores its validated runtime
representation.

## Identity and idempotency

Namespaced natural keys produce deterministic BSON ObjectIds where the domain
needs repeatable creation. Examples include skill ID/version, chunk
ID/version, project ID, and optional context or memory `external_id` values.
Standard unique indexes enforce the same domain identities.

Context idempotency is bounded by its project. Shared memory idempotency is
bounded by scope and project; agent scopes additionally include the
authenticated client identity. Records without an external ID can use a
generated ObjectId. A repeated create must still pass the same authorization
checks as the first request.

## Repositories and isolation

Asynchronous persistence adapters live in `app/repositories/`. The shared base
owns validated serialization, CRUD primitives, cursor pagination, and revision
checks; concrete repositories expose domain-specific operations.

Project context and conversation repositories are bound to one project.
Memory repositories are bound to their resolved scope, project, and client
where applicable. Content repositories are bound to one creator profile.
These ownership fields are present in the query filters, rather than checked
only after an unscoped read. API and MCP callers must also satisfy operation
scopes and the relevant project/profile grants.

Client ownership is derived from authentication. A caller cannot select another
client's operational memory by putting its ID in a request. Shared global and
project records retain their shared meaning even when an agent authored them.
See [memory.md](memory.md) for the four supported memory scopes.

Mutable updates match the record and `expected_revision`, set a fresh
`updated_at`, and increment `revision`. A stale match produces a conflict
instead of silently overwriting another caller's work. Cursor-based lists use
stable sort keys, usually `(created_at, _id)`; metric captures use
`(captured_at, _id)`.

## Migrations and startup

[Migration source files](../app/db/migrations/) define the durable schema
changes. `_schema_migrations` records each applied version under a unique
index. Startup applies pending migrations and reconciles standard indexes;
Search/Vector Search indexes are reconciled separately through MongoDB's search
management interface.

| Migration range | Contracts introduced or extended |
| --- | --- |
| 1–5 | Core collections, skill/context/memory embeddings, API keys, audit |
| 6–12 | Creator/content schema, profile grants, content embeddings, approval audit, publication receipts, metric provenance |
| 13–21 | Research schema and source registry, structured briefs, immutable review decisions, controlled promotion, skill proposals, evidence-backed content briefs, freshness |
| 22–32 | Agent clients, credentials, project bindings, caller provenance, agent memory scopes, sessions, gateway retry controls |
| 33–35 | Reusable client profiles, profile lifecycle, Gemini client type |

Migrations preserve existing records where a contract is widened. For example,
migrations 2–4 add embedding metadata to core knowledge collections, migration
9 extends current content representations, and migration 27 adds client-scoped
memory ownership. Migrations 18 and 20 preserve research provenance on promoted
knowledge and content briefs. Migrations 33–34 add reusable client profiles
without widening existing client grants.

Review the exact migration before an upgrade or rollback. Some migrations
refuse automatic reversal because removing authored content, review decisions,
credentials, or provenance would destroy durable state. A successful schema
rollback is not a substitute for a verified backup.

## Recovery and verification

Full-database backups include every durable collection and retain stored
vectors. Search indexes are rebuilt from restored documents; they are not a
second source of truth. Restore verification compares collection counts in an
isolated temporary namespace. See [backup.md](backup.md) for operational commands
and recovery limits.

Run the real database checks against a disposable stack:

```bash
docker compose up -d --build --wait
docker compose --profile test run --rm --build test
```

The integration suite writes fixtures. Give it its own database and volumes,
following [Contributing](../CONTRIBUTING.md). It checks repeated initialization,
required indexes, strict validation, CRUD, pagination, optimistic updates, and
ownership boundaries against the actual replica set.
