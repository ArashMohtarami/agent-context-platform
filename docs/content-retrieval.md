# Content retrieval

Content retrieval makes the current representations in `content_ideas`,
`content_briefs`, and `content_items` searchable. It reuses the existing local
embedding provider, MongoDB Search, MongoDB Vector Search/mongot, and the pure
hybrid-ranking primitive. It does not alter V1 engineering retrieval, generate
prose, discover ideas, repurpose content, or publish anything.

## What is indexed

Migration 9 adds nullable embedding metadata to ideas, briefs, and current
content items: `embedding`, `embedding_model`, `embedding_dimensions`, and
`embedding_generated_at`. It first backfills `null` values and then reconciles
the strict validators, making it safe to apply to a populated development
database. `content_versions` is deliberately unchanged: version history stays
append-only and is never turned into a duplicate vector corpus.

The content service embeds the current idea and brief representation on write.
It embeds an item only while its current status is `drafting`, `approved`, or
`published`; a transition to a non-meaningful current representation removes
that item's stale vector. An archived item remains lexically searchable. Body
changes create a new immutable version and re-embed only the new current copy.

Startup reconciles six derived indexes idempotently:

| Collection | Lexical index | Vector index |
| --- | --- | --- |
| `content_ideas` | `content_idea_text_index` | `content_idea_vector_index` |
| `content_briefs` | `content_brief_text_index` | `content_brief_vector_index` |
| `content_items` | `content_item_text_index` | `content_item_vector_index` |

Every index has `profile_id` as a pre-filter. The applicable platform, project,
status, pillar, and tag fields are also filters, so MongoDB restricts the
candidate set before scoring rather than leaking or losing recall after search.
Normal MongoDB embedding-model indexes support later re-embedding work.

## API and authorization

`POST /api/v2/content/search` requires `content:read` and an explicit
`profile_id`.

```json
{
  "profile_id": "personal-brand",
  "query": "What have I written about idempotency?",
  "kinds": ["content"],
  "retrieval_profile": "content_history",
  "method": "hybrid",
  "platform": "linkedin",
  "project_id": "payments-api",
  "status": "published",
  "pillar_id": "payments",
  "tags": ["idempotency"],
  "token_budget": 1200,
  "limit": 10
}
```

`method` is `lexical`, `vector`, or `hybrid`; `hybrid` is the default. Optional
`kinds` is a unique subset of `idea`, `brief`, and `content`; omitting it keeps
the existing all-current-representations search. The
response includes the result type, current text, score, methods, token count,
and retained provenance (`project_ids` and `source_refs`). The caller needs a
profile grant and, where supplied, the requested project grant. A broad search
also silently removes any result whose recorded project provenance is not
allowed by the caller's project grant. This avoids confirming that private
project-derived material exists.

`GET /api/v2/content/{content_id}/versions?profile_id=…` is a read-only,
cursor-paginated history endpoint used by the MCP history tool. It applies the
same profile and current-item provenance checks before returning snapshots.

## Retrieval profiles

The V2-only profiles are `content_creation`, `content_history`,
`content_ideation`, and `content_repurposing`. They use separate content
ranking weights, so tuning them does not change V1 engineering ranking.

The configurable initial `content_creation` priorities are:

| Source | Default |
| --- | ---: |
| `project_context` | 1.00 |
| `memory` | 0.95 |
| `technical_skill` | 0.85 |
| `content_skill` | 0.85 |
| `published_content` | 0.70 |
| `conversation` | 0.40 |

They are exposed through `CONTENT_CREATION_PRIORITY_*` settings. The content
search endpoint searches only content collections. Cross-source discovery and
brief preparation use their own explicit workflows; these priorities do not
widen this endpoint or change engineering search. See
[content-discovery.md](content-discovery.md).

## MCP

The thin, read-only MCP tools call the API only:

| Tool | Scope | API |
| --- | --- | --- |
| `search_content` | `content:read` | `POST /api/v2/content/search` |
| `get_content` | `content:read` | `GET /api/v2/content/{id}` |
| `get_content_history` | `content:read` | `GET /api/v2/content/{id}/versions` |

Their prompt-facing renderings fence every retrieved body and label it as data,
not instructions. MCP holds no MongoDB credential, repository, vector query,
or ranking logic.

## Recovery

The new vectors stay in the existing MongoDB collections. Existing complete
database backup, restore, and manifest-count verification therefore include
them automatically; no content database or vector store is introduced.
