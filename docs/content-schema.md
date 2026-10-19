# Creator and content schema

The creator/content system stores structured preferences, ideas, briefs, drafts,
versions, provenance, and metrics. See [creator.md](creator.md) for profile/pillar
interfaces, [content-storage.md](content-storage.md) for lifecycle APIs, and
[content-retrieval.md](content-retrieval.md) for current-content retrieval.
It has no content generation or autonomous strategy analysis; metrics remain descriptive data only. Content agents
remain responsible for writing prose; this platform stores structured data,
provenance, revisions, and lifecycle state.

## Collections

| Collection | Profile ownership | Purpose |
| --- | --- | --- |
| `creator_profiles` | `profile_id` | Communication preferences as data, never hidden instructions |
| `content_pillars` | `profile_id` | Long-lived themes and priority |
| `content_ideas` | `profile_id` | Candidate topics and initial origin |
| `content_briefs` | `profile_id` | Platform-specific evidence plan |
| `content_items` | `profile_id` | Current working copy of a content item |
| `content_versions` | `profile_id` | Append-only snapshots for a content item |
| `content_links` | `profile_id` | Provenance relations to source knowledge |
| `content_metrics` | `profile_id` | Timestamped, generic metric observations |
| `content_campaigns` | `profile_id` | Coordinated sets of related content |

All records carry an `_id`, UTC `created_at` and `updated_at` timestamps, and
a positive `revision`. The named record types use deterministic BSON ObjectIds
from their profile and public IDs; versions additionally include
`(content_id, version)`. MongoDB strict validators and normal indexes mirror
the Pydantic models. Standard index initialization is idempotent. Derived content
Search and Vector Search indexes are described in [content-retrieval.md](content-retrieval.md).

Content repositories are bound to exactly one profile for every read, list,
write, and revision-guarded update. A caller cannot use a profile-scoped
repository to read or mutate a different profile's data. `content_versions` and
`content_metrics` intentionally expose append-and-list storage only, preserving
history rather than enabling accidental overwrites.

## Vocabulary and provenance

The shared content lifecycle values are `idea`, `selected`, `researching`,
`brief_ready`, `drafting`, `review`, `approved`, `published`, and `archived`.
The supported platform enum contains `linkedin`, `medium`, and `github`; adding a
future destination such as YouTube, X, Reddit, a newsletter, or a blog is
localized to the platform enum and MongoDB validator rather than platform
branches in storage code.

`content_links` accepts `derived_from`, `mentions`, `explains`, `documents`,
`updates`, and `contrasts`. Briefs and content items also record source
references with `source_type`, `source_id`, and optional `project_id`. That
keeps project context identifiable as data. The
[publication review service](content-publication-safety.md) uses this provenance
when assessing a stored draft; provenance alone is not permission to publish.

## Migration and recovery

Migration 6 creates or reconciles these nine strict collections and their
validators. It is additive: no V1 collection is renamed or structurally
changed. Its rollback is deliberately refused because dropping content history
or provenance would lose authored records.

Migration 7 adds an empty-default `tone_preferences` field to creator profiles,
an empty-default profile allow-list to API keys, and an optional profile target
in audit records. It safely backfills only those defaults before reconciling
the affected strict validators. An empty key allow-list preserves the V1
meaning of access to every profile for that historical schema. V6 adoption
records it as explicit `all_in_org` in the selected organization; new managed
NONE policy is distinct and never serialized as an empty legacy ALL array.

Backups already dump the complete configured MongoDB database and record a
collection-count manifest. Consequently the new collections are included in
new backups and verified restores automatically; no separate content database
or backup path exists. Managed restore verification compares each collection count and required
metadata, including these V2 collections, in isolated temporary storage. A
combined count alone cannot establish preservation.

Migration 12 backfills the existing `content_metrics` collection with a `source` (default `manual`) and an empty `provider_metadata` object, then restores strict validation. Captures remain append-only and use the existing profile/content/platform/captured-at index; it adds no collection, external analytics dependency, or V1 change. The API and MCP boundary are documented in [content-metrics.md](content-metrics.md).

Creator/profile routes use `creator:read` and `creator:write`; content
storage uses `content:read` and `content:write`. Publication requires the
separate `content:publish` scope. Allowed content mutations record
`content.write` audit events. Migration
8 adds that action to the existing strict audit validator; no V1 route or
document structure changes. Migration 9 adds nullable embedding metadata only
to current ideas, briefs, and content items, preserving immutable
`content_versions`; its derived Mongo Search/Vector Search indexes and current
representation policy are documented in [content-retrieval.md](content-retrieval.md).
Existing V1 routes and their authorization behavior remain unchanged.

## Research-backed content briefs and fact review

Research evidence can be linked into the existing `content_briefs` collection.
A brief may retain a `research_project_id`,
frozen `claim_refs`, frozen `citation_refs` (including source class, excerpt,
source snapshot, and locator), and `factual_review_status`.

`factual_review_status` is one of `not_reviewed`, `verified`, or
`needs_review`. `verified` has a deliberately limited meaning: every reviewed,
mapped claim was explicitly stored as `supported` and had a supporting
citation at review time. It is not a guarantee that a draft is true or
complete.

`POST /api/v2/content/{content_id}/fact-review` reviews a saved draft against
a selected research project (or the research project stored on its brief). A
caller may provide claim IDs or claim text. When neither is supplied, the
service identifies only normalized exact occurrences of already stored claim
statements in the draft. Semantic retrieval and retrieval scores never label a
claim as factual support.

The result exposes the supported, unsupported, contested, and outdated counts;
per-claim findings; citation references and locators; and missing evidence.
Partially supported and unreviewed material remains visible as evidence that
still needs review. Unsupported, contested, outdated, and missing evidence are
never hidden.

The endpoint requires V2 profile authorization, the normal V1 grants for every
project attached to the draft and brief, and the V1 grants linked to the V3
research project. It is read-only: it does not rewrite a draft, advance its
lifecycle, publish it, update claim states, promote research to V1 memory or
context, or change canonical skills. MCP exposes the same protected operation
as `review_content_facts`; it has no MongoDB access.

Migration 20 backfills legacy content briefs with `null` `research_project_id`,
empty reference lists, and `not_reviewed`, then restores strict validation.
No collection, database, vector store, or backup target is added: the complete
MongoDB backup already includes the extended `content_briefs` collection.
