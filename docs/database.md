# Database contracts and migrations

MongoDB is the source of truth for the platform's runtime data. The database
contains core knowledge, creator content, research evidence, client identity,
sessions, credentials, and audit history. Search indexes are derived state;
local model files live in a separate cache.

## Collections and validation

The core knowledge collections are:

| Collection | Purpose | Ownership |
| --- | --- | --- |
| `skills` | Versioned canonical-skill metadata | Organization-owned Git import |
| `skill_chunks` | Runtime chunks for one skill version | Same organization as imported skill |
| `projects` | Software-system boundaries | One organization and explicit directory policy |
| `project_context` | Durable technical decisions and context | Same organization and one project |
| `memories` | Intentional shared or client-operational memory | One org; global/project/agent/project-agent, plus private client ownership |
| `conversations` | Conversation records kept separate from memory | One org/project; not indexed for retrieval |
| `retrieval_logs` | Optional private query diagnostics without result bodies | One org/caller/project; excluded from workforce monitoring |

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

## Ownership catalog

Every served domain collection has immutable organization ownership. The source
catalogs are [tenant inventory](../app/db/tenant_inventory.py),
[identity ownership](../app/identity/repository.py),
[policy](../app/security/policy/inventory.py),
[human management](../app/management/inventory.py),
[managed agents](../app/managed_agents/inventory.py) and
[summaries](../app/summaries/inventory.py).

Private global human identity includes users, exact login identities, local
credentials, MFA, opaque browser sessions/challenges, OIDC transactions and
identity audit. Organization memberships, provider policy, teams, grants,
invitations, review provenance, sponsorship and governed events are tenant state.
Schema/adoption/activation controls and cursor secrets are installation metadata.
Private global users are not an organization-wide directory. Operator quarantine
is outside served domain collections and retains preservation manifests; an
unresolved record blocks compatible adoption, even after quarantine.

Strict validators, repository guards and compound indexes protect application
writes, not against a trusted database administrator. Whole-database archives
include all ownership classes and are installation-operator-only.

Overview aggregates use bounded process memory classified in the summary
inventory. MongoDB change streams invalidate source changes before reuse;
authorization is never cached. The data cache and observation cursors are
recreated after restart/restore. An organization/time covering index supports
cold activity aggregation; normal tenant index reconciliation creates it without
changing stored identities or migration versions. See
[authorized summaries](authorized-summaries.md) for limits and observation times.

## Identity and idempotency

Namespaced natural keys produce deterministic BSON ObjectIds where the domain
needs repeatable creation. Examples include skill ID/version, chunk
ID/version, project ID, and optional context or memory `external_id` values.
Tenant-first compound unique indexes enforce the same identities within one
organization. Only explicitly adopted records retain legacy identity version 1;
new version-2 identities include the organization. Requests cannot choose the
legacy scheme. Adopted ObjectIds, revisions and actors remain unchanged, and
validators/lookups support subsequent updates to those adopted objects.

All idempotency is first bounded by organization and current authority.
Context idempotency is additionally bounded by its project. Shared memory idempotency is
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
Trusted tenant context is mandatory. Ownership and action-specific resource
fields cover CRUD, counts/distinct, joins, search, references, jobs and exports.
These ownership fields are present in query filters, rather than checked
only after an unscoped read. API and MCP callers must also satisfy operation
scopes and the relevant project/profile grants.

Client ownership is derived from authentication. A caller cannot select another
client's operational memory by putting its ID in a request. Organization-global and
project records retain their explicitly authorized shared meaning even when an agent authored them.
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
| 36 | Explicit tenant schema, preserved legacy identity and resumable adoption |
| 37 | Tenant isolation, search/cursor/job ownership and sealed activation |
| 38 | Private global local identity, MFA and hashed browser sessions |
| 39 | Exact issuer/subject OIDC, assurance and organization switching |
| 40 | Coupled policy, delegation, governance audit/outbox and shared fencing |
| 41 | Teams, invitations, access requests and membership lifecycle |
| 42 | Verified machine sponsorship, service accounts and one-time credentials |

Migrations preserve existing records where a contract is widened. For example,
migrations 2–4 add embedding metadata to core knowledge collections, migration
9 extends current content representations, and migration 27 adds client-scoped
memory ownership. Migrations 18 and 20 preserve research provenance on promoted
knowledge and content briefs. Migrations 33–34 add reusable client profiles
without widening existing client grants.

The current schema head is **42**. The identity/policy/management transitions
are guarded operator cutovers, not an instruction to start a full console against
an unadopted database. Follow [staged migration/recovery](migration-recovery.md).
An API flag does not substitute for explicit adoption or worker exclusion.

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

```sh
bash scripts/test-v6-isolation.sh
```

The integration suite writes fixtures. Give it its own database and volumes,
following [Contributing](../CONTRIBUTING.md). It checks repeated initialization,
required indexes, strict validation, CRUD, pagination, optimistic updates, and
ownership boundaries against the actual replica set.
