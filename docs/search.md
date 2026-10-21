# Unified knowledge search

All runtime records and search candidates described here belong to one trusted
organization. `global` means organization-global, with explicit action/resource
policy; it never means a null-org or cross-installation corpus. Imported skills
are tenant-owned copies of canonical Git. Private agent memory also retains
verified client ownership and is not revealed by human management visibility.


```
POST /api/v1/search
```

One question, every source that can answer it. Skills, a project's own recorded context, and
memory are searched together, ranked against each other, and cut to a token budget — rather than
asked three times and concatenated by the caller.

The individual skill, context, and memory search endpoints remain available. They remain the right
tool when a caller wants exactly one kind of knowledge, or when it needs the stored record rather
than retrieved context. This is the interface for the case that matters most: a question, and the
context that answers it.

## The pipeline

```
query
  │
  ▼  normalize          whitespace collapsed; identifiers, casing and symbols untouched
  ▼  select sources     a source not asked for is never searched
  ▼  embed              one query vector, reused by every vector search
  ├──────────────┬──────────────┐
  ▼ skills       ▼ context      ▼ memory        each searched lexically and by vector,
  └──────────────┴──────────────┘                all concurrently
  ▼  filter             project isolation, sources, tags, importance, lifecycle
  ▼  rank               the shared ranking formula, across requested sources
  ▼  deduplicate        a result a better-ranked one already covers is dropped
  ▼  token budget       filled in rank order, nothing truncated
  ▼  context builder
  ▼  compact response   content, source metadata, score, and retrieval metadata
```

Every stage is the one the individual searches already use: `RetrievalService` runs the searches,
`app/retrieval/` ranks and budgets what they return. A unified answer therefore cannot rank
differently from the endpoint it replaces — there is one ranking implementation, not two.

## Sources

| Source | What it is | Priority | Reachable |
| --- | --- | --- | --- |
| `project_context` | What a project recorded about itself | 1.00 | Inside its own project |
| `active_skill` | Curated skill chunks, carrying their skill's metadata | 0.95 | Everywhere |
| `memory` | Eligible shared and authenticated client-operational memory | 0.80 | Per [memory.md](memory.md) |
| `conversation` | Stored, but carries no index yet | 0.50 | Not retrievable |

Naming no source searches everything retrievable. `sources` narrows that, and does so twice: a
source that was not asked for is never searched, **and** is refused again at the filter, so one of
the two failing cannot put it in an answer.

A conversation can be asked for and is answered honestly rather than rejected: conversations are
stored separately but carry no vector and no search index, so `conversation`
always appears under `skipped_sources`. Conversation records are not
automatically embedded or extracted into memory.

`active_skill` covers both "skills" and "skill chunks": a skill is retrieved as the chunk that
answers the question, carrying the metadata of the skill version it came from.

## Filters

| Filter | Applies to | Behaviour |
| --- | --- | --- |
| `project_id` | Everything | The isolation boundary; see below |
| `sources` | Everything | Which kinds of knowledge may answer |
| `tags` | Everything | The result must carry every requested tag |
| `minimum_importance` | Everything | Skills score neutrally at 0.5 |
| `active_only` | Everything | Default `true`; excludes archived and superseded |
| `phrase` | Everything | The query words must appear together, in order |
| `context_types` | Project context | One or more of the fourteen types |
| `memory_types` | Memory | `preference`, `convention`, … |
| `memory_scopes` | Memory | Eligible subset of `global`, `project`, `agent`, `project_agent` |
| `skill_id` / `skill_version` | Skills | One skill, or one exact version |
| `category` / `trust_level` | Skills | Skill metadata |

`skill_id`, `skill_version`, `category`, and `trust_level` describe a skill. No context entry and
no memory carries one, so naming any of them makes the request a request about skills: the other
sources are reported as skipped rather than searched, returning candidates that the same filter
would then discard. Skipping them says what happened; searching them would read exactly like both
sources being empty.

`memory_scopes` narrows the eligible scopes and cannot widen ownership. Project
scopes require an explicit authorized project; agent scopes require a linked
authenticated client. Defaults include global memory, the named project's memory
when present, and that client's own operational scopes when authenticated. See
[memory.md](memory.md).

## Project isolation

Unified search preserves both project and client ownership:

- Global knowledge is shared with callers holding the required grants.
- Knowledge owned by a project is visible only to that project.
- A request naming no project cannot retrieve project context or project memory.
- Agent memory is visible only to its authenticated client; naming a project
  also makes that client's own project-agent memory eligible.
- Research and creator-content collections use their dedicated search workflows;
  this endpoint searches core skills, context, and memory.

It is enforced three times over, and unified search deliberately re-states it rather than assuming
the sources enforced it: each repository is bound to its scope when it is built, each search
pre-filters on `project_id` inside the query, and the service checks every candidate again before
ranking. A result belonging to another project is dropped even if a search returns it.

## Retrieval metadata

Every response carries a `retrieval` block. It is not behind `explain`, because an answer drawn
from several sources is only debuggable if the caller can see which ones contributed:

```json
{
  "query_id": "0123456789abcdef0123456789abcdef",
  "retrieval_method": "hybrid",
  "requested_sources": ["project_context", "active_skill", "memory"],
  "searched_sources": ["active_skill", "memory"],
  "skipped_sources": ["project_context"],
  "latency_ms": 18.41,
  "diagnostics": {
    "methods": ["vector", "lexical"],
    "weights": {"vector": 0.45, "lexical": 0.25, "...": 0.0},
    "vector_matches": 6, "lexical_matches": 3, "merged": 7,
    "filtered_out": 1, "duplicates_removed": 1, "ranked": 5,
    "dropped_for_budget": 2
  }
}
```

`skipped_sources` is what turns an empty answer into an explanation: a source contributes nothing
either because it found nothing, or because no query ran against it at all. `retrieval_method` is
`hybrid` when both searches ran and names the survivor when only one did, so a deployment running
without an embedding model is visible in its own output rather than indistinguishable from a
healthy one.

Per-result score breakdowns stay behind `explain: true`. They are the part that grows with the
answer; the block above is a fixed size.

## Results

```json
{
  "key": "project_context:payment-service:6720a1b2c3d4e5f60718293a",
  "source": "project_context",
  "title": "Payment idempotency strategy",
  "content": "Every charge request carries a UUID4 idempotency key. …",
  "score": 0.8343,
  "tokens": 28,
  "metadata": {"context_id": "…", "project_id": "payment-service", "type": "architecture_decision"}
}
```

`key` identifies a result stably across queries, so a caller can say which result it used and a
retrieval log can record the same thing. `metadata` carries the source's own fields — a skill's id,
version, section and trust level; a context entry's type and importance; a memory's scope,
confidence, and both ends of any supersession.

Nothing is unbounded: `limit` is at most 50, the token budget is clamped to the deployment's
maximum, and content is never truncated to fit — a result that does not fit is left out whole.

## Retrieval logging

Every search is reported to a hook when it finishes. `app/search/logging.py` defines the entry and
the protocol; a hook that fails is logged and skipped, because retrieval logging exists to explain
searches and must never be the reason one fails.

Two hooks exist:

| Hook | Records | Default |
| --- | --- | --- |
| `StructuredRetrievalLogger` | One `retrieval_completed` log line | Always on |
| `PersistentRetrievalLogger` | One `retrieval_logs` document | `RETRIEVAL_LOG_ENABLED` |

The structured line carries `query_id`, `project_id`, `retrieval_method`, `requested_sources`,
`result_count`, and `latency_ms` — and deliberately **not** the query text. That is the one field in
a search that can describe the person who ran it, and application logs are shipped, indexed, and
retained by whatever collects stdout.

Storing it is therefore opt-in. With `RETRIEVAL_LOG_ENABLED=true` each search also writes one
`retrieval_logs` document holding query, project, requested sources,
result count, selected result keys, latency, and method. Results are referenced by key rather than
copied, so the record stays small and reading it back cannot become a second way to read the
knowledge base. The document is written inside the project scope the search ran in.

A finished search also tells the call it is part of what it did — the query identifier, the project,
and the result count — so the one HTTP access line for that request carries them too, and records
`acp_retrieval_duration_seconds` and `acp_retrieval_results`. Each index query underneath is timed
separately as `acp_search_duration_seconds{method,collection}`, and a query that could not be
served is counted as `acp_search_failures_total{method,collection}`: vector and lexical apart,
because a rebuilding vector index takes semantic search down while lexical search keeps answering.
See [observability.md](observability.md).

## API

```
POST /api/v1/search
{
  "query": "How do we prevent duplicate payment requests?",
  "project_id": "payment-service",
  "sources": ["project_context", "active_skill", "memory"],
  "memory_scopes": ["global", "project"],
  "context_types": ["architecture_decision"],
  "tags": ["payments"],
  "minimum_importance": 0.5,
  "token_budget": 4000,
  "limit": 10,
  "explain": false
}
```

`200` returns the budgeted context. `503` means the search infrastructure or the embedding model
could not serve the query, with a message that exposes no internals. `422` rejects a malformed
request — an empty query, a limit or budget outside its range, a repeated filter value, a version
without its skill, or project memory without a project.

## Reaching it from an agent

`search_knowledge` is the MCP tool over this endpoint, and the one an agent should reach for
first. See [mcp.md](mcp.md).

## Verification

```bash
python -m pytest                                     # unit coverage, no MongoDB required
docker compose --profile test run --rm --build test  # live mongod + mongot checks
```

The live checks seed one skill, two projects' context, and both scopes of memory on known vector
axes and with deliberately disjoint vocabulary, then assert that one query reaches all three
sources ranked together; that a semantic query with no word in common still retrieves the right
context on vector evidence alone; that `INSUFFICIENT_SCOPE`, `wallet:read`, `/api/v1/payments` and
`OAuth2` each retrieve the chunk containing them despite the query vector pointing elsewhere; that
no query reaches another project while each project reaches its own; that a smaller budget removes
whole results and alters none of the ones it keeps; that restricting sources restricts what is
searched; that repeating a query returns identical scores; and that an opted-in deployment stores
exactly one record per search.
