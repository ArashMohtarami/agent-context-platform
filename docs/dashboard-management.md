# Dashboard management workflows (V6 Phase 10)

The starting backend/frontend revision was
`101c0182ca4989c84525758762ac68e4476e03c9` (Phase 9). This phase adds organization
management screens in `dashboard/` over the existing Phase 5–7 engines. It adds
no collection, migration, permission bypass or deployment activation. The
migration head remains 42; the 75 existing classified collections retain their
ownership. Installation recovery remains an explicitly approved operator CLI
operation. Analytics and corpus/research interfaces belong to later phases.

## Workflows

People and teams provide authorized directory pages, details, invitations,
verified team placement, lead assignments, suspension, offboarding and safe
reinstatement. Display-name/team-name prefix filters run before pagination.
Human roles, agent permission templates, creator profiles and machine clients
have distinct names and purposes.

The policy editor defaults personal use, delegation ceiling and targets to
NONE. Each row keeps its action and resource dimension together. Selected IDs,
`all_in_org`, onward roles, depth and UTC expiry are explicit. Assignment previews
use the real governance validator without writing a grant, audit or policy
anchor. Issuance checks the reviewed target membership revision as well as
parent/team generations. Previews reserve no authority. Existing assignments
remain effective until separately narrowed, revoked, stale or expired.

Signed-in recipients can inspect their own invitation proposals and paste a
single-use delivered secret in **My invitations**. New local enrollment uses the
Phase 9 enrollment/verification screens. Acceptance rechecks the issuer and
proposal. Requests use an independent current parent, exact policy and expiry;
decisions bind both request and policy revisions. Stale proposals require
resubmission. Self-approval and self-elevation are refused by the backend.

Owners can offer a bounded handover from a verified person's detail page. The
distinct target accepts the ten-minute offer in **Ownership & recovery**. The
existing root ceiling transfers, the former owner is demoted, and descendants
of the old root become stale. A title creates no corpus entitlement. The UI
explains when independently approved operator recovery is necessary.

Machine-client approval previews the intersection of current sponsor, team,
parent, template and client limits. Recipient/custodian key generation is a
separate authenticated operation. Key receipts expose safe metadata; managers
cannot retrieve somebody else's token. Explicit rotation revokes the replaced
key atomically. Client, key and service-custody revocation stop future authorized
work without undoing committed work.

V5 agent permission templates support catalog, create, edit, disable and actual
assignment-count impact. V5 requires at least one catalog scope; this ceiling
alone grants nothing. Client/credential resource assignments still start at
NONE. Template changes leave assigned grants and issued credentials unchanged.
`rate_limit_profile` is descriptive, not an enforced quota. V4 machine sessions
have a separate authorized lifecycle from human browser sessions. Project
bindings reuse the V4 normalized mapping model, conflict constraints and
reviewed revisions. New binding/session governance actions are explicitly
declared in the content-free activity projection.

## Session adapter enforcement

All adapter paths are under `/api/v6/organizations/{org_id}/management` and use
`CurrentBrowser`, the existing organization/context check and no-store response
middleware. They require activated human/machine governance. V1–V5 bearer routes
and MCP continue to reject browser-cookie authentication.

| Adapter | Selection and policy gate |
| --- | --- |
| `GET /context` | Own active assignment envelopes; only independently usable request handles; authorized/current own teams. No foreign ceiling/ancestry graph. |
| `POST /policy-preview` | Same parent, role, target, same-org resources and expiry validator as issuance; read-only transaction; recent MFA. |
| `GET /members/{id}` | Existing directory prefilter; safe tenant DTO; visible team links; action-specific current affordances. |
| `GET /own-invitations` | Exact current organization and authenticated recipient before projection/pagination; no secret/hash. |
| `GET /catalog/{clients,services}` | Current own/team/org client-read tuples before pagination; verified sponsorship fields; no descriptive `owner_id` inference. |
| `GET /catalog/templates` | Explicit current client-management capability; tenant-only safe template fields. |
| `GET /templates/{id}/impact` | Organization client-management grant; same-org exact template/revision and actual assigned-client count. |
| `GET /catalog/sessions` | Explicit visible managed client plus action-specific permitted project associations; safe operational fields only. |
| `POST /clients/{client}/sessions/{session}/end` | Current client management and project-write rights, session/client/org/revision match; shared governance/session fence and transactional audit. |
| `GET /catalog/bindings` | Action-specific project-read prefilter. |
| `PUT /bindings/{id}` / `POST /bindings/{id}/delete` | Current project-write rights for old/new references, same-org project existence, normalized V4 model, exact revision and uniqueness; governance transaction/audit. |
| `GET /ownership-transfers` | Exact organization and current owner/target participation; bounded metadata. |

All cursor pages bind organization, subject, query/filter and policy revision.
Foreign filters produce no foreign totals. The screens show page size, not an
invented total. Existing governance mutations retain their shared policy-anchor
CAS, current browser/membership fence and atomic audit/outbox contract. Missing
MFA, revoked authority, conflicts and service failures produce explicit refusal
or retry states; failed commands are never automatically replayed.

## Secret and browser behavior

Machine plaintext is excluded from query/mutation caches, browser storage,
metadata, idempotency and outbox. It is shown only after recipient issuance, for
up to two minutes, and copied only by a **Copy machine key** click. Dismissal,
page hide, route/context change or session loss discards the transient view.
A retry returns a receipt; lost delivery requires revocation/replacement. This
is a state-lifetime guarantee, not a claim of JavaScript memory zeroization.

Setup examples contain static placeholders and a private token-file path, never
an embedded token. They use the existing local stdio launcher and explicit
Codex/Claude Code/Gemini CLI configuration. Official client references checked
on 2026-10-18: [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
[Claude Code MCP](https://code.claude.com/docs/en/mcp), and
[Gemini CLI MCP](https://geminicli.com/docs/tools/mcp-server/).
Use the [client guide](guide/content.md) for remote HTTP setup. No new npm or
Python dependency was introduced; pinned versions, license inventory and
notices remain those reviewed in [the foundation](dashboard-foundation.md).

## Reproduction and evidence boundaries

Use the Phase 9 Node/Python/HTTPS prerequisites in [dashboard/README](../dashboard/README.md).

```sh
python -m pytest -q tests/unit/test_agent_client_api.py tests/unit/test_client_profile_api.py tests/unit/test_project_bindings_api.py tests/unit/test_agent_session_service.py
python -m pytest -q tests/unit/test_v6_management_adapters.py
V6_MANAGEMENT_ADAPTERS_ONLY=1 bash scripts/test-v6-isolation.sh
npm --prefix dashboard run test:unit -- src/people/people.test.tsx src/access/grants.test.tsx src/clients/credentials.test.tsx src/clients/templates.test.tsx
npm --prefix dashboard run build
npm --prefix dashboard run test:e2e -- e2e/management.spec.ts e2e/client-onboarding.spec.ts
```

The real integration selector is `tests/integration/test_v6_management_adapters.py`;
the disposable runner maps to that exact target. Browser tests use the real API,
replica set, HTTPS provider fixture and local test mail sink with fictional
identities/credentials. The private filesystem fixture control channel is absent
from product HTTP routes. Never point this runner at a production database or
mail service. Screenshots, reports, Graphify output and private planning remain
ignored and outside public release inventories.

Visual checks cover real populated/empty/refusal screens, both themes and
360/768/1440 CSS-pixel widths, keyboard dialog focus/restore, reduced motion and
200%-equivalent geometry. These do not establish other browser-engine coverage,
OS display scaling or a manual screen-reader audit. Build acceptance retains
the foundation's 300 KiB aggregate JavaScript gzip budget; no unmeasured latency
or productivity result is claimed.

Recorded Phase 10 verification on 2026-10-18: 3,233 Python unit tests; five
replica-set adapter integration tests; 19 dashboard component tests (eight in
the focused Phase 10 selector); and six real HTTPS browser journeys passed.
The requested existing Python selector passed 13 tests. Lint, strict TypeScript,
Python type checks, license inventory and public release audit passed. The
installed Chrome 147 fallback was used, with Node 24.22.0; browser-engine and
manual assistive-technology limitations above still apply. Representative
screens and the nested policy/credential dialogs were visually reviewed.
