# MongoDB and mongot tutorial for this project

This tutorial assumes MongoDB is new to you. It explains the MongoDB stack
used by Agent Context Platform, then connects each concept to the files you
will edit in this repository.

Terminology first: people sometimes say “Mongo stack.” In this project that
means **MongoDB (`mongod`) + MongoDB Community Search (`mongot`) + PyMongo + a
local embedding model**. `mongot` is not a replacement database and not a
separate vector database.

## 1. The mental model

| Relational database term | MongoDB term | Example here |
| --- | --- | --- |
| Server | `mongod` process | Compose service `mongodb` |
| Database | Database | one application database from `MONGODB_DATABASE` |
| Table | Collection | `projects`, `memories`, `content_items` |
| Row | BSON document | one `ProjectDocument` |
| Column | Field | `project_id`, `tags`, `revision` |
| Primary key | `_id` | BSON `ObjectId` |
| Schema constraint | `$jsonSchema` validator | `COLLECTION_VALIDATORS` |
| Index | MongoDB index | `project_id_uq`, cursor indexes |
| Full-text/vector index | Search index in `mongot` | `skill_chunk_text_index` |

A MongoDB document is JSON-like, but MongoDB stores **BSON**, which includes
native types such as `ObjectId`, UTC date, binary data, integers, and doubles.
The following resembles a persisted project document:

```json
{
  "_id": {"$oid": "..."},
  "project_id": "payment-service",
  "name": "Payment Service",
  "description": "Card and wallet payments",
  "repository": "git@example.invalid:team/payment-service.git",
  "environment": "production",
  "tags": ["payments", "python"],
  "created_at": {"$date": "2026-09-23T00:00:00Z"},
  "updated_at": {"$date": "2026-09-23T00:00:00Z"},
  "revision": 1
}
```

MongoDB does not require every document in a collection to have identical
fields by default. This project deliberately enables strict collection
validators, so it gets the flexibility of documents without allowing arbitrary
or malformed fields into core records.

## 2. What runs in Docker

Read [docker-compose.yml](../docker-compose.yml) with this diagram:

```text
                    private Docker network: agent_context

context-api -- Async PyMongo --> mongodb / mongod (replica set rs0)
                                      |
                                      | authenticated gRPC sync/coordinator channel
                                      v
                              mongot (Community Search)

MCP process -- HTTP only --> context-api
```

- **`mongodb`** runs `mongod`, the source of truth. It holds collections,
  documents, ordinary indexes, transactions, users, and validators.
- **`mongodb-init`** is an idempotent bootstrap job. It initializes replica
  set `rs0` and creates narrowly scoped users.
- **`mongot`** maintains MongoDB Search and Vector Search indexes derived from
  MongoDB documents. The API talks to `mongod`, never directly to `mongot`.
- **`context-api`** runs FastAPI, PyMongo, service logic, and the local model.
- **`mcp`** is a separate optional stdio process. It has no MongoDB credentials.

MongoDB and mongot are on an internal Docker network. MongoDB’s port is not
published. The API alone exposes `127.0.0.1:8000` by default. Do not expose
`mongod` or `mongot` to the internet as a convenience shortcut.

Start safely:

```bash
./scripts/generate-dev-secrets.sh
./scripts/prepare-docker-wheels.sh
docker compose up -d --build --wait
docker compose ps
```

The first local embedding-model load can take longer than later starts because
the model cache is initially empty.

## 3. The application’s MongoDB connection

Open [app/db/client.py](../app/db/client.py).

`create_mongodb_client(settings)` creates one `AsyncMongoClient` with:

- application—not root—credentials;
- `authSource` set to the application database;
- replica-set awareness (`rs0`), required for transactions;
- bounded server-selection, connection, and command timeouts;
- a bounded connection pool;
- UTC-aware dates; and
- an optional command listener for latency metrics.

`MongoDBClient.connect()` does not accept “a socket opened” as healthy. It
calls `check_mongodb_health()` to require database, replica set, and normally
mongot readiness. It then exposes two handles:

```python
mongodb.client     # AsyncMongoClient: sessions and server-level actions
mongodb.database   # AsyncDatabase: collections in MONGODB_DATABASE
```

Repositories receive the database handle. They should not create one-off
clients. One application client means pooled sockets, consistent settings, and
one lifecycle owner.

## 4. Collections: the application data map

`app/db/schema.py:COLLECTION_VALIDATORS` is the authoritative list of managed
collections. The following table is the practical map.

| Collection | Owner/scope | Canonical data |
| --- | --- | --- |
| `skills` | global | Git-canonical skill version metadata |
| `skill_chunks` | global | searchable chunks and their embeddings |
| `projects` | global directory | project boundaries |
| `project_context` | one project | durable technical facts |
| `memories` | global, project, agent, or project-agent | deliberate memory, including supersession links |
| `conversations` | one project | conversation messages; not automatic memory |
| `retrieval_logs` | global/project | safe retrieval diagnostics, not result bodies |
| `api_keys` | global | hashed API credentials and grants |
| `audit_logs` | global | authorization/write audit events |
| `creator_profiles` | creator profile | preferences as data, never system instructions |
| `content_pillars` | creator profile | content themes |
| `content_ideas` | creator profile | source-backed candidate ideas |
| `content_briefs` | creator profile | evidence packages for a platform |
| `content_items` | creator profile | current draft/approved/published copy |
| `content_versions` | creator profile | immutable historical snapshots |
| `content_links` | creator profile | provenance relations |
| `content_metrics` | creator profile | captured platform metrics |
| `content_campaigns` | creator profile | groups of content items |
| `_schema_migrations` | internal | versions of migrations already applied |

`mongot` also has a tiny probe collection for its readiness check. It contains
no application data.

## 5. Three layers validate every write

The platform is strict on purpose. A valid write passes all three layers:

```text
HTTP/MCP input schema --> Pydantic persisted model --> MongoDB validator
```

1. **Request schemas** in `app/schemas/` reject unknown HTTP fields and invalid
   caller input early.
2. **Models** in `app/models/` normalize timestamps, validate identifiers,
   calculate deterministic IDs, and enforce Python-level invariants.
3. **MongoDB `$jsonSchema`** in `app/db/schema.py` rejects bad direct database
   writes too (`additionalProperties: false`, required fields, BSON types,
   enum values, and limits).

This is defense in depth, not duplication by accident. Pydantic gives a good
error at the API boundary; the MongoDB validator protects data if a script or
future bug bypasses a repository.

Example: `EmbeddingFields` in `app/models/embedding.py` requires the vector,
model name, vector dimensions, and generation timestamp to be present together.
The schema applies the equivalent BSON restriction. A vector with no model
metadata is therefore rejected before it can contaminate search.

## 6. IDs, timestamps, idempotency, and revisions

### `_id` and deterministic ObjectIds

Every document has MongoDB `_id`, normally a BSON `ObjectId`. Many domain
documents derive it from a namespace plus a natural key using
`deterministic_object_id()` in `app/models/common.py`.

Examples:

- a project is determined by `project_id`;
- a skill version by `(skill_id, version)`;
- a content item by `(profile_id, content_id)`;
- a content version by `(profile_id, content_id, version)`.

This makes a retry safe: trying to install the same identity cannot silently
create a duplicate. Unique ordinary MongoDB indexes enforce the same property
at the database layer.

### Timestamps

`created_at` never changes. `updated_at` changes on mutable updates. The code
uses aware UTC dates, never naïve local timestamps. This makes ordering,
cursor pagination, backups, and audit records comparable across machines.

### Optimistic concurrency with `revision`

Most mutable records use a positive `revision`:

```text
read revision 7
       |
send expected_revision=7
       |
MongoDB update filter includes {_id, revision: 7}
       |
success: $set changed fields + $inc {revision: 1}
failure: no match -> ConcurrencyConflictError
```

The common implementation is `MongoRepository._update()` in
`app/repositories/base.py`. It prevents “last writer silently wins.” A route
turns the conflict into a safe client error; the client must reload and decide
how to reconcile edits.

## 7. CRUD and repository query shapes

The driver is asynchronous PyMongo. The repository layer uses normal MongoDB
operations such as `insert_one`, `find_one`, `find`, `find_one_and_update`, and
`delete_one`, always with a scope-aware filter.

Illustrative read-only MongoDB shell commands, once you are already
authenticated in `mongosh`:

```javascript
// Inspect names and validators; use this for learning, not for editing schemas.
db.getCollectionInfos({ name: "projects" })

// Read only selected fields; _id is excluded explicitly here.
db.projects.find({}, { _id: 0, project_id: 1, name: 1, revision: 1 })

// Inspect ordinary indexes used by the repository.
db.content_items.getIndexes()

// Inspect recorded schema migrations.
db._schema_migrations.find({}, { _id: 0 }).sort({ version: 1 })
```

Do **not** learn by manually writing application documents in production. A
manual write can skip service lifecycle rules, provenance checks, embeddings,
audit behavior, and version transactions. Use the API/CLI or a focused test
fixture instead.

### Project and profile isolation is in the query

The database cannot infer an API caller’s project access by itself. The
application makes it hard to forget by constructing scope-bound repositories:

```text
MongoProjectContextRepository(database, project_id)
MongoMemoryRepository(database, project_id=project_id)
MongoContentItemRepository(database, profile_id)
```

Their filters include the owning `project_id` or `profile_id` on every read and
write. In `app/repositories/content_repository.py`, `_ProfileRepository`
checks the profile before it sends the database command. In the API layer,
`authorize_project` and `authorize_profile` decide whether the authenticated
principal may create that bound repository operation.

## 8. Standard MongoDB indexes

An ordinary MongoDB index is a data structure that makes a filter/sort fast.
It is not the same as full-text or vector search.

Index definitions live in `app/db/indexes.py:INDEX_DEFINITIONS`; startup calls
`create_indexes()` every time. That is idempotent: an existing identical index
is retained.

Common patterns in this project:

| Pattern | Example | Why |
| --- | --- | --- |
| Unique index | `(profile_id, content_id)` | one item identity per profile |
| Partial unique index | active `skills` version | only one active version, old versions preserved |
| Compound filter/sort | `(profile_id, platform, status, published_at)` | content feeds and platform filters |
| Multikey tag index | `tags` | find documents containing a tag |
| Cursor index | `(profile_id, created_at, _id)` | stable keyset pagination |
| Maintenance index | `(embedding_model, embedding_dimensions)` | find records to re-embed after a model change |

Indexes speed reads but cost disk, memory, and write work. Add one only for a
concrete query. Before adding it, identify the exact repository filter and
sort, then add an integration test that proves the query stays correct.

## 9. Pagination: why this project does not use offsets

An offset query says “skip 10,000 rows,” which gets slower at large offsets and
can duplicate/omit rows while writes happen. This project uses keyset (cursor)
pagination.

The cursor contains the last `(created_at, _id)` pair. The next query uses a
compound sort and selects records after that pair. `encode_page_cursor()` and
`decode_page_cursor()` in `app/models/common.py` make the cursor opaque to API
clients.

```text
page 1: sort {created_at: 1, _id: 1}, limit 20
        return cursor of row 20
page 2: same sort, filter strictly after row-20 key, limit 20
```

The matching ordinary compound index makes this stable and efficient.

## 10. Transactions: when one document is not enough

MongoDB updates to a single document are atomic. Use one document when you can.
Use a multi-document transaction only when an invariant spans documents.

The key real example is V2 content versioning:

```text
content_items current copy     +     content_versions immutable snapshot
```

`MongoContentItemRepository.create_with_initial_version()` inserts both in one
transaction. `append_version()` inserts a new immutable version and advances
the current item only if its `expected_revision` still matches. Read
`app/repositories/content_repository.py` around those two methods.

Conceptually:

```python
async with database.client.start_session() as session:
    await session.with_transaction(transaction)

async def transaction(session):
    await versions.insert_one(new_snapshot, session=session)
    current = await items.find_one_and_update(
        {"_id": item_id, "revision": expected_revision},
        {"$set": next_current, "$inc": {"revision": 1}},
        session=session,
    )
    if current is None:
        raise ConcurrencyConflictError(...)
```

Transactions require the replica set configured by Compose, even in a
single-node development deployment. Do not add transactions to ordinary single
document updates; they cost more and solve no additional problem there.

## 11. Migrations: change schema without breaking data

Migration code is in `app/db/migrations/`; the ordered registry is
`runner.py:MIGRATIONS`. On application startup:

1. `_schema_migrations` gains/uses its unique `version` index.
2. `run_migrations()` reads already applied versions.
3. Each missing migration runs `up()`.
4. MongoDB records the version and timestamp.
5. `ensure_indexes()` reconciles ordinary indexes.

Migration 1 creates strict collections. Later migrations add fields or adjust
validators in safe order. For example, an embedding migration first widens the
validator so null embedding fields are accepted, then backfills them. A
validator change uses MongoDB’s `collMod` command; the application user has a
narrow role for that task.

When adding persistence:

```text
Pydantic model -> JSON Schema validator -> additive migration -> normal indexes
-> repository -> service -> schema/route -> backup/restore documentation -> tests
```

Never rename an existing collection as a casual refactor. Never make an old
validator stricter before existing documents can satisfy it. Prefer additive,
backwards-compatible migrations and idempotent initialization.

## 12. Search indexes and mongot

### `mongod` versus `mongot`

`mongod` stores documents and receives every command from the application.
`mongot` synchronizes from the replica set and serves derived MongoDB Search
and Vector Search indexes. The application calls search through `mongod`, such
as `$search`, `$vectorSearch`, `create_search_index`, and
`list_search_indexes`; it has no direct mongot URL or credential.

```text
PyMongo aggregation -> mongod -> mongot -> candidate documents -> mongod -> API
```

Search-index definitions are in:

- `app/db/text_indexes.py` and `app/db/vector_indexes.py` for skill chunks;
- `app/db/context_indexes.py` and `app/db/memory_indexes.py` for V1 context
  and memory; and
- `app/db/content_indexes.py` for V2 ideas, briefs, and current content items.

`app/db/search_indexes.py:reconcile_search_index()` has three safe outcomes:
`created`, `updated`, and `unchanged`. Definitions are compared so startup only
changes an index when application-controlled fields actually differ.

### Lexical search

Lexical search matches terms and phrases. It is best for exact vocabulary:
`Keycloak`, `wallet:read`, endpoint paths, error names, and identifiers.

This project’s text indexes use a standard analyzer for prose and an additional
exact-ish identifier analyzer for punctuation-bearing technical identifiers.
The query builder gives exact identifier matches a stronger boost than ordinary
text matches. Read [retrieval.md](retrieval.md) and
`app/db/text_indexes.py:compound_text_query`.

### Vector search

Vector search compares embeddings. It is best for conceptually similar wording:

```text
"How can payment retries avoid duplicate charges?"
                         ~
"Use a stable idempotency key for a retried payment request."
```

The local embedding model turns text into a normalized vector. MongoDB Vector
Search uses cosine similarity. The vector index knows the dimension count;
startup derives it from the loaded model rather than trusting a copied number.

Vector filters must be declared in the vector index and are applied **before**
nearest-neighbor candidate selection. That is why content vector definitions
include `profile_id`, platform, project, status, pillar, and tags as filter
paths: a private or wrong-profile document should never become a candidate.

### Hybrid retrieval

`RetrievalService` runs lexical and vector retrieval concurrently. The pure
functions in `app/retrieval/hybrid.py` merge candidates; `ranking.py` combines
normalized semantic/lexical scores with importance, recency, and source
priority; `context_builder.py` enforces the token budget.

Vector score and lexical score are not naturally comparable. The platform
normalizes them before blending; do not add their raw values directly.

## 13. Embeddings are stored data; search indexes are derived data

The application stores an embedding inside each embeddable document, alongside
its model metadata. That data is backed up. `mongot` search indexes are derived
from it and are rebuilt after restore.

```text
canonical document + embedding  -- backed up
           |
           v
mongot Search/Vector index      -- rebuildable, not backed up
```

This distinction prevents a common mistake: deleting an index is recoverable;
deleting a document is data loss. See [embeddings.md](embeddings.md) and
[backup.md](backup.md).

Not every historical record is embedded. For example, V2 content retrieval
embeds the meaningful current idea/brief/item representation, not every
immutable `content_versions` snapshot. History is still preserved in MongoDB.

## 14. Backup, restore, and inspection

MongoDB is the only primary state. The backup command runs inside the API
container because MongoDB’s port is intentionally private:

```bash
docker compose exec -T context-api python -m app.cli.contextctl backup
docker compose exec -T context-api python -m app.cli.contextctl backups list
docker compose exec -T context-api python -m app.cli.contextctl backups verify --restore
```

The backup archive contains documents, validators, ordinary indexes, and
stored vectors. Search indexes are rebuilt afterward with the existing
reindexing command. Secrets are not in database backups; protect them in a
separate secret manager.

Before a risky operation, read [backup.md](backup.md) completely. In
particular, avoid `docker compose down --volumes` unless you intentionally
want to remove persistent MongoDB and mongot data.

Useful operational checks:

```bash
docker compose ps
docker compose logs --tail=100 mongodb
docker compose logs --tail=100 mongot
docker compose exec -T context-api python -m app.cli.contextctl health
make verify-mongodb
make verify-mongot
```

## 15. Common beginner mistakes

| Mistake | Why it is wrong here | Safer alternative |
| --- | --- | --- |
| Treating mongot as a second database | It is only derived search state | Write canonical data through services/repositories to MongoDB |
| Editing records directly in Compass/mongosh | Skips lifecycle, provenance, audit, and embeddings | Use API/CLI; use direct reads only for diagnosis |
| Adding an index to solve every query | Indexes slow writes and consume resources | Start from a measured repository query |
| Filtering vector results after `$vectorSearch` | Can return too few and risk scope mistakes | Declare filter fields and pre-filter in index/query definition |
| Using `skip`/`limit` for deep pages | Slow and unstable during writes | Reuse opaque cursor pagination |
| Updating a content body in place | Breaks version history | Create a new content version / rollback as a new version |
| Restoring only MongoDB and assuming search works | mongot indexes are derived and may be absent | Rebuild/reconcile search indexes after restore |
| Changing vector dimensions alone | Stored vectors and index become incompatible | Change model and dimensions together, re-embed, then reconcile |
| Disabling strict validators to make a script work | Converts an immediate error into latent corrupt data | Fix the model/migration/script instead |

## 16. Suggested hands-on learning sequence

1. Start the stack and call `contextctl health`.
2. Create/list a test project through the API or CLI; inspect the response’s
   `revision`, timestamps, and `project_id`.
3. Read `ProjectDocument`, its repository, and the matching validator.
4. Import one skill; observe a `skills` document and its `skill_chunks`.
5. Run lexical search for an exact identifier and vector search for a related
   idea. Compare why the result sets differ.
6. Read index names with `getIndexes()` and Search index state with the
   platform health endpoint; do not alter them manually.
7. Make two simulated updates using the same expected revision in a unit test
   and observe the conflict.
8. Read the content-version transaction test, then follow the repository
   transaction code.
9. Take and verify a backup in a disposable development environment.

The best feedback loop is: read one route, one service, one repository, its
model, its schema validator, and its focused test. Repeat by domain instead of
trying to memorize MongoDB commands in isolation.

## 17. Reference map

- MongoDB startup/client: `app/db/client.py`, `app/db/health.py`,
  `app/db/initializer.py`
- Collections and validators: `app/db/schema.py`, `app/db/collections.py`
- Ordinary indexes: `app/db/indexes.py`
- Migrations: `app/db/migrations/`
- Repository queries: `app/repositories/`
- Search definitions: `app/db/*_indexes.py`, `app/db/search_indexes.py`
- Embeddings: `app/embeddings/`, `app/services/embedding_service.py`
- Ranking: `app/retrieval/`, `app/services/retrieval_service.py`
- Operations: [database.md](database.md), [mongot.md](mongot.md),
  [backup.md](backup.md), [deployment.md](deployment.md)

## 18. What “MongoDB expert” means

Expertise is not memorizing shell commands. An expert can make and defend a
data-modeling decision, predict a query’s index behavior, explain a consistency
guarantee, diagnose a slow operation from evidence, recover safely, and know
when **not** to add a MongoDB feature.

Use this guide as a curriculum, not a promise that one read replaces practice.
You become proficient by completing the labs, reading query plans, breaking
only disposable data, reviewing real incidents, and revisiting these topics as
the dataset and traffic change.

| Level | You can do this | Project evidence to study |
| --- | --- | --- |
| Foundation | read/write a document, use a filter, understand BSON | `ProjectDocument` and `MongoProjectRepository` |
| Application developer | model data, paginate, add a validated field, handle conflicts | `MongoRepository`, Pydantic models, migrations |
| Database-aware developer | choose/index a query, read `explain`, use aggregation/transactions deliberately | `INDEX_DEFINITIONS`, content-version transaction tests |
| Operator | assess health, capacity, backups, restore, and security | Compose, readiness, backup service, mongot docs |
| System designer | reason about replica sets, read/write concerns, sharding, and failure domains | sections 25–31; not all are enabled in this local deployment |

The current Compose deployment is a **single-node replica set**, which enables
transactions but is not high availability. Learn the concepts of multi-node
replica sets and sharding here; do not pretend a development compose stack has
tested them for production.

## 19. BSON and the MongoDB query language

### BSON types are part of the schema

MongoDB query matching is type-sensitive. The string `"42"`, integer `42`,
double `42.0`, `ObjectId("...")`, and a `Date` are different values. This is
why the project validates IDs, UTC timestamps, floats, and vectors before a
repository writes them.

Important BSON types in this project:

| BSON type | Used for | Why it matters |
| --- | --- | --- |
| `objectId` | `_id`, some supersession links | compact, indexed identity; not a user-facing domain ID |
| `string` | names, identifiers, prose, tags | human data and stable natural keys |
| `date` | timestamps, expiry, publication time | store UTC-aware instants, not locale-formatted strings |
| `int`/`long` | revision, versions, token counts | use integral values for ordering/counts |
| `double` | scores, importance, embeddings | vectors require numeric double components |
| `array` | tags, source refs, embeddings | query and index behavior differs from a scalar |
| `object` | provenance, metadata, voice preferences | nested data that belongs to the same record |
| `null` | optional fields | distinguish “known to be empty” from absent where the schema requires it |

Do not use an ObjectId’s timestamp as a business timestamp. This project stores
explicit `created_at` and `updated_at`, then uses those values for pagination
and lifecycle decisions.

### Filters: the vocabulary behind repository methods

The following `mongosh` examples are for a throwaway development copy or for
read-only diagnosis after you have authenticated. The Python repository layer
builds the same BSON expressions as dictionaries.

```javascript
// Equality: one project boundary.
db.project_context.find({ project_id: "payment-service" })

// Several conditions on the same document.
db.content_items.find({
  profile_id: "personal-brand",
  platform: "linkedin",
  status: "published"
})

// An array contains a requested tag.
db.projects.find({ tags: "payments" })

// Every requested tag must be present.
db.projects.find({ tags: { $all: ["payments", "python"] } })

// Nested field access uses dot notation.
db.content_ideas.find({ "origin.project_id": "payment-service" })

// Explicit alternatives for shared memory only. Agent scopes also require
// the authenticated client identity in their ownership branch.
db.memories.find({
  $or: [
    { scope: "global", project_id: null },
    { scope: "project", project_id: "payment-service" }
  ]
})
```

The project uses filters as security boundaries. Do not build a broad query and
filter records in Python afterward if a project/profile condition can be added
to the MongoDB filter itself.

### Projection, sort, limit, and update operators

```javascript
// Projection reduces network/decoder work. _id is included unless excluded.
db.projects.find({}, { _id: 0, project_id: 1, name: 1 }).sort({ created_at: 1 }).limit(20)

// A narrow atomic update; this is illustrative, not the app's full policy.
db.projects.updateOne(
  { project_id: "payment-service", revision: 3 },
  { $set: { environment: "staging", updated_at: new Date() }, $inc: { revision: 1 } }
)

// Common operators: $set, $unset, $inc, $push, $addToSet, $pull, $currentDate.
// Choose an operator that expresses the intended invariant; do not replace an
// entire document just to change one field.
```

`updateOne` being atomic does not make a read-modify-write workflow safe. When
the old value matters, include it in the filter—this is the `revision` pattern
implemented in [`app/repositories/base.py`](../app/repositories/base.py).

## 20. Data modeling: the highest-leverage MongoDB skill

MongoDB data modeling begins with **access patterns**, not an entity diagram.
Before defining a collection, write down:

1. What document is the unit users read and change together?
2. What queries must be fast, with what filter and sort?
3. What values grow without a fixed bound?
4. What must be consistent atomically?
5. What ownership/security condition must appear in every query?
6. What history must remain immutable?

### Embed versus reference

| Choose embedding when | Choose a reference when |
| --- | --- |
| data is bounded and belongs to one parent | child data can grow without bound |
| parent and child are read/written together | child is independently queried, updated, or owned |
| one-document atomicity is valuable | many parents share the child |
| duplicating a small snapshot is intentional | copying would create inconsistent duplicated truth |

Examples in this codebase:

- `ContentSourceReference` is embedded in briefs/items: provenance is bounded,
  must travel with the content, and is checked before content is returned.
- `content_versions` is a separate collection: history grows, is immutable,
  has its own pagination, and must never be overwritten inside the current
  `content_items` document.
- `skill_chunks` is separate from `skills`: chunks are numerous/searchable and
  each carries its own embedding; the skill metadata remains compact.
- `tags` are embedded arrays because they are small labels belonging to one
  document. A separate `tags` collection would add joins without a current
  access-pattern benefit.

MongoDB has a 16 MiB maximum BSON document size. Even before that limit, a
rapidly growing array makes updates, indexes, and network reads unpleasant.
Use a child collection for unbounded history, events, messages, or chunks.

### Intentional duplication is allowed—but must have an owner

Document databases often copy a small value to improve reads. The current
content item stores `current_version`; the immutable snapshot also carries its
own title/body/status. This duplication is safe only because:

- `ContentService` defines the update policy;
- `append_version()` changes both records transactionally; and
- earlier snapshots are never rewritten to “synchronize” them.

When you duplicate data, write down which copy is canonical, who updates
derived copies, and how tests prove the invariant.

### Schema evolution in a document database

MongoDB allows field-level evolution, but production data still needs a plan:

```text
1. Add an optional/backwards-compatible model field.
2. Widen the MongoDB validator in an additive migration.
3. Deploy readers that tolerate old and new documents.
4. Backfill if required, in batches with observability.
5. Make the field required only after every document complies.
6. Remove compatibility reads later, not in the same risky release.
```

This project records migrations in `_schema_migrations` and uses strict
validators. See [`app/db/migrations/runner.py`](../app/db/migrations/runner.py)
and [`app/db/schema.py`](../app/db/schema.py).

## 21. Aggregation pipelines: MongoDB’s data-processing language

`find()` is ideal for “return matching documents.” An aggregation pipeline is
for staged computation: filter, reshape, join a bounded related collection,
group, calculate, then sort/limit.

Think of a pipeline as data flowing through stages:

```text
collection -> $match -> $project/$set -> $unwind -> $group -> $sort -> result
```

Essential stages:

| Stage | Purpose | Expert warning |
| --- | --- | --- |
| `$match` | filter documents | place selective matches early so an index can help |
| `$project` | select/rename/compute fields | reduce payload before expensive stages |
| `$set`/`$addFields` | add computed fields | do not overwrite canonical data in a read pipeline |
| `$unwind` | one output per array element | output can multiply dramatically |
| `$group` | aggregate by key | can consume memory; control cardinality |
| `$sort` | order results | needs an index before blocking stages to avoid memory work |
| `$limit` | bound result size | usually put immediately after relevant sort |
| `$lookup` | join another collection | model/query choice, not a free relational join |
| `$facet` | run bounded subpipelines together | useful for counts + page, but budget it |

Example: count published V2 content by platform for one authorized profile.

```javascript
db.content_items.aggregate([
  { $match: { profile_id: "personal-brand", status: "published" } },
  { $group: { _id: "$platform", published_count: { $sum: 1 } } },
  { $sort: { published_count: -1, _id: 1 } }
])
```

Example: inspect how many provenance references each content item holds without
returning source prose:

```javascript
db.content_items.aggregate([
  { $match: { profile_id: "personal-brand" } },
  { $project: { content_id: 1, source_count: { $size: "$source_refs" } } },
  { $sort: { source_count: -1, content_id: 1 } },
  { $limit: 20 }
])
```

The platform mainly uses repository operations and MongoDB Search rather than
aggregation for its API flows. Learn aggregation because it is crucial for
analytics, diagnostics, batch migration, and future metrics analysis—not
because every request should become a pipeline.

## 22. Indexes in depth

### What an index can and cannot do

Most ordinary MongoDB indexes are B-tree-like ordered structures. They can
quickly locate values and, when index order matches the requested sort, avoid a
separate in-memory sort. Every index also adds write amplification: inserts and
updates must maintain it.

The question is not “should this field be indexed?” It is:

```text
For this exact filter + sort + cardinality + write rate, does this index reduce
total cost enough to justify its storage and write cost?
```

### Compound index order and the ESR heuristic

A useful starting heuristic is **E-S-R**:

1. Equality fields first (`profile_id = ...`, `status = ...`).
2. Sort fields next when their order can be served by the index.
3. Range fields (`created_at > ...`, `$in`, inequality) last.

It is only a heuristic. Real selectivity, query shapes, sort direction,
multikey arrays, and the query planner decide the final answer. This project’s
`content_item_profile_platform_status` index begins with profile/platform/status
because those equality filters establish ownership and a narrow feed before
the `published_at` sort.

### Index kinds you should recognize

| Index kind | Project use | Lesson |
| --- | --- | --- |
| Unique | natural keys such as `project_id` | database is the final duplicate guard |
| Compound | owner + filters + pagination sort | shape it around a concrete query |
| Multikey | `tags`, `project_ids` arrays | an array index has special compound-index constraints |
| Partial | only active skill version is unique | index only documents satisfying a filter |
| Sparse-like optional pattern | optional `external_id` uniqueness | null/missing semantics must be designed explicitly |
| TTL | not currently used | good for truly disposable data, dangerous for primary history |
| Text/Search/vector | handled by mongot here | separate from normal B-tree indexes |

Avoid redundant prefix indexes. If an index begins
`{profile_id: 1, created_at: 1, _id: 1}`, a separate `{profile_id: 1}` may be
unnecessary for a simple equality query—but verify with workload and
`explain()`, especially if a different collation or partial filter is involved.

### Covered queries and index intersection

A query is *covered* when MongoDB can answer it entirely from an index: filter,
projection, and sort all fit. It can avoid fetching the full document. This is
useful for metadata/list views, not a goal worth distorting your model for.

MongoDB can sometimes combine multiple indexes (index intersection), but one
well-chosen compound index usually beats relying on planner magic for a
critical query. Create the compound index for the query you actually run.

## 23. Explain plans and performance diagnosis

Never add an index because it “feels right.” Measure the real query against a
representative disposable dataset.

```javascript
db.content_items.find({
  profile_id: "personal-brand",
  platform: "linkedin",
  status: "published"
}).sort({ published_at: -1 }).limit(20).explain("executionStats")
```

Read these fields first:

| Signal | Good sign | Investigate when |
| --- | --- | --- |
| Winning stage | `IXSCAN`/index-supported plan where expected | `COLLSCAN` over a large collection |
| `nReturned` | matches intended page size | unexpectedly huge response |
| `totalKeysExamined` | close to useful candidates | much larger than results |
| `totalDocsExamined` | close to useful candidates | scanning most of a collection |
| Sort stage | index supplies order | blocking `SORT` for a frequent query |
| Rejected plans | sensible alternatives | planner changes or a new poor competing index |

`COLLSCAN` is not automatically bad: a tiny admin collection, an occasional
maintenance script, or a query that intentionally reads everything may be
correct. It is a warning to understand the workload, not an instruction to add
an index blindly.

For slow-operation evidence, use MongoDB logs, server metrics, and careful
profiling in a non-production environment first. Do not log document bodies or
queries containing secrets. The project’s
[`CommandMetricsListener`](../app/observability/mongo.py) records latency
metrics without making repositories responsible for instrumentation.

## 24. Consistency, replica sets, read concern, and write concern

### Replica-set vocabulary

In a multi-node replica set:

- the **primary** accepts writes;
- **secondaries** copy the primary’s oplog;
- an **election** chooses a new primary after failure;
- the **oplog** is the ordered change stream used for replication and change
  streams.

This repository configures a one-member replica set named `rs0`. That grants
transaction support and makes local behavior closer to production MongoDB, but
there is no second node to take over if the host fails.

### Write concern: when a write is acknowledged

Write concern answers “how much durability/replication acknowledgement do I
need before this write reports success?” In a multi-node system, `majority`
waits for acknowledgement by a majority of voting members. `j: true` asks for
journal durability. Higher assurance can increase latency.

Choose concern from the business consequence of losing a recently acknowledged
write, not from habit. Authentication keys, audit events, version history, and
financially relevant facts normally deserve stronger guarantees than temporary
cache-like data. The project relies on its MongoDB/driver defaults and the
replica-set transaction semantics; a production topology should make its
write-concern policy explicit and test it under failure.

### Read concern and read preference

Read concern answers “what consistency guarantee does this read require?”
Read preference answers “which replica member may answer it?” Reading from a
secondary can reduce primary load but can return stale data. Never send a
security-sensitive “just wrote it, now authorize it” workflow to an arbitrary
secondary without proving the consistency behavior.

The current single-node deployment has no secondary choice. Learn these
concepts before scaling, because they change correctness, not only throughput.

## 25. Sharding: scale-out architecture, not a tuning checkbox

Sharding distributes collections across multiple machines by a **shard key**.
It solves data/throughput that no single replica set can handle, but makes data
modeling, operations, and incident response more complex.

Before sharding, exhaust simpler work:

1. fix query/index design;
2. right-size CPU, memory, and storage;
3. use a healthy multi-node replica set;
4. measure a real bottleneck; and
5. establish backups, alerting, and recovery drills.

Shard-key questions:

| Question | Why it matters |
| --- | --- |
| Does it appear in most queries? | avoids scatter-gather queries across every shard |
| Does it have high cardinality? | spreads data instead of one hot chunk |
| Is write distribution even? | avoids a single hot shard |
| Is it stable? | shard-key changes are operationally expensive |
| Does it preserve isolation? | an ownership key can make scoped queries targeted |

`project_id` and `profile_id` are strong isolation keys but may be too low
cardinality if one tenant dominates traffic. A hashed or compound strategy is
a workload-specific decision. This project is **not sharded**; do not add a
shard key or a `mongos` router as part of ordinary V2 work.

## 26. Change streams and event-driven design

Change streams allow a client to observe inserts/updates/deletes from a replica
set or sharded cluster. They are built from the oplog and use resumable tokens.

Good uses:

- notify a UI that a long-running import completed;
- drive a separately deployed, explicitly owned derived projection;
- update a cache with a recoverable consumer;
- audit or integrate an external system with an idempotent consumer.

Bad uses:

- hiding a required synchronous write behind eventual consistency;
- replacing transaction boundaries with hope;
- an unbounded consumer with no resume-token persistence or backpressure plan;
- silently replicating private project data into an unscoped system.

The platform does not currently use change streams. Search-index synchronization
is owned by mongot. If you introduce a new consumer, document its source of
truth, resume token, retry/idempotency behavior, authorization boundary, and
failure/replay policy before writing code.

## 27. Security: database access is part of application security

Security layers in this repository:

```text
Docker internal network
  -> MongoDB authentication
  -> least-privilege database roles
  -> Pydantic + MongoDB schema validation
  -> repository scope filters
  -> API-key scopes/project/profile grants
  -> audit logs and secret-safe observability
```

Important rules:

- Use the application user, never the root user, for runtime access.
- Keep passwords in Docker secrets/files, not command lines, source, logs, or
  copied `.env` files.
- Make roles capability-specific. The schema migrator needs `collMod`; a normal
  read path should not acquire it merely because one migration does.
- Keep MongoDB/mongot ports private. If topology crosses a trusted local host
  boundary, use TLS and certificate management rather than carrying forward the
  local private-network setting.
- Treat backups as highly sensitive: they contain all documents and vectors.
- Treat retrieved content as data. A valid MongoDB record is not permission to
  expose it to another profile/project or to follow text inside it as an instruction.

Read [security.md](security.md), [deployment.md](deployment.md), and
[`app/api/security.py`](../app/api/security.py) together.

## 28. Capacity, storage engine, and operational performance

MongoDB’s WiredTiger storage engine and the operating system need memory for
working data, indexes, filesystem cache, connections, and maintenance. `mongot`
also needs substantial memory for search indexes. More indexes are not free.

Watch at least:

| Area | Symptoms | First questions |
| --- | --- | --- |
| Query latency | API slows, timeouts | Which query shape regressed? Is an index missing/unused? |
| CPU | sustained high utilization | expensive aggregation, poor plan, compression, search/index build? |
| Memory | OOM/restarts, cache pressure | working set/indexes exceed allocation? Is mongot sized separately? |
| Disk | low space, latency spikes | data/index growth, backup location, journal/headroom? |
| Connections | pool wait/timeouts | one client per process? pool bounds sensible? leaked cursors? |
| Replication lag | stale reads/failover risk | secondary health, disk/network, write burst? |
| Search index state | retrieval unavailable | is mongot healthy and are index definitions/queryable? |

The Compose file sets separate resource limits for `mongodb`, `mongot`, and
`context-api`. Treat those as explicit starting budgets, not proof of a
production capacity plan. Load test with representative document sizes, query
mix, and concurrent traffic before changing them.

## 29. A disciplined production incident loop

When a MongoDB-backed endpoint is slow or failing:

1. **Protect users first.** Respect API timeout/concurrency limits; avoid
   restarting/dropping/rebuilding blindly.
2. **Classify the dependency.** Is it API, network, authentication, mongod,
   mongot, embedding, disk, or one query shape?
3. **Collect safe evidence.** Health response, service status, structured
   latency metrics, error type, query plan on a safe representative query.
4. **Bound the blast radius.** Disable/retry a non-critical feature only with
   an explicit policy; do not weaken authorization or validators to make a
   symptom disappear.
5. **Make the smallest reversible correction.** For example, deploy a tested
   index, scale a resource, repair credentials, or roll back code.
6. **Verify end to end.** Confirm database, mongot, retrieval, API behavior,
   and audit/authorization—not only a process being alive.
7. **Write the learning down.** Add a runbook, regression test, dashboard, or
   capacity alert so the next incident is shorter.

Never use `dropDatabase`, `drop`, or `docker compose down --volumes` as a
troubleshooting reflex. Those actions can turn an availability issue into
irrecoverable data loss.

## 30. Expert lab curriculum

Complete these in order on disposable development data. For each lab, write a
short note containing the query/model choice, expected result, observed result,
and what you would change in production.

### Lab 1 — Trace one safe CRUD request

Follow project creation from `ProjectCreateRequest` to MongoDB. Identify every
validation layer, the deterministic ID, ordinary indexes, and the test that
proves duplicate behavior.

### Lab 2 — Design a new query before an index

Invent a read-only list view, for example “active projects with a tag sorted
by newest update.” Write its filter, projection, sort, limit, expected data
size, and ownership constraint. Only then propose an index and predict its
order. Use `explain("executionStats")` against disposable data to validate the
prediction.

### Lab 3 — Model bounded versus unbounded data

Compare embedded `source_refs` with separate `content_versions`. State why one
is embedded and the other referenced. Sketch how the design would fail if
versions were an unbounded embedded array.

### Lab 4 — Cause and handle a concurrency conflict

Use a unit test double or disposable record to send two updates with the same
`expected_revision`. Verify exactly one wins and that the loser receives a
conflict rather than overwriting data.

### Lab 5 — Read a transaction

Read `create_with_initial_version()` and `append_version()`. Add a temporary
test failure between the two writes in a disposable branch and prove the
transaction leaves neither a dangling version nor a current pointer to a
missing version.

### Lab 6 — Compare lexical and vector retrieval

Search an exact identifier and a paraphrased concept. Explain why lexical,
vector, and hybrid retrieval return different candidates. Inspect filter fields
in [`app/db/content_indexes.py`](../app/db/content_indexes.py) and explain how
profile isolation happens before ranking.

### Lab 7 — Perform a schema evolution dry run

On a branch, design an optional harmless field. Write the model/schema/migration
sequence without applying it to valuable data. Explain how old documents and
old application versions remain readable during rollout.

### Lab 8 — Back up and restore a disposable environment

Create a backup, verify its manifest, restore only through the documented
procedure, then reconcile search indexes. Explain why embedding vectors restore
from the archive while mongot indexes rebuild.

### Lab 9 — Capacity review

Read the Compose resource settings and inventory current collections/indexes.
Estimate which document types grow with users, conversations, skills, versions,
and search. Choose two metrics/alerts before increasing a resource limit.

## 31. Official learning path

Use this project guide for context and the official MongoDB Manual for the
broader database platform. Work through these topics in sequence:

1. [MongoDB Manual](https://www.mongodb.com/docs/manual/) — shell, BSON, CRUD,
   operational vocabulary.
2. [Data modeling](https://www.mongodb.com/docs/manual/data-modeling/) — embed,
   reference, schema design, and document-size constraints.
3. [Indexes](https://www.mongodb.com/docs/manual/indexes/) and
   [query plans](https://www.mongodb.com/docs/manual/reference/explain-results/)
   — index selection and `explain` analysis.
4. [Aggregation](https://www.mongodb.com/docs/manual/aggregation/) — pipelines
   and performance constraints.
5. [Transactions](https://www.mongodb.com/docs/manual/core/transactions/) and
   [replication](https://www.mongodb.com/docs/manual/replication/) — consistency
   and failure semantics.
6. [Sharding](https://www.mongodb.com/docs/manual/sharding/) — only after you
   understand replica sets and real capacity evidence.
7. [Change streams](https://www.mongodb.com/docs/manual/changeStreams/) and
   [security](https://www.mongodb.com/docs/manual/security/) — event consumers
   and least privilege.

For this repository’s self-managed search component, keep
[mongot.md](mongot.md), [retrieval.md](retrieval.md), and
[embeddings.md](embeddings.md) open alongside the official Search/Vector Search
documentation for the version you deploy.
