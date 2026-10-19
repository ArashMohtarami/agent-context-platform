# Memory

All runtime records and search candidates described here belong to one trusted
organization. `global` means organization-global, with explicit action/resource
policy; it never means a null-org or cross-installation corpus. Imported skills
are tenant-owned copies of canonical Git. Private agent memory also retains
verified client ownership and is not revealed by human management visibility.


Memory keeps useful facts across tasks and sessions: a preference, convention,
correction, or operational note. Writes are intentional; the platform does not
watch conversations or extract memories automatically. Reviewed research
candidates can be promoted through a separate, explicitly authorized
[research workflow](research.md).

## Scope and isolation

| Scope | Project ownership | Client ownership | Visibility |
| --- | --- | --- | --- |
| `global` | None | None | Shared with callers holding the required read grants |
| `project` | One project | None | Shared within that authorized project |
| `agent` | None | Authenticated client | That client's operational memory |
| `project_agent` | One project | Authenticated client | That client's operational memory within the authorized project |

For writes and item/list reads, omitting `scope` preserves shared behavior:
no project means `global`; naming a project means `project`. Agent scopes are
explicit opt-ins. Their client identity comes from the credential, never from
an arbitrary request field. A legacy API key without a linked client cannot use
agent scopes.

Search combines the eligible scopes unless the caller narrows them. A request
without a project can retrieve global memory and its own agent memory. A request
with an authorized project can also retrieve shared project memory and its own
project-agent memory. It never reaches another project's records or another
client's operational memory.

The repository binds to scope/project/client ownership, search pre-filters the
same fields, and retrieval checks candidates again before ranking. A record
outside the addressed ownership boundary is reported as not found. Global
memory is shared knowledge, so use an agent scope for client-operational notes.

## The record

| Field | Meaning |
| --- | --- |
| `memory_id` | The memory's own identifier |
| `scope` / `project_id` / `client_id` | Shared or client-operational ownership; client identity is server-derived |
| `content` | The memory itself |
| `summary` | The memory in one line, where one is useful |
| `type` | What kind of memory it is, e.g. `preference`, `convention` |
| `importance` | 0..1, assigned by whoever stored it; a ranking signal |
| `confidence` | 0..1, how sure the writer was |
| `tags` | Free-form labels used for filtering |
| `source` | Where it came from |
| `status` | `active`, `archived`, or `superseded` |
| `supersedes` / `superseded_by` | Both ends of a correction |
| `external_id` | Optional idempotency key |
| `actor` / `promotion_provenance` | Authenticated writer and reviewed research origin, where applicable |
| `created_at` / `updated_at` / `revision` | Written by the storage layer |

## Lifecycle

`active` memory is retrieved. `archived` memory is kept but no longer answers
questions, and archiving is reversible. `superseded` memory has been replaced
by a specific, named memory.

The public lifecycle API retains archived and superseded memory instead of deleting it.

## Superseding

When new information conflicts with old, the old memory is superseded rather
than edited or removed:

```
POST /api/v1/memory  {"content": "Deploys never happen on Fridays…", "supersedes": "<old id>"}

old memory  →  status: superseded, superseded_by: <new id>
new memory  →  status: active,    supersedes:    <old id>
```

Both ends of the link are stored, so the chain reads in either direction and
the decision's history survives the correction. The old content is untouched.

Two rules protect the chain:

- A memory that has already been superseded cannot be superseded again. A
  second replacement would overwrite the existing link and drop a correction
  out of the history, so it is refused with a conflict.
- A memory can only supersede one in its own scope, because the replaced memory
  is looked up inside that scope. One project cannot correct another's memory,
  and global memory cannot be corrected by a project.

The two writes are ordered deliberately: the replacement is stored first, then
the old memory is marked. If the second write loses a race, both memories are
still stored and retrievable and the operation reports that the link was not
written — the reverse order would leave a link pointing at nothing and drop the
old knowledge out of retrieval. Supplying an `external_id` makes the retry
safe: the repeat returns the memory already stored instead of creating a second
one.

## Updates

`PATCH` changes only the fields it names. Changing the content or the summary
re-embeds the memory, because a vector that no longer describes the stored text
would quietly return the wrong thing. Changing only metadata leaves it alone.

Scope, the idempotency key, and both ends of a supersession link are identity
rather than content, so none of them can be edited: a memory never moves
between projects and never loses its history. The model also refuses to make a
replaced memory active again while it still points at its replacement.

`expected_revision` makes a lost update visible: if the memory changed after it
was read, the write is refused with a conflict instead of overwriting someone
else's edit.

## Retrieval

Memory is embedded from its summary and content together and indexed twice — a
vector index for meaning and a Search index for exact vocabulary — exactly as
skill chunks and project context are.

Memory's source priority is **0.80**: below project context (1.00) and an
active skill (0.95), above a conversation (0.50). At equal relevance, what a
project recorded about itself beats what was remembered in general.

Confidence is stored and returned but is not a ranking term. The ranking
formula is documented in [retrieval.md](retrieval.md). Evaluate retrieval
quality before adding or retuning ranking signals.

Three entry points return memory:

- `POST /api/v1/search` is the unified interface: memory is one of the sources
  it ranks together, and `memory_scopes` narrows which of them it reaches. See
  [search.md](search.md).
- `POST /api/v1/memory/search` searches memory alone, over the scopes the
  request may see.
- `POST /api/v1/skills/search/hybrid` adds memory to skill and context
  retrieval, ranking them together.

Archived and superseded memory is excluded by default, which is what makes
superseding a correction rather than a duplicate.

## API

```
POST  /api/v1/memory
GET   /api/v1/memory?project_id=…&scope=…
GET   /api/v1/memory/{memory_id}?project_id=…&scope=…
PATCH /api/v1/memory/{memory_id}?project_id=…&scope=…
POST  /api/v1/memory/search
```

Creating a memory returns `201`. Superseding an already-replaced memory returns
`409`, as does a stale `expected_revision`. A memory for an unknown project, or
one that is not in the named scope, returns `404`. A malformed request returns
`422`.

The item endpoints accept optional `project_id` and `scope` query parameters.
Use the same scope/project address used to create the record. Agent scopes
add the authenticated client identity automatically. A request cannot move a
memory to another scope or impersonate another client.

Responses never include vectors or internal fields. `revision` is returned so a
client can send it back as `expected_revision`.

```
POST /api/v1/memory
{
  "type": "preference",
  "content": "Use strict typing in every Python project, checked with mypy.",
  "summary": "Strict typing everywhere",
  "importance": 0.8,
  "confidence": 0.95,
  "source": "user",
  "tags": ["python"],
  "external_id": "strict-typing"
}
```

```
POST /api/v1/memory/search
{
  "query": "how do we handle payment retries?",
  "project_id": "payment-service",
  "scopes": ["global", "project"],
  "types": ["convention"],
  "status": "active",
  "minimum_importance": 0.5,
  "limit": 10
}
```

## Readiness

`/health/ready` reports both memory indexes under `memory_search`, in the same
shape `context_search` uses. Readiness fails while the Search index is missing,
building, or unreachable. With `EMBEDDING_ENABLED=false` the vector half stops
being required and memory stays lexically retrievable.

## Verification

```bash
python -m pytest                                     # unit coverage, no MongoDB required
docker compose --profile test run --rm --build test  # live mongod + mongot checks
```

The live checks store global and project memory, read them back, archive and
restore one, supersede a conflicting memory and assert both ends of the link,
refuse a second supersession, and then assert that neither a memory search nor
hybrid retrieval can reach another project's memory — while global memory remains shared with permitted readers. Agent-scope
authorization tests verify authenticated ownership and deny client spoofing.
