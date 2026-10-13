# Content storage and lifecycle

The content storage API keeps manually supplied ideas, briefs, drafts, immutable
versions, and provenance under profile and project grants. Agents write the
content; the platform stores and validates it. Dedicated retrieval, planning,
review, export, and publication workflows build on this storage layer.

## REST API

Every storage endpoint below is under `/api/v2`, requires a `content:read` or
`content:write` scope, and takes an explicit `profile_id` in its query or
request body. The profile must be allowed by the API key. Any project named by
an origin, source reference, or content `project_ids` also needs the key's
project grant before it is returned or stored.

| Method | Path | Scope | Purpose |
| --- | --- | --- | --- |
| `GET` | `/content-ideas?profile_id=…` | `content:read` | Cursor-page a profile's ideas |
| `POST` | `/content-ideas` | `content:write` | Create an initial `idea` |
| `PATCH` | `/content-ideas/{idea_id}` | `content:write` | Revision-guard idea metadata or lifecycle |
| `POST` | `/content-briefs` | `content:write` | Store an evidence-backed brief |
| `GET` | `/content-briefs/{brief_id}?profile_id=…` | `content:read` | Read one authorized brief |
| `POST` | `/content` | `content:write` | Create a draft plus immutable version 1 |
| `GET` | `/content/{content_id}?profile_id=…` | `content:read` | Read one item, including archived items |
| `PATCH` | `/content/{content_id}` | `content:write` | Revision-guard metadata/lifecycle, never the body |
| `POST` | `/content/{content_id}/versions` | `content:write` | Append a body or rollback as a new version |
| `GET` | `/content/{content_id}/versions?profile_id=…` | `content:read` | Cursor-page immutable history after provenance checks |
| `POST` | `/content/search` | `content:read` | Search current content representations |
| `POST` | `/content/{content_id}/export` | `content:read` | Validate and return a manual representation without state change |

The idea list uses the platform's opaque `created_at`/`_id` cursor and limits
from 1 to 50. Repository-level content and version listing likewise retains
archived records. Retrieval covers current ideas, briefs, and items;
its API, indexes, privacy behavior, and MCP tools are documented in
[content-retrieval.md](content-retrieval.md).

## Lifecycle and immutable versions

New ideas start at `idea`; new content items always start at `drafting`.
The lifecycle progresses through `selected`, `researching`, `brief_ready`,
`drafting`, `review`, `approved`, `published`, and `archived`. Approval uses the
dedicated review/approval operation; publication uses the separate
`content:publish` receipt operation. A normal PATCH cannot bypass these steps.
See [publication safety](content-publication-safety.md) and
[manual publishing](content-publishing.md). A transition from `published` to `drafting` is rejected by a
normal PATCH. An explicit version request is the revision workflow: it creates
a new draft snapshot while keeping the previously published snapshot unchanged.

Content bodies cannot appear in `PATCH /content/{id}`. Creating a content item
writes `content_items` and `content_versions` version 1 in one MongoDB
transaction. A later version request writes the next immutable snapshot and
updates the current copy in the same transaction, using the item's
`expected_revision`. A stale request returns a conflict rather than creating a
partial history entry.

To roll back, send `rollback_to_version` instead of `body` to the version
endpoint along with `expected_revision`, `change_reason`, and optionally
`created_by`. The platform copies the historical body into a *new* latest
version. It never edits or deletes the earlier version.

`source_refs`, `project_ids`, tags, and source links remain explicit data.
They are not instructions and are not included automatically in exported prose.
`content_links` and `content_campaigns` have profile-isolated service support
for manually supplied provenance and grouping data; their public REST workflows
remain internal service operations because the public REST contract does not
define link or campaign endpoints.

Export formats an authorized current item for manual use, but does not
write a version, alter its lifecycle, set a public URL, or publish it. Its
platform-specific representations are described in
[content-export.md](content-export.md).

## Audit and migration

Allowed mutations record the `content.write` audit action with the profile ID,
HTTP method, and route, never the content body. Migration 8 widens only the
existing strict `audit_logs` validator to accept that additive action. It does
not alter V1 documents or any V2 content document shape. Existing V2 content
collections, indexes, and backup/restore coverage remain those created by
Migration 6.
