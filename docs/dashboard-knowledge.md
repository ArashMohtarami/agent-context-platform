# Knowledge workspace (V6 Phase 11)

The console provides a project directory/details/create flow, revision-aware
project context, intentional shared memory, canonical skill metadata/versions,
and budgeted unified search. It calls session-only adapters under
`/api/v6/organizations/{org_id}/knowledge`. Existing V1 services/repositories
remain the domain implementation. No internal HTTP request or machine credential
is used to authenticate a human.

Every adapter requires the enabled console, a verified current human session,
matching active organization, and an exact action. Mutations also require current
MFA, Origin, CSRF, and the existing policy/session transaction fences. Repository
queries, pagination/cursors and search candidates use the same tenant and
per-action predicates used by managed machine paths. A directory/management role
does not grant knowledge access. Read-A and write-B remain separate assignments.

| Adapter | Required action and resource |
| --- | --- |
| `GET /projects`, `GET /projects/{project_id}` | `projects:read` + `project_directory` |
| `POST /projects` | `projects:write` + requested `project_directory` |
| `GET /context`, `GET /context/{context_id}` | `context:read` + exact project |
| `POST /context`, `PATCH /context/{context_id}` | `context:write` + exact project; PATCH requires expected revision |
| `GET /memories`, `GET /memories/{memory_id}` | `memory:read` + exact project or explicit organization knowledge |
| `POST /memories`, `PATCH /memories/{memory_id}` | `memory:write` + same scope/resource; PATCH requires expected revision |
| `GET /skills`, `GET /skills/{skill_id}` | `skills:read` + imported skill; optional exact version/chunk cursor |
| `POST /search` | `search:read` intersected with each source's `context:read`, `memory:read`, or `skills:read` resource selector |

List/chunk pages are bounded to 50 records, default 20. Status and supported
skill filters narrow existing repository queries before pagination. Search uses
the existing query/source/filter validation, 50-result maximum and configured
token-budget clamp. A supplied project/skill filter needs that search resource.
NONE never becomes ALL. Cursors retain tenant/filter/action-policy binding.
Denied/foreign records do not contribute candidate totals or citations.

All four memory scopes retain their existing tenant/client addresses. The human
workspace exposes **global** (organization-global) and **project** memory only.
It never accepts a client identity or derives one from sponsorship, `owner_id`,
team visibility or management rights. Requests for **agent**/**project_agent**
memory fail closed; those scopes remain available through their verified machine
identity and explicit memory policy. Memory creation is intentional. Supersession
uses the existing same-scope service and preserves the prior record, content and
links; archive uses optimistic concurrency. It retains the existing service's
ordered-write/retry behavior, rather than claiming a new atomic history system.

Context preserves its current revision, actor and promotion provenance. Existing
APIs do not store historical context text snapshots, so the UI does not advertise
such history. On conflict, the draft stays in component memory. The user loads
and compares the current revision, then explicitly chooses to save against it;
there is no automatic overwrite or retry. Session/policy/org changes still clear
the old context and sensitive drafts. Skill versions/chunks are stored imports of
canonical Git; the workspace has no server file editor or skill mutation route.

Search reports actual source, trust (where the source has a trust classification),
source path/version/hash, actor and revision where available, citation key,
retrieval methods and returned token budget. Human search uses the existing
retrieval pipeline without the opt-in query-text retrieval logger. Terms, prompts,
transcripts and result bodies are not added to workforce monitoring or audit.
Process-local retrieval counters remain operational observations.

Markdown is rendered with pinned `react-markdown`, without raw-HTML plugins.
An explicit element allowlist excludes images/embedded content; links accept only
absolute HTTP(S) URLs without credentials/control characters. Links have
`noopener noreferrer` and no referrer. HTML, scripts, event attributes, unsafe
schemes and relative/protocol-relative action links are inert. No remote image
is fetched. Source content is never assigned to `innerHTML`.

## Verification

The requested filenames are used directly:

```sh
python -m pytest -q tests/unit/test_v6_knowledge_adapters.py
python -m pytest -q tests/unit/test_projects_api.py tests/unit/test_context_api.py tests/unit/test_memory_api.py tests/unit/test_search_api.py tests/unit/test_hybrid_search_api.py tests/unit/test_memory_service.py tests/unit/test_retrieval_filters.py
V6_KNOWLEDGE_ADAPTERS_ONLY=1 bash scripts/test-v6-isolation.sh
npm --prefix dashboard run test:unit -- src/knowledge/search.test.tsx src/knowledge/memory.test.tsx
npm --prefix dashboard run build
npm --prefix dashboard run test:e2e -- e2e/knowledge.spec.ts
```

The disposable runner selects exactly
`tests/integration/test_v6_knowledge_adapters.py`. It provisions a real MongoDB
replica set and mongot, with matching logical IDs and different private sentinel
content in two organizations. Synthetic deterministic 3D embeddings exercise the
real production lexical/vector candidate pipelines; they are not a relevance or
embedding-quality benchmark. Human fixtures use persisted browser sessions and
independently approved operator bootstrap/recovery contracts, never a superuser
HTTP token. A separate normal recipient-issued machine credential compares V1
behavior with the human adapters.

Browser verification uses the built SPA, real HTTPS API and disposable provider
infrastructure described in [the foundation](dashboard-foundation.md). Its
private filesystem fixture channel prepares data and concurrent edits; there are
no fixture endpoints in the product. The tests cover safe Markdown, project
creation, conflict recovery, memory supersession, skill history, delayed search
across organization switch, direct API denials and both themes at
360/768/1440px, keyboard and text zoom. Screenshots and infrastructure logs are
local verification artifacts, excluded from public release inventories. Browser
engine/version limitations must be recorded with the actual run evidence.
