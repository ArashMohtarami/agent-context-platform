# Content metrics

The metrics workflow stores content performance observations as generic, append-only
data in the existing MongoDB `content_metrics` collection. It does not depend on
an analytics provider: the supported workflow is an explicit manual capture.

## What one observation contains

Every observation is owned by a creator profile and one existing content item:

- `metric_id` is unique within the profile and deterministically derives the BSON `_id`.
- `content_id` and `platform` identify the observed representation.
- `captured_at` is the time the values were observed.
- `metrics` is a non-empty map of non-negative finite numeric values.
- `source` defaults to `manual`; it records provenance only and never triggers a provider call.
- `provider_metadata` is optional descriptive metadata; it does not invoke an integration.
- Standard timestamps, a revision, tags, and an active/archived lifecycle field are retained.

Common metric names are `views`, `impressions`, `likes`, `comments`,
`shares`, `clicks`, `bookmarks`, and `followers_gained`. They are not an
exhaustive vocabulary. Generic keys let a provider expose a useful, reviewed
measurement without a schema rewrite.

The API rejects empty maps, invalid metric names, boolean values, negative
numbers, infinities, and NaN. It also rejects a duplicate profile/metric ID.
Metrics are immutable observations: correcting a value means recording a new
capture with a new ID, not overwriting history.

## API and access controls

`POST /api/v2/content/{content_id}/metrics` requires `content:write`.
`GET /api/v2/content/{content_id}/metrics` and the `get_content_metrics`
MCP tool require `content:read`.

Both routes require access to the profile and, before returning or writing any
metric, to every project referenced by the content item. The list endpoint
supports `platform`, `captured_after`, `captured_before`, `limit`, and
an opaque `cursor`; captures are ordered newest-first by `captured_at`.

The metrics service has no analytics client and does not mutate creator
profiles, voice, content pillars, or content. It produces no strategy insight
or engagement optimization. Any later insight feature must be an explicitly
reviewed, advisory layer.

## Persistence and recovery

Migration 12 adds `source` and `provider_metadata` to the already-existing
`content_metrics` collection. It first loosens only those required fields,
backfills legacy captures as `source: "manual"` with empty metadata, then
restores strict validation. It is idempotent and does not modify any V1
collection.

The existing backup workflow uses `mongodump` for the entire application
database and records per-collection counts in its manifest. Therefore it
already includes `content_metrics` and continues to restore it without a
separate content database or backup rule. Standard MongoDB indexes include the
profile/content/platform/captured-at time-series query path.

