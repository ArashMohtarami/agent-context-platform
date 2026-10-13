# Content discovery and brief preparation

Content discovery assembles bounded, reviewable inputs for an agent. It does not call an
LLM, write an idea automatically, generate body prose, decide novelty, publish
anything, or change V1 engineering retrieval.

`ContentDiscoveryService` reads the existing V1/V2 repositories through
profile- and project-bound adapters. Its output is structured data: exact topic
labels from stored records, source references, source excerpts, recency data,
matching published content, and stored creator/pillar preferences. An agent
decides whether to turn that data into prose and must explicitly save any idea.

## Discovery

`POST /api/v2/content-ideas/discover` requires `content:read` and a creator
profile grant. `project_id` is optional, but project context is inspected only
when it is supplied and authorized. Without it, discovery can consider only
global memory, trusted active skills, profile-owned published content, and
profile pillars; it does not enumerate every V1 project.

```json
{
  "profile_id": "personal-brand",
  "project_id": "payment-service",
  "pillar_id": "payments",
  "platform": "linkedin",
  "time_range": {"start_at": "2026-09-01T00:00:00Z"},
  "limit": 10
}
```

The response contains candidates with source IDs, related project/pillar,
suggested platforms, deterministic usefulness labels, similar published items,
and inclusive update-time freshness. It excludes exact-title duplicates among
the authorized profile's unpublished ideas and returns those retained ideas so
the caller can see why they were not proposed. Time filtering is applied at the
repository query and again to every evidence record before candidate assembly.

A project grant is not a relevance grant: a project record becomes a candidate
only when it matches the requested pillar, another active pillar, or stored
creator interests. This prevents an authorized but unrelated project note from
being suggested as content.

Private provenance is never broadened: published records with a project are
included only if all their project IDs are the explicit, authorized project.
Candidates are separated by source family and project boundary so the displayed
IDs can be preserved when an agent elects to save one reviewed idea.

## Brief preparation

`POST /api/v2/content-briefs/prepare` requires `content:read`. It accepts a
saved idea and a destination platform, verifies the profile plus both explicit
and idea-origin project grants, and returns a non-persistent brief package:
objective, stored audiences, primary message, supporting points, source refs,
matched trusted technical skills, one trusted platform-writing skill, similar
published records, and constraints derived from stored preferences.

The package always includes `do not fabricate project experiences` and tells an
agent to ground claims in the listed source references. Creator preferences are
labels in returned data, not hidden system instructions.

The existing `POST /api/v2/content-briefs` remains the explicit `content:write`
operation for persisting a reviewed brief. The `/prepare` route never
creates a `content_briefs` document.

## MCP and CLI

The MCP server remains API-only and has no MongoDB credentials:

| Tool | Scope | API |
| --- | --- | --- |
| `discover_content_ideas` | `content:read` | `POST /api/v2/content-ideas/discover` |
| `save_content_idea` | `content:write` | `POST /api/v2/content-ideas` |
| `search_content_ideas` | `content:read` | existing `POST /api/v2/content/search` with `content_ideation` and server-side `idea` kind filtering |
| `prepare_content_brief` | `content:read` | `POST /api/v2/content-briefs/prepare` |

`contextctl content ideas`, `contextctl content idea create`, and
`contextctl content brief` are equally thin API clients. They do not access
MongoDB, rank records, or generate content locally.

## Persistence and recovery

Discovery uses the existing creator/content collections and full-database
backup workflow; it does not persist its preparation results automatically. Startup idempotently reconciles five normal MongoDB
recency indexes for active skills, project context, memory scopes, ideas, and
published items; these are derived database indexes, not another persistence
schema. All source provenance remains in the existing
`ContentOrigin`/`ContentSourceReference` fields.
