# Retrieval

Retrieval has three modes: **vector** search for conceptual similarity, **lexical**
search for exact technical vocabulary, and **hybrid** retrieval, which runs
both, combines their scores, and returns a token-budgeted context. Vector and lexical search are
served by self-managed `mongot`; all three resolve the same metadata filters.

**Unified knowledge search** is the interface built on top of them: `POST
/api/v1/search` runs this pipeline across eligible core knowledge sources and is documented in
[search.md](search.md). This page documents the machinery it is made of.

This core pipeline retrieves three collections. **Skill chunks** are global
knowledge available to permitted readers. **Project context** belongs to one
authorized project; [projects.md](projects.md) describes its isolation rules.
**Memory** supports shared global/project records and authenticated agent or
project-agent records; [memory.md](memory.md) explains ownership and supersession.
Creator-content and research retrieval use separate domain workflows.

## Topology

```
Context API ──► mongod ──► mongot ──► vector index + search index
```

The application issues `createSearchIndexes`, `$listSearchIndexes`, `$vectorSearch`, and `$search`
to `mongod`, which coordinates with `mongot`. No application code opens a connection to `mongot`,
and no `mongot` endpoint is configured anywhere in the application settings. No other search
engine is involved.

## Indexes

Six core-knowledge search indexes exist — one Vector Search and one Search index over each
core collection — reconciled by the same primitives in `app/db/search_indexes.py`.

### Vector index

| Property | Value |
| --- | --- |
| Name | `skill_chunk_vector_index` |
| Type | `vectorSearch` |
| Path | `embedding` |
| `numDimensions` | The dimensions of the loaded embedding model |
| `similarity` | `cosine` |
| Filter fields | `skill_id`, `skill_version` |

Dimensions have exactly one source: the model the embedding provider actually loaded. Startup
loads the model, reads `EmbeddingModelInfo.dimensions` from it, and passes that to
`ensure_vector_indexes()`. Because the embedding provider refuses to load a model whose real dimensions
differ from `EMBEDDING_DIMENSIONS`, the configured value, the stored vectors, and the index can
never disagree. There is no separate vector-dimension setting to drift.

Cosine matches the normalized unit vectors the provider produces. Changing it requires evidence
from retrieval evaluation, not preference.

### Search index

| Property | Value |
| --- | --- |
| Name | `skill_chunk_text_index` |
| Type | `search` |
| Mapped fields | `content`, `section`, `skill_id`, `skill_version` |
| Analyzers | `lucene.standard` on `content` and `section`; `identifier_exact` on `content.exact` |
| Filter fields | `skill_id`, `skill_version` (indexed as `token`) |

`content` is indexed twice. The standard analyzer handles ordinary prose and punctuation, so
`Keycloak.` at the end of a sentence still matches `Keycloak`. The `identifier_exact` multi-field
uses a whitespace tokenizer with a lowercase filter, so `wallet:read`, `/api/v1/payments`,
`payment-service`, and `INSUFFICIENT_SCOPE` survive tokenization as single terms rather than being
split at every symbol. Matching is case-insensitive; structure is not.

`dynamic: false` keeps vectors and internal fields out of the text index.

### Project context indexes

| Property | Value |
| --- | --- |
| Names | `project_context_vector_index`, `project_context_text_index` |
| Vector path | `embedding`, cosine, dimensions from the loaded model |
| Mapped text | `content` (with the same exact multi-field), `title` |
| Filter fields | `project_id`, `type`, `status`, `tags`, `importance` |

A context document carries its own metadata, so every filter — project isolation included — is a
pre-filter on the same document rather than a scope resolved from another collection. The title is
indexed at a 1.5 boost: it states what the entry is about, which is a stronger signal than a
section heading inside a longer document.

The analyzer, the exact multi-field, and the query shape are shared with skill chunks, so one
lexical vocabulary governs both collections.

### Memory indexes

| Property | Value |
| --- | --- |
| Names | `memory_vector_index`, `memory_text_index` |
| Vector path | `embedding`, cosine, dimensions from the loaded model |
| Mapped text | `content` (with the same exact multi-field), `summary` |
| Filter fields | `scope`, `project_id`, `client_id`, `type`, `status`, `tags`, `importance` |

Memory search unions the eligible scopes in its pre-filter: shared global
records, the explicitly named project's records, and the authenticated client's
own agent/project-agent records where applicable. Each branch retains its
project and client ownership conditions. The summary is boosted at 1.5: it
states the memory in one line, which matches a question more directly than the
body it condenses.

Managed search intersects the search action with each source's read action.
Impossible NONE branches are removed without widening their AND/OR predicate.
ALL resource identifiers use a nonempty string range inside mongot, equivalent
to their strict identifier schema; they do not enumerate every corpus ObjectId.
Both [vector prefilters](https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-stage/)
and [lexical string ranges](https://www.mongodb.com/docs/search/query/operators-collectors/range/)
retain the explicit organization condition. More complex provenance predicates
still use bounded ID preselection and refuse work beyond that bound. Retrieved
ownership and action/resource access are checked independently afterward.

## Index reconciliation

`ensure_vector_indexes()` and `ensure_text_indexes()` are safe to run on every startup and every
import:

1. Absent index: created.
2. Identical definition: left untouched.
3. Different path, dimensions, similarity, analyzer, or mapped fields: updated in place, which
   makes an embedding-model migration a configuration change rather than a manual operation.

Only the parts of a definition this application controls are compared, so defaults MongoDB adds to
a stored definition never cause a pointless rebuild on the next startup.

A concurrent creation race resolves to "unchanged". Every failure to reach the search coordinator
raises `SearchUnavailableError`, which startup logs as `vector_index_unavailable` without stopping
the process; `/health/ready` then reports the platform as not ready.

Search indexes are not part of the migration history and are not backed up: they are derived state
that can always be rebuilt from the stored vectors.

## Filters

`$vectorSearch` can only pre-filter on fields indexed on the same collection, and chunks carry no
skill metadata. The retrieval service therefore resolves metadata filters against the `skills`
collection first and passes the resulting `(skill_id, skill_version)` keys into the query:

```
category / tags / trust_level / active  ──► skills ──► version keys ──► $vectorSearch filter
```

Vector search pre-filters inside the approximate search, which keeps `limit` results reachable;
filtering after a k-nearest-neighbour query would silently return fewer results than requested.
Lexical search applies the same keys as `compound.filter` clauses, which restrict what is eligible
without contributing to relevance. Supported filters:

| Filter | Behaviour |
| --- | --- |
| `skill_id` | One skill |
| `skill_version` | One exact version; requires `skill_id` |
| `category` | Exact match |
| `tags` | Skill must carry every requested tag |
| `trust_level` | `trusted`, `user`, `external`, or `generated` |
| `active_only` | Default `true`; ignored when `skill_version` is given |

Requesting an explicit version targets that version even when it is superseded, which is the same
rule the skill detail endpoint follows. Beyond 200 matching versions the pre-filter stops being
selective, so the query runs unfiltered and over-fetches instead; every result is verified against
the resolved set either way, so a result outside the filter can never be returned.

## Lexical relevance

A lexical query becomes one `compound` operator whose clauses each contribute score:

| Clause | Path | Boost | Purpose |
| --- | --- | --- | --- |
| `text` | `content.exact` | 3.0 | The identifier matched exactly as written |
| `phrase` | `content` | 2.0 | The words appeared together, in order |
| `text` | `content` | 1.0 | Ordinary prose matching |
| `text` | `section` | 0.5 | The heading mentioned the term |

An exact identifier match is the strongest signal lexical search has, so it carries the largest
boost. These are deliberate starting weights for retrieval evaluation, not optimal values; hybrid ranking weights are configurable.

Setting `phrase: true` promotes the phrase clause from optional to required, so only chunks
containing the words together, in order, match at all. Without it a phrase still boosts the
chunks that contain it, while chunks containing only the individual words remain eligible.

The query text reaches the analyzers exactly as written apart from whitespace normalization:
casing, colons, slashes, dots, and underscores are preserved, because destroying them is what
makes `wallet:read` unfindable.

## Results

Both modes return the same model, so a caller handles one shape regardless of how a result was
found:

```json
{
  "source": "skill_chunk",
  "method": "lexical",
  "score": 2.848,
  "skill": {"skill_id": "fastapi-production", "version": "1.0.0", "category": "backend", "..." : "..."},
  "chunk": {"chunk_id": "chunk-0000", "section": "Rules", "content": "..."}
}
```

`method` distinguishes a cosine similarity (roughly 0..1) from a lexical relevance score
(unbounded, comparable only within one query). The two are never mixed in this shape; combining
them is what hybrid retrieval does, after normalizing both.

Vectors are never returned to clients. Internal BSON identifiers and revisions are not exposed.

`numCandidates` for vector search is derived from the requested limit:
`limit × VECTOR_SEARCH_CANDIDATE_MULTIPLIER`, clamped by `VECTOR_SEARCH_MAX_CANDIDATES` and by
MongoDB's own ceiling, and never below `limit`.

## Hybrid retrieval

Vector search finds what a question means; lexical search finds what it literally says. Neither is
sufficient alone: a vector query for `wallet:read` drifts to whatever is conceptually nearby, and a
lexical query for "how do we avoid charging twice" matches nothing. Hybrid retrieval runs both and
combines them.

```
query ──┬─► vector search ──┐
        └─► lexical search ─┴─► merge ─► filter ─► rank ─► deduplicate ─► token budget ─► context
```

Both searches run concurrently over the same resolved metadata scope, so hybrid retrieval costs
roughly one round trip rather than two.

### Module boundaries

`app/retrieval/` holds the algorithm and imports nothing from `app/db/` or `app/repositories/`:

| Module | Responsibility |
| --- | --- |
| `query.py` | Query normalization, the first stage of every pipeline |
| `types.py` | The candidate, score, and diagnostics value objects |
| `filters.py` | What a request may see, including project isolation |
| `ranking.py` | Normalization, the weighted score, deterministic ordering |
| `hybrid.py` | Merging the two result sets and removing duplicates |
| `context_builder.py` | Filling the token budget and shaping the result |

`RetrievalService` is the only piece that knows about both. It splits into two halves that compose:
`gather_sources()` resolves each source's scope and runs both searches against each one
concurrently, returning source-agnostic candidates; `rank_context()` performs no I/O at all and
turns candidates into budgeted context. `hybrid_search()` and unified knowledge search are both
those two calls in sequence, which is why one ranking implementation serves both. Ranking can
therefore be exercised, and retrieval quality evaluated, without MongoDB running.

### Score normalization

The two scores are not comparable as they stand, so each is normalized against what it means:

| Score | Range | Normalization |
| --- | --- | --- |
| Vector | 0..1 (cosine, reported by MongoDB) | Clamped; it already carries an absolute meaning |
| Lexical | Unbounded, query-specific | Divided by the best score of its own source's search |

Relative scaling is used for lexical scores rather than min-max, because min-max always reports
the weakest result as a total mismatch and inflates differences that carry no meaning. Dividing by
the best score preserves the ratios between results.

Lexical scores are scaled **within each source**. A score reflects the statistics of the index it
came from, so a skill chunk's relevance and a context entry's relevance only become comparable
after each is scaled against the best result of its own search; scaling them together would rank
one collection's index statistics against another's. Cosine similarity needs no such treatment:
the same model and metric produce it in both collections, so it is comparable as it stands.

A result only one search found contributes nothing from the other, so agreement between the two
searches ranks above a single strong hit. When a search did not run at all — no embedding model is
loaded — its weight is redistributed over the remaining signals instead, because scoring every
result against a signal that cannot exist would only compress the scores.

### Ranking

```
final_score = 0.45 * vector_score
            + 0.25 * lexical_score
            + 0.15 * importance_score
            + 0.10 * recency_score
            + 0.05 * source_priority
```

| Weight | Setting | Default |
| --- | --- | --- |
| Vector similarity | `RETRIEVAL_WEIGHT_VECTOR` | 0.45 |
| Lexical relevance | `RETRIEVAL_WEIGHT_LEXICAL` | 0.25 |
| Importance | `RETRIEVAL_WEIGHT_IMPORTANCE` | 0.15 |
| Recency | `RETRIEVAL_WEIGHT_RECENCY` | 0.10 |
| Source priority | `RETRIEVAL_WEIGHT_SOURCE_PRIORITY` | 0.05 |

Weights express relative influence and are normalized before use, so any configuration keeps the
final score inside 0..1 and comparable between queries. These are starting values for retrieval
evaluation, not measured optima.

Source priority encodes that project-specific knowledge should normally beat generic knowledge:

| Source | Priority |
| --- | --- |
| `project_context` | 1.00 |
| `active_skill` | 0.95 |
| `memory` | 0.80 |
| `conversation` | 0.50 |

Skills, project context, and memory are wired up; a conversation is stored but carries no index,
so nothing retrieves one yet. Asking for it is answered rather than rejected: unified search
reports it under `skipped_sources`. Project
context is the reason the source term exists: at equal relevance it beats an active skill, which
is what "project-specific information should normally beat generic knowledge" means in practice.
Memory sits below both — useful across sessions, but less authoritative than what a project
recorded about itself.

Importance is a curator-assigned value. Project context and memory carry a real one, chosen by
whoever stored the entry; skills carry none, so every skill chunk scores neutrally and the term
separates one entry from another within those sources. Recency halves every
`RETRIEVAL_RECENCY_HALF_LIFE_DAYS` (default 90) from the document's `updated_at` — for a skill,
when that version was imported; for context or memory, when it was last edited.

A memory's `confidence` is stored and returned but is not a ranking term: adding a sixth signal to
the documented formula is a change that should follow retrieval evaluation, not precede it.

Ranking is deterministic. The reference time is passed in rather than read from a clock, scores are
summed in a fixed order and rounded before they are compared, and ties are broken by source
priority, then an active version over a superseded one, then newer content, then the stable
candidate key. The same inputs in any order produce the same output.

### Deduplication

After ranking, a result is dropped when a better-ranked result already covers its content: the
share of the shorter result's words that also appear in the longer one reaches
`RETRIEVAL_DUPLICATE_OVERLAP` (default 0.85). Because the better-ranked copy is the one kept, and
ranking already prefers active and newer versions, the surviving copy is the one worth keeping.

The chunker deliberately repeats 50-100 tokens between adjacent chunks. That overlap is roughly a
tenth of a chunk, far below the threshold, so genuinely different neighbours both survive while a
chunk contained in another — or the same section reachable through two skill versions — is
returned once. Results shorter than a dozen distinct words are only removed when they repeat
exactly, since short fragments share common words by chance.

### Token budget

The platform stores a lot and retrieves little. `RETRIEVAL_TOKEN_BUDGET` (default 4000) is filled
in rank order and `RETRIEVAL_MAX_TOKEN_BUDGET` (default 16000) is the ceiling a request may ask
for: a caller can request less context, never more than the deployment allows.

A result that does not fit is skipped and a smaller later one may still be included, so one
oversized result cannot waste the budget. Content is never truncated: half a technical rule reads
like a whole one, which is worse than omitting it. A budget smaller than the largest matching chunk
therefore returns fewer results, or none, by design.

### Explainable metadata

Every result carries its score breakdown — each normalized signal, the raw scores, and which
searches found it — and every response carries pipeline counters: how many matches each search
returned, how many merged, how many the filters removed, how many were duplicates, and how many
were dropped for budget. This stays internal by default and is returned only when a request asks
for it, so a surprising ranking is reproducible instead of a guess without making every response
larger.

## API

```
POST /api/v1/skills/search/vector
{
  "query": "How do we prevent duplicate payment requests?",
  "category": "backend",
  "tags": ["payments"],
  "trust_level": "trusted",
  "active_only": true,
  "limit": 5
}
```

The response reports the active embedding model and dimensions alongside the ranked results.

```
POST /api/v1/skills/search/lexical
{
  "query": "wallet:read",
  "phrase": false,
  "category": "security",
  "limit": 5
}
```

Lexical search needs no embedding model, so it keeps working with `EMBEDDING_ENABLED=false` and
while the model is still loading. Both endpoints return `503` when their infrastructure cannot
serve the query, with a message that exposes no internals, and `422` for a malformed request.
`POST /api/v1/search` combines eligible core knowledge sources; these three remain for callers that
want exactly one kind of knowledge or one retrieval method.

```
POST /api/v1/skills/search/hybrid
{
  "query": "How do we prevent duplicate payment requests?",
  "project_id": "payment-service",
  "category": "backend",
  "minimum_importance": 0.0,
  "token_budget": 4000,
  "limit": 10,
  "explain": false
}
```

The response is the budgeted context rather than raw search results:

```json
{
  "query": "How do we prevent duplicate payment requests?",
  "project_id": "payment-service",
  "token_count": 900,
  "token_budget": 4000,
  "results": [
    {
      "source": "active_skill",
      "title": "Payment Service: Idempotency",
      "content": "...",
      "score": 0.9225,
      "tokens": 300,
      "metadata": {"skill_id": "payment-service", "skill_version": "1.0.0", "...": "..."}
    }
  ]
}
```

`explain: true` fills in the `explanation` on each result and the `diagnostics` on the response;
both keys are always present and null otherwise, so the response shape never varies. Hybrid
retrieval degrades to its lexical half with `EMBEDDING_ENABLED=false`, reporting the weighting it
actually applied.

`POST /api/v1/skills/search` remains a literal metadata/content search: it matches escaped
literal terms with regular expressions over skill metadata and chunk content, needs no search
index, and returns no scores. It remains useful when `mongot` is unavailable.

## Project isolation

Runtime skills and chunks are tenant-owned imports without a `project_id`; their
organization-global policy is explicit, not an installation-shared fallback. Memory can also belong to an
authenticated client. The filters preserve these ownership boundaries on every
candidate:

- Organization-imported skills and organization-global memory require their explicit permitted resources.
- Content owned by a project is visible only to that project.
- A request naming no project cannot retrieve project context or project memory.
- An authenticated client can retrieve only its own agent-scoped memory.
  Project-agent memory also requires its explicit authorized project.

The rule is applied three times over, because one layer failing must not become a leak: the
repository is bound to trusted organization/project, the search pre-filters on `org_id` and action-specific resources
inside the query, and the service checks every candidate again before ranking. None of the three
trusts the caller to have scoped the query correctly. [projects.md](projects.md) and
[memory.md](memory.md) document the rest of the isolation contract.

## Readiness

`/health/ready` reports both indexes:

```json
{
  "vector_search": {
    "enabled": true,
    "available": true,
    "index": "skill_chunk_vector_index",
    "queryable": true,
    "index_status": "READY",
    "dimensions": 384,
    "dimensions_match": true
  },
  "lexical_search": {
    "available": true,
    "index": "skill_chunk_text_index",
    "queryable": true,
    "index_status": "READY",
    "analyzer": "lucene.standard"
  }
}
```

Two more blocks, `context_search` and `memory_search`, report the project-context and memory
index pairs the same way; see [projects.md](projects.md) and [memory.md](memory.md).

## Retrieval logging

Every unified search reports what it did to a hook when it finishes: always to the structured
log, and — with `RETRIEVAL_LOG_ENABLED=true` — to the `retrieval_logs` collection as well. The
query text never reaches the application log. [search.md](search.md) documents both hooks and
why storing a query is opt-in.

Readiness fails while any index is missing, still building, or unreachable, and while a vector
index was built for a different number of dimensions than the loaded model. With
`EMBEDDING_ENABLED=false` no provider is built and vector search stops being a required
dependency; the Search indexes are still required, because lexical retrieval does not depend on
the model.

## Verification

```bash
python -m pytest                                   # unit coverage, no MongoDB required
docker compose --profile test run --rm --build test  # live mongod + mongot checks
```

The live checks build both indexes, wait for `mongot` to report them queryable, and then assert
ranking, every supported filter, top-K bounds, score ordering, and idempotent reconciliation.
Vector coverage inserts chunks whose vectors point along known axes. Lexical coverage inserts
chunks containing `Keycloak`, `OAuth2`, `wallet:read`, `INSUFFICIENT_SCOPE`, `/api/v1/payments`,
`payment-service`, `PostgreSQL`, `Redis`, `Saga`, `outbox`, `PaymentIdempotencyService`,
`FastAPI`, and `MONGODB_REPLICA_SET`, then asserts that each one retrieves the chunk that contains
it.

Hybrid coverage points the query vector at one chunk's axis and puts the technical identifiers in
another, so a result can only reach the top on evidence the ranking actually combined: it asserts
that agreement wins, that an exact identifier lifts a chunk above a closer vector match, that the
token budget bounds what comes back, and that repeating a query against live indexes returns
identical scores. Project-context coverage asserts that neither a context search nor hybrid
retrieval reaches another project's entries, and that a request naming no project reaches none of
them at all. Memory coverage asserts the same for project memory, and that global memory reaches
every request.
