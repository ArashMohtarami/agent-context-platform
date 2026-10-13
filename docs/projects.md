# Projects and project context

A **project** is one software system or repository. **Project context** is the durable technical
knowledge that belongs to it: architecture decisions, business rules, API conventions, known
issues. Skills are global knowledge that applies anywhere; context is what is true about one
system in particular, which is why it outranks skills during retrieval.

## Project isolation

Every read, write, and search preserves this isolation boundary.

- Context belongs to exactly one project and can never be moved.
- A repository is bound to its project when it is built, so no call site can widen the scope.
- Every read, write, and search is restricted to that project, and a query naming a different
  project is refused rather than quietly answered.
- Retrieval applies the rule again on every candidate, so a result that escaped a backend filter
  still cannot be returned.
- A request naming no project reaches no project-owned context at all: context must be asked for
  explicitly and is never inherited by omission.
- A context item that exists in another project is reported as "not found", the same as one that
  does not exist. One project must not learn what another stores, including whether an identifier
  is in use.

There is no API-user authentication yet, so the caller states which project it is asking about.
The enforcement is still ours: the query is *scoped* by the stated project rather than looked up
unscoped and checked afterwards. When API keys arrive they constrain which projects a caller may
name, without changing any of the rules above.

## Projects

| Field | Meaning |
| --- | --- |
| `project_id` | Stable identifier every context item is filed under |
| `name` | Human-readable system name |
| `description` | What the system is |
| `repository` | Where its code lives |
| `environment` | The environment this boundary describes |
| `tags` | Free-form labels, unique within a project |

The identifier is the boundary itself, so it cannot be taken twice: a repeat create reports a
conflict rather than merging two systems' knowledge under one name. `_id` is derived from
`project_id`, so the uniqueness is structural rather than a check that could be skipped.

## Context

| Field | Meaning |
| --- | --- |
| `project_id` | The owning project. Identity, not content: it can never be edited |
| `type` | One of the fourteen supported context types |
| `title` | What the entry is about |
| `content` | The knowledge itself |
| `importance` | 0..1, assigned by whoever stored it; a ranking signal |
| `tags` | Free-form labels used for filtering |
| `source` | Where the entry came from |
| `status` | `active`, `archived`, or `superseded` |
| `external_id` | Optional idempotency key. Identity, so it cannot be edited |

Supported types: `architecture`, `architecture_decision`, `business_rule`, `api`, `database`,
`infrastructure`, `deployment`, `security`, `integration`, `convention`, `troubleshooting`,
`known_issue`, `implementation`, `constraint`.

Context is written for a project that already exists; context belonging to no project would be
unreachable and unattributable, so the write is refused instead.

### Lifecycle

Status is how knowledge stops being current without being destroyed. Retrieval returns `active`
context by default; archiving or superseding an entry removes it from answers while keeping the
record and its history. The public lifecycle API archives or supersedes context
rather than deleting it.

### Idempotency

Supplying an `external_id` makes a create repeatable: the second write returns what is already
stored rather than failing or duplicating. That is what makes an interrupted import safe to run
again. The identifier derives the document `_id` together with the project, so two projects can
use the same key without colliding.

### Updates

`PATCH` changes only the fields it names. Changing the title or the body re-embeds the document,
because a vector that no longer describes the stored text would quietly return the wrong thing.
Changing only metadata leaves the vector alone.

`expected_revision` makes a lost update visible: if the item changed after it was read, the write
is refused with a conflict instead of overwriting someone else's edit. Omitting it uses the
revision just read, which still detects a concurrent write but cannot detect one that happened
before the client decided what to send.

## Retrieval

Context is embedded from its title and body together — a vector built from the body alone loses
the framing a question is most likely to match — and indexed twice, exactly as skill chunks are:
a vector index for meaning and a Search index for exact technical vocabulary.

Because a context document carries its own metadata, every filter is a pre-filter on the same
document: project, type, status, tags, and a minimum importance all restrict the search itself
rather than trimming its results afterwards.

Two entry points return context:

- `POST /api/v1/context/search` searches one project's context only.
- `POST /api/v1/search` ranks that project's context alongside every other source it may see;
  see [search.md](search.md).
- `POST /api/v1/skills/search/hybrid` adds that project's context to skill retrieval when the
  request names a project, ranking both together.

Ranking is described in [retrieval.md](retrieval.md). The part that matters here is source
priority: `project_context` scores 1.00 against an active skill's 0.95, so project-specific
knowledge beats generic knowledge when both are equally relevant.

## API

```
POST /api/v1/projects
GET  /api/v1/projects
GET  /api/v1/projects/{project_id}

POST  /api/v1/context
GET   /api/v1/context?project_id=…
GET   /api/v1/context/{context_id}?project_id=…
PATCH /api/v1/context/{context_id}?project_id=…
POST  /api/v1/context/search
```

Creating a project or a context item returns `201`. A taken project identifier returns `409`, as
does a stale `expected_revision`. Context for an unknown project returns `404`, and so does
context that exists in a different project. A malformed request returns `422`.

The item endpoints take `project_id` as a required query parameter because the documented paths
address a context item by its own identifier alone. Without it there would be nothing to scope
the query by, and scoping is what makes isolation enforceable rather than merely checked.

Responses never include vectors, BSON identifiers, or internal fields. `revision` is returned so a
client can send it back as `expected_revision`.

```
POST /api/v1/context
{
  "project_id": "payment-service",
  "type": "architecture_decision",
  "title": "Payment idempotency strategy",
  "content": "Payment requests carry a UUID4 idempotency key…",
  "importance": 0.95,
  "source": "user",
  "tags": ["payments", "idempotency"],
  "external_id": "payment-idempotency"
}
```

```
POST /api/v1/context/search
{
  "project_id": "payment-service",
  "query": "how do we prevent duplicate charges?",
  "types": ["architecture_decision"],
  "tags": ["payments"],
  "status": "active",
  "minimum_importance": 0.5,
  "limit": 10
}
```

## Readiness

`/health/ready` reports both context indexes:

```json
{
  "context_search": {
    "enabled": true,
    "vector_available": true,
    "vector_index": "project_context_vector_index",
    "vector_queryable": true,
    "vector_dimensions_match": true,
    "text_available": true,
    "text_index": "project_context_text_index",
    "text_queryable": true
  }
}
```

Readiness fails while the Search index is missing, building, or unreachable. With
`EMBEDDING_ENABLED=false` the vector half stops being required and context stays lexically
retrievable, exactly as skills do.

## Verification

```bash
python -m pytest                                     # unit coverage, no MongoDB required
docker compose --profile test run --rm --build test  # live mongod + mongot checks
```

The live checks create projects and context, read them back, update them, exercise the
idempotency key, and then assert that neither a context search nor hybrid retrieval can reach
another project's entries — including that a request naming no project reaches none of them.
