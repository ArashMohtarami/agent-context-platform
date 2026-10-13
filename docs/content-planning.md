# Novelty checks and repurposing plans

The planning services provide planning evidence around content that already exists. It does
not call an LLM, generate a draft, create a version, publish anything, or
declare that a topic is objectively novel. Creator content and retrieved text
remain data, not instructions.

## Advisory novelty checks

`POST /api/v2/content/novelty` requires `content:read`, a profile grant, and a
project grant when `project_id` is supplied. It searches the existing
content history through the `content_history` retrieval profile, then compares
normalized title/tag/pillar terms (topic) and descriptive terms (angle).

```json
{
  "profile_id": "personal-brand",
  "title": "Monitoring Outbox delivery lag",
  "description": "Explain alerting on lag and exhausted retries rather than implementation.",
  "tags": ["outbox", "payments"],
  "project_id": "payment-service",
  "method": "hybrid",
  "limit": 10
}
```

The result is explicitly `advisory: true`. Its classifications are
`same-topic-same-angle`, `same-topic-new-angle`, `related-topic`, and
`new-topic`; they are stable planning labels, not truth claims. Each visible
match retains its identifier, kind, platform/status, source references, shared
terms, and topic/angle overlap. A history record that references a project the
caller cannot access is discarded before comparison, so its scores and
existence cannot leak.

## Repurpose plans

`POST /api/v2/content/{id}/repurpose` takes a profile, `targets`, and an
optional `source_kind` (`content`, `brief`, or `idea`; `content` is the
default). It first loads and authorizes that stored source, then returns a
read-only plan. It accepts LinkedIn, Medium, and GitHub targets today; adding a
platform is localized to `ContentPlatform` and its structural planning
metadata.

The plan contains a core thesis, bounded source excerpts, explicit source
provenance, examples that are present in the source, target-specific fields to
shorten or expand, structural destination differences, the reviewed canonical
platform-writing skill, and visible related history. It contains no target
`body`, no generated draft, and no persistence operation. A one-sentence
source is deliberately represented by a stable evidence label rather than
returned verbatim as a purported plan.

The content skill selection is trusted-active-only. Its canonical Git-backed
Markdown file remains the writing guidance; platform metadata in the service
only describes output structure and does not generate prose.

## MCP and CLI

The thin MCP server calls the same API routes only:

| Tool | Scope | API |
| --- | --- | --- |
| `check_content_novelty` | `content:read` | `POST /api/v2/content/novelty` |
| `prepare_repurpose_plan` | `content:read` | `POST /api/v2/content/{id}/repurpose` |

`contextctl content novelty` and `contextctl content repurpose` are equally
thin API clients. Neither reads MongoDB, computes an overlap, generates a
draft, nor writes content locally.

## Persistence and recovery

Novelty and repurposing results are read-only planning data. It reuses `content_ideas`, `content_briefs`, `content_items`, and the
existing retrieval/backup/restore path. Saving a resulting idea or draft is a
separate, explicit write operation.
