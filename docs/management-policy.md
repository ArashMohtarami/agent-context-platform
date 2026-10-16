# Managed roles, grants and delegation (V6 Phase 5)

This optional foundation follows verified tenant isolation, local identity and
SSO/org switching. `V6_MANAGEMENT_POLICY=false` remains the default. It adds no
management dashboard or team/data UI. Browser cookies still cannot authenticate
V1–V5 or MCP. Managed machine sponsorship, credential issuance and the separately
approved service-account reviewer runtime remain sealed until Phase 7.

## Authority model

The fixed catalog contains 28 management permissions and nine role templates in
`app/security/policy/catalog.py`. Templates describe selectable capabilities;
a role label grants no corpus access and does not authorize arbitrary targets.
Installation operators are a separate host boundary and have no human role
permissions. Monitoring (`activity`, audit, reports), grant issuance authority
and data use are separate action/resource tuples.

Every assignment starts with empty use/delegation tuples and NONE targets.
Selectors are `none` without IDs, `include` with 1–50 unique IDs, or explicit
`all_in_org` without IDs. ALL includes future resources in that organization.
Issuing ALL requires distinct current `grants:all-in-org` approval. Empty arrays
never mean ALL in managed policy. Only the reviewed legacy adoption adapter
preserves the earlier explicit `all_in_org` spelling.

A tuple couples one action to one resource dimension. `context:read` on A plus
`context:write` on B cannot authorize a write on A. Query/count/join/search and
result checks use the current action. Unified search intersects `search:read`
with each source's own read tuples before lexical/vector candidate selection.
Content also intersects inherited project/private-client boundaries; research
requires its research-project boundary and every linked project. Unlinked
research and organization-global memory need explicit `organization_knowledge`
access. Imported `skills`/`skill_chunks` need explicit `skill` access. Private
memory needs `client_memory`; a missing project is never global permission.
Project-directory bootstrap rights do not grant project context access.

Issuance ceilings are separate from effective use. A lead may have an explicit
ceiling to appoint reviewers while having no personal `reviews:decide` tuple.
Every child must fit the parent's actions, resources, named user targets, allowed
roles, remaining depth and expiry. The parent must itself hold current issuance
authority for that target. Owner/admin can appoint executive/lead; those roles
can appoint member roles only. Owner-to-admin appointment consumes one level;
a lineage has at most four records. No nested-team or script policy exists.
Named team workflows wait for Phase 6; no team title expands target selectors.

Self-grants, self-ceiling changes and appointment of owner/admin by leads are
refused. Regrant creates a new assignment ID. Narrowing/revocation advances the
parent generation; suspension/reinstatement advances membership revision.
Old descendants remain ineffective after regrant, widening or reinstatement.
There is no authorization cache. Expiry is checked during resolution, not by TTL.

## Bootstrap and operator boundary

Stop and drain old API, job and MCP workers before initial policy cutover. The
API will refuse to apply migration 40 automatically. The host operator uses the
existing recorded, verified initial owner's `pending_phase5` intent:

```sh
contextctl --org-id "$ORG" policy activate-bootstrap \
  --user-id "$VERIFIED_OWNER" \
  --target-user-id "$VERIFIED_LEAD" --target-user-id "$VERIFIED_MEMBER" \
  --allowed-role team_lead --allowed-role contributor \
  --expires-at "$REVIEWED_EXPIRY" \
  --approval-reference "$OPERATOR_APPROVAL" --approve-management-template \
  --writers-stopped --worker-schema-version 40
```

Enable the existing Phase 1–4 flags and mounted identity/provider configuration,
plus `V6_MANAGEMENT_POLICY=true`, for this command and subsequent workers. Root
expiry is at most 365 days. Target and role lists default to NONE. The recorded
root ceiling is preserved exactly. Activation creates explicit management
assignments plus only the already approved project-directory read/write tuples;
it does not grant the owner effective use of the root's corpus ceiling. Audit
and outbox commit with activation. Repeating activation does not widen it.

The shared control stage becomes `managed`, workers must declare version 40,
and both browser identity markers become 40. Old bearer/browser/job/CLI binaries
refuse the new state. A partially failed cutover remains closed and resumable;
do not downgrade or mix writers. Run/review the complete disposable evidence
selector below before cutover. Schema presence alone is not test evidence.

Unreviewed credentials fail closed. An operator may explicitly approve an
unchanged adopted version-1 credential, bounded to its adoption organization,
current client/scope/resource envelope and at most 30 days:

```sh
contextctl --org-id "$ORG" policy approve-legacy-adapter \
  --key-id "$ADOPTED_KEY_ID" --hours 24 --approval-reference "$ADOPTION_REVIEW" \
  --writers-stopped --worker-schema-version 40
```

This is the reviewed adoption-operator exception, not a managed service-account
sponsor. New credentials cannot choose it. Even adopted admin/bearer keys
cannot approve reviews after managed cutover: they do not establish verified
human identity or service sponsorship. This deliberate review restriction is
separate from preserved ordinary data access. Existing REST/MCP/admin paths retain
its resource checks and refresh it on authentication, job execution and result
access. Operator key revocation and client-envelope writes share the anchor and
strict governance audit. New key issuance is refused until Phase 7.
Whole-database backup/restore and raw shared metrics stay installation-only.

An owner who needs additional corpus use cannot grant it to themselves. Until
an independently approved human delegation lineage is available, the documented
host recovery exception is an explicit, expiring use-only assignment:

```sh
contextctl --org-id "$ORG" policy recover-owner-corpus \
  --user-id "$VERIFIED_OWNER" --tuples-file "$REVIEWED_CORPUS_TUPLES" \
  --expires-at "$REVIEWED_EXPIRY" --approval-reference "$INDEPENDENT_APPROVAL" \
  --writers-stopped --worker-schema-version 40
```

The file is a nonempty JSON list of at most 100 typed data-use tuples, at most
64 KiB. Recovery is at most 30 days, creates no delegation ceiling or management
permission, preserves the original root ceiling and records the operator
exception transactionally. No browser route can invoke it. Ownership transfer
moves the explicit governance/directory root after fresh MFA on offer and
acceptance; it does not transfer personal corpus assignments. The old owner's
membership revision changes, invalidating its grants and descendants. Ordinary
suspension or role assignment cannot remove an owner; transfer preserves an
active verified owner and invalidates both old session contexts.

## Review and transactional contracts

Managed review decisions need `reviews:decide` on the actual profile/research boundary,
source readability and the operation's domain permission (`content:write` or
`research:approve`). Promotion/publication remain separate permissions. New
records cannot begin in an approved review state. Service and repository hooks
cover lifecycle shortcuts as well as explicit decision routes.

A server-attested authorship ledger records human sponsors with authored writes
in the same fenced transaction. Every contributing author is excluded from
review. An adopted record without this proof remains unreviewable by managed
humans; editing it cannot manufacture sponsorship. Unknown machine authorship
and all managed machine reviewers fail closed. Phase 7's exception must bind a
separately approved service account, verified active sponsor/generation, exact
review operations/resources, expiry and sponsor-based self-review exclusions.
`owner_id`, equal emails, IdP roles or `content:write` alone do not establish it.

Each organization has a shared `policy_anchors` document. Issuance, revoke,
narrowing, suspension, ownership changes, sensitive domain writes and operator
credential/client mutations conditionally change that anchor. Governance uses
snapshot transactions with majority commit and an actual increment, not a no-op
or reads/writes to unrelated documents. Conflicting governance transactions
retry at most three times against freshly read policy and original expected
revisions/generations. Stale human domain review is never automatically replayed.
Browser mutations also fence the live session, user and membership.

Assignment/authority mutation, immutable governance event and durable outbox are
one transaction. Audit contains assignment before/after metadata, not corpus
bodies, factor secrets or credentials. Audit/outbox failure aborts the mutation.
The existing ordinary domain audit remains best-effort. Outbox records are
persisted only; Phase 5 does not send messages or pretend to deliver reports.

This follows MongoDB's documented [transaction conflict/stale-read behavior](https://www.mongodb.com/docs/manual/core/transactions-production-consideration/)
and [PyMongo transaction semantics](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/transactions/).
No dependency was added; the existing pinned PyMongo async driver and standard
library implement this phase.

## Session-only API foundation

All routes use the current verified active organization; path selectors are not
authority. Mutations require HTTPS, exact Origin, CSRF, current session context
and recent MFA. Effective-access explanations show exact tuples, generations,
expiry and inactive ancestry without exposing private login identities.

| Method | Path | Gate |
| --- | --- | --- |
| GET | `/api/v6/me/effective-access` | `grants:read` on self |
| GET/PATCH | `/api/v6/organizations/{org_id}` | `org:read` / `org:manage` |
| PATCH | `/api/v6/organizations/{org_id}/policy` | `org:manage`; provider already operator-approved |
| GET | `/api/v6/organizations/{org_id}/assignments?subject_id=…` | `grants:read` on that member |
| POST | `/api/v6/organizations/{org_id}/assignments` or `/delegations` | `grants:delegate` and current parent ceiling |
| PATCH | `/api/v6/organizations/{org_id}/assignments/{id}` | narrow only; expected revision |
| POST | `/api/v6/organizations/{org_id}/delegations/{id}/revoke` | ancestor authority; expected revision |
| POST | `/api/v6/organizations/{org_id}/ownership-transfer` | current owner; distinct verified target |
| POST | `/api/v6/organizations/{org_id}/ownership-transfer/{id}/accept` | named target; unexpired unchanged offer |

Membership suspension/reinstatement policy primitives are transactional, but
membership/team management HTTP workflows and UI remain Phase 6. No directory,
credential issuance UI, OIDC changes or data dashboard is introduced here.

## Storage, bounds and verification

Seven new collections are tenant-owned and excluded from ordinary corpus
repositories: `policy_anchors`, `policy_assignments`, `policy_credentials`,
`governance_events`, `governance_outbox`, `ownership_transfers`,
`review_authorship`. `_management_policy_control` is installation metadata.
All have strict validators; natural indexes begin with organization. Existing
private identity collections remain private. No new public filesystem/cache
path or authorization cache is introduced.

Initial evaluation refuses organizations exceeding 1,000 retained assignments.
This conservative bound includes revoked history; reaching it requires operator
capacity work, not silent truncation or deleting audit history. Selectors cap at
50 IDs, commands at 100 tuples, live lineage at four records, root expiry at
365 days and operator recovery/adoption adapters at 30 days. The shared anchor
serializes organization-sensitive writes. These are implementation bounds, not
throughput, latency or visual benchmark results. Existing Phase 0 performance
budgets remain targets; this phase claims correctness only and supplies no UI.

```sh
python -m pytest -q tests/unit/test_security_models.py tests/unit/test_api_authorization.py tests/unit/test_content_publication.py tests/unit/test_knowledge_candidate_service.py tests/unit/test_agent_client_service.py
python -m pytest -q tests/unit/test_v6_management_policy.py tests/unit/test_v6_delegation.py tests/unit/test_v6_governance_audit.py
scripts/test-v6-policy.sh
```

The full runner uses generated test-only secrets, a disposable MongoDB replica
set, real mongot and HTTPS Keycloak. It runs the full units and prior isolation,
local identity and SSO selectors before the real policy selector. Reports bind
to runtime/test bytes; skips do not establish a gate. Development selectors are
explicitly labeled and do not replace a complete run. Test evidence and planning
stay under ignored `.private/`; Graphify's local graph stays ignored too.

Acceptance verification for this implementation: 3,100 unit tests; 12 real
policy tests; 12 complete tenant-isolation tests; 9 local-identity tests; and
9 HTTPS Keycloak/org-switch tests. All passed without skips. The named focused
selectors passed 291 new policy cases and 140 existing regressions. These are
correctness results from disposable infrastructure, not capacity benchmarks.

## Fixed initial role templates

All corpus defaults are NONE. Each allowed management cell still requires its
explicit target envelope; a template is not an effective assignment.

| Permission | installation_operator | org_owner | org_admin | executive | team_lead | reviewer | contributor | auditor | viewer |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| activity:read:org | deny | allow | allow | allow | deny | deny | deny | allow | deny |
| activity:read:self | deny | allow | allow | allow | allow | allow | allow | allow | allow |
| activity:read:team | deny | deny | deny | deny | allow | deny | deny | deny | deny |
| audit:read:org | deny | allow | allow | deny | deny | deny | deny | allow | deny |
| clients:manage:org | deny | allow | allow | deny | deny | deny | deny | deny | deny |
| clients:manage:own | deny | deny | deny | deny | deny | deny | deny | deny | deny |
| clients:manage:team | deny | deny | deny | deny | allow | deny | deny | deny | deny |
| clients:read:org | deny | allow | allow | deny | deny | deny | deny | deny | deny |
| clients:read:own | deny | allow | allow | allow | allow | allow | allow | allow | allow |
| clients:read:team | deny | deny | deny | deny | allow | deny | deny | deny | deny |
| credentials:issue:own | deny | deny | deny | deny | deny | deny | deny | deny | deny |
| credentials:revoke:org | deny | allow | allow | deny | deny | deny | deny | deny | deny |
| credentials:revoke:own | deny | deny | deny | deny | deny | deny | deny | deny | deny |
| credentials:revoke:team | deny | deny | deny | deny | allow | deny | deny | deny | deny |
| grants:all-in-org | deny | allow | deny | deny | deny | deny | deny | deny | deny |
| grants:delegate | deny | allow | allow | deny | allow | deny | deny | deny | deny |
| grants:read | deny | allow | allow | allow | allow | allow | allow | allow | allow |
| members:invite | deny | allow | allow | deny | allow | deny | deny | deny | deny |
| members:read | deny | allow | allow | deny | allow | deny | deny | deny | deny |
| members:reinstate | deny | allow | allow | deny | allow | deny | deny | deny | deny |
| members:suspend | deny | allow | allow | deny | allow | deny | deny | deny | deny |
| org:manage | deny | allow | deny | deny | deny | deny | deny | deny | deny |
| org:read | deny | allow | allow | allow | allow | allow | allow | allow | allow |
| org:transfer-owner | deny | allow | deny | deny | deny | deny | deny | deny | deny |
| reports:export | deny | allow | allow | deny | deny | deny | deny | allow | deny |
| reviews:decide | deny | deny | deny | deny | deny | allow | deny | deny | deny |
| teams:manage | deny | allow | allow | deny | deny | deny | deny | deny | deny |
| teams:read | deny | allow | allow | allow | allow | deny | deny | deny | deny |
