# Creator studio (V6 Phase 13)

The console adds creator preferences and pillars, evidence discovery, idea
planning, novelty checks, repurposing plans, content-history search, research
briefs, supplied drafts, immutable versions and line comparisons, independent
review, manual exports/receipts and manually captured metric observations.
Creator profiles are communication/corpus boundaries, separate from human
roles and V5 agent permission templates. A person or calling agent supplies
prose; there is no autonomous writer or publishing-provider integration.

Session-only adapters live under
`/api/v6/organizations/{org_id}/creator`. They call the existing V2/V3 services
and repositories directly, without a privileged machine token. Console
activation still requires the earlier verified isolation, identity and policy
cutovers. No new dependencies, collections or migration versions are added.

| Operation | Exact current authority |
| --- | --- |
| Creator preferences and pillars | `creator:read` / `creator:write` on the creator profile; metadata permission does not reveal corpus content |
| Ideas, briefs, drafts, versions, metrics, discovery, novelty, history and repurposing | `content:read` / `content:write` for the profile and every transitive linked project/source; unlinked knowledge and skills retain their explicit resource dimensions |
| Advisory safety scan | `content:read`; it does not change lifecycle or approve |
| Approval | `content:write`, readable evidence, `reviews:decide` for the profile, independent verified authorship and exact current revision |
| Approved manual export | `content:read` and current approved/published revision; there is no invented `content:export` scope |
| Manual publication receipt | Separate `content:publish`, current approved revision, explicit confirmation and recent MFA |

Every action retains tenant and action/resource coupling through the shared
repository envelope. Linked briefs, ideas, skills, research projects and
citations are resolved in the same organization, including their linked
projects and private-memory boundaries. Lists use bounded tenant/filter/policy
cursors (default 20, maximum 50). History search uses the existing lexical/vector
candidate prefilters, defensive ownership checks and an explicit token budget.
It does not record query terms in workforce monitoring. Search results and
advisory comparisons are bounded evidence, not exhaustive history guarantees.

Research brief requests select stored claim IDs and `(claim_id, citation_id)`
keys. The server resolves actual claims, citations, snapshots and locators and
revalidates them through the existing research integration service. A caller
cannot replace their evidence with invented citation objects. The browser
shows retained evidence, source links, factual review state and authored
confidence separately. Structured preparation/repurpose results are planning
inputs, not newly generated drafts.

Body changes create immutable versions and return content to drafting, clearing
its current publication receipt. Authorial metadata changes also invalidate
review; editing cannot be combined with approval. Published prose requires a
new body version before metadata editing. Record revision and body version are
separate labels. Conflicts preserve unsaved prose in component memory and
require explicit comparison with the current server revision before retry.
Line comparison is bounded to 400 lines per version; full stored text remains
available in paged history. Markdown uses the existing allowlisted renderer;
HTML/scripts/embeds and unsafe URL schemes are inert.

Managed approval gates remain shared by V2 REST, V6 and capable clients. Normal
machines cannot review with `content:write`. A separately approved service
reviewer needs current custody, reviewer grants and independent immutable
user/event-time sponsor authorship; using another client of the same sponsor
does not create an independent reviewer. The currently shipped MCP publication
review tool is advisory and cannot approve; its publication tool still requires
separate publish authority. No new MCP administration or approval tool is added.

The shipped `manual` publisher makes no network request and records no verified
external URL/ID. The screen confirms an outside-console action and labels the
local receipt-recording time; it does not claim provider confirmation. Export
returns formatted text with `Cache-Control: no-store`, writes no server file,
and copies only after an explicit user action. LinkedIn, Medium and GitHub are
format choices, never external destinations called by this console.

Metrics are descriptive manual observations with capture time, source and
escaped notes. No automatic provider telemetry, productivity/cost inference or
causal analytics are promised. Creator audit is content-free and best effort,
matching ordinary domain audit. It records the verified human actor; the legacy
required `key_id` field uses `unauthenticated` to mean no API key, not the human's
authentication state. The server-owned audit append checks organization ownership
and the current session/policy fence and grants no audit-read/corpus authority.
Governance audit retains its separate transactional requirement.

## Verification

```sh
python -m pytest -q tests/unit/test_v6_creator_adapters.py tests/unit/test_v6_review_authority.py
python -m pytest -q tests/unit/test_content_api.py tests/unit/test_content_discovery.py tests/unit/test_content_novelty.py tests/unit/test_content_retrieval.py tests/unit/test_content_publication.py tests/unit/test_content_publishing.py tests/unit/test_content_metrics.py tests/unit/test_content_research_service.py
V6_CREATOR_ADAPTERS_ONLY=1 bash scripts/test-v6-isolation.sh
npm --prefix dashboard run test:unit -- src/creator/versions.test.tsx src/creator/review.test.tsx src/creator/receipts.test.tsx
npm --prefix dashboard run build
npm --prefix dashboard run test:e2e -- e2e/creator.spec.ts
```

The integration runner selects `test_v6_creator_adapters.py` and
`test_v6_review_authority.py` against disposable MongoDB/mongot. Repeated logical
IDs have different private tenant sentinels. Approval fixtures create real
server-recorded authorship and separately approved service custody, rather than
injecting prefiltered provenance. Connected stdio/HTTP MCP clients verify the
advisory/approval and publishing boundaries. Browser journeys use the built SPA,
real HTTPS API, persisted sessions and synthetic users/factors through the
existing private fixture channel. They produce actual revisions, decisions,
exports and local receipts without contacting publishing providers. Browser
engine/version and measured results belong in each run's evidence; no untested
engine coverage is implied. Planning, Graphify output, screenshots and disposable
infrastructure artifacts remain ignored and outside public release inventories.
