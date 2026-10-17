# Human-sponsored agents and service accounts

Phase 7 adds verified machine sponsorship to the existing V4 client/key/session
interfaces and V5 template snapshots. It adds session-only management APIs;
normal MCP tools cannot manage people, organizations or credentials. No dashboard
or live deployment activation is included.

## Cutover and legacy keys

`V6_MANAGED_AGENTS=false` is the default. Enable it only with authenticated tenant
isolation and the verified Phase 3–6 identity/policy configuration. Migration **42**
follows actual head 41 and requires an explicit stopped-writer operator cutover.
Stop API, stdio/HTTP MCP and background writers. Verify a restorable installation
backup, then prepare with the new runtime/configuration:

```sh
contextctl --org-id fictional-org managed-agents prepare \
  --writers-stopped --worker-schema-version 42 \
  --approval-reference fictional-independent-cutover-review
```

Preparation preserves domain ObjectIds, authored records, key hashes, actor and
revision history. It adds strict authority storage and switches the persisted
worker seal to 42. Older workers are refused. Earlier adopted-key policy adapters
are explicitly revoked; an unverified admin adapter cannot continue queued work.
Human organization management remains available. Machine authentication stays
sealed until the separate installation-wide activation succeeds.

Inventory every organization's active keys with `managed-agents inventory` and
the same required org, writer/version and approval flags. Each legacy key needs
one of these explicit decisions:

- Independently approve a client with a verified human recipient or organization
  service account. Then `managed-agents claim-key --policy-file reviewed.json`
  binds an adopted key to that reviewed client. The bounded claim contains
  `key_id`, `client_id`, `expected_client_revision`, exact `tuples`, and aware
  `expires_at`. It cannot expand the existing key, sponsor or parent policy.
- `managed-agents revoke-key --key-id PUBLIC_ID` retires the credential. Expired
  but still active metadata must also be explicitly revoked.

The operator never gets a plaintext key from claiming. Descriptive `owner_id`
values, equal emails and creator labels are never ownership proof. Claim cannot
silently move a key already attached to another client. New credentials cannot
choose legacy identity rules.

Run `managed-agents activate` with the same required flags after reconciliation.
Activation checks every active key across the installation under shared policy
anchors. An unclaimed, expired, stale or unresolved credential blocks it. This
command creates no grants. Repeated migration preserves the activation marker;
unknown populated reserved storage is refused. Rollback requires a sealed,
verified installation restore. There is no tenant restore API.

## Approval, custody and current permissions

A client approval records immutable organization, subject kind/ID and optional
team, a verified human sponsor, sponsor membership/team-edge revisions, the
independent issuer's exact assignment ID/generation, bounded action/resource
policy and expiry. An organization service account has a verified current human
custodian, named team, bounded lifetime and independent approval. Old `owner_id`
is retained as descriptive history and never consulted by authorization.

Client policy approval requires an independent authorized manager and a current
delegating parent with sufficient depth, target/team envelope and issuance
ceiling. Leads can approve only their delegated named team's clients. They cannot
approve their own policy, move a client to another subject/team, or widen beyond
that parent. Explicit owner/admin capabilities are still required; titles confer
no corpus access.

Every REST authentication and MCP preflight re-evaluates the current sponsor,
team/edge generation, service custody, complete delegation ancestry, client
snapshot and key policy. Intersection preserves each action/resource pair:
read-A plus write-B never permits write-A. NONE remains empty. Organization-global
knowledge and skill imports require their own explicit tuples. No null-org or
shared-corpus fallback is introduced.

Policy updates advance the client approval revision and invalidate its previous
credential approvals. Revoked/suspended membership, team, parent, client, key or
service custody denies future work. Reinstatement does not restore old captured
revisions. Reapprove and issue a replacement explicitly. A service custody change
requires a new independently approved account/relationship; there is no implicit
handover from an old owner label.

## Recipient-only one-time delivery

Approval contains policy only. A recipient signs in with their own recent-MFA
browser session and explicitly delegated `credentials:issue:own` capability.
For unattended service accounts, the authenticated current custodian performs
issuance within the approved service policy, with transactional governance audit.
A lead may inspect/revoke permitted metadata and cannot issue or recover another
worker's token.

The recipient submits an `issuance_id`, current approval revision, exact narrowed
key tuples and expiry. The server reuses the existing random key minting and
SHA-256 verifier for high-entropy machine secrets. The token is returned once,
after the key/receipt/audit transaction commits, with `Cache-Control: no-store`.
Only its hash goes into `api_keys`; the delivery receipt, governance event and
outbox contain safe identifiers/policy metadata. Browser storage and frontend
environment variables are not token destinations.

An identical retry returns safe metadata with `delivered=false`, without a token.
A changed command under the same receipt ID is refused. Lost response delivery
requires explicit revoke/replacement; it never causes plaintext replay. Rotation
uses a new issuance ID and `replaces_key_id`, atomically revoking the old key and
issuing its replacement. Expiry is checked on each request, not by cleanup.
Ordinary MCP idempotency also refuses machine credential material in retained
results. No credential issuance tool is added to MCP.

## Templates, previews, sessions and limits

An explicit template assignment selects an active V5 profile and exact revision,
and can only narrow its scopes. The copied scopes/profile revision are recorded
with the approved client. Later template edits/disable do not update assigned
clients or issued keys. An explicit reassignment is reviewed again and invalidates
old credential approvals. `rate_limit_profile` remains descriptive; it changes no
limit. Preview returns current intersected tuples, copied scopes, source revision
and policy revision; it is not a promise of future authority.

MCP preflight and capability summaries carry complete action/resource tuples.
Generic project/profile lists are empty for managed summaries to avoid suggesting
one broad grant. Named resource preflight uses the tool's action; the API still
checks every actual resource/reference and every search candidate. Summaries
never substitute for authorization.

V4 agent sessions are bound to the org/client/key/subject/policy fingerprint.
A change to its effective policy requires a new operational session. Copied
template edits and unrelated org metadata do not invalidate its policy context.
The live shared org anchor still fences every authorized persistence step. HTTP MCP transport sessions
remain bound to their original key/org and reauthenticate every request; stdio
preflight reauthenticates each tool call. MCP rate reservations carry verified
subject/policy context and single-use leases. Quotas aggregate by org/client so
rotating credentials or changing policy cannot reset a client's budget. Limits
remain process-local as documented; no distributed quota enforcement is claimed.

Ingestion captures the exact initiating key and schema version. Workers and job
reads/downloads recheck the same current sponsorship and action/resource source
closure. Revocation denies pending work/results. It does not undo work committed
before the revocation fence won. Operator backups/raw metrics remain host-only.

## Service-account reviewer exception

Ordinary clients cannot review with `content:write`, even if their copied policy
mentions review. A service account additionally needs a separate bounded approval
from an independent verified reviewer. That approval names the reviewer's current
assignment/generation/membership revision, exact review/read/operation resources
and expiry. The custodian and account creator cannot approve the exception.
Custody and the independently delegating client ceiling still bound all data
operations. The reviewer cannot contribute unrelated policy as a broad union.

For every review operation, the service must have readable evidence, the exact
write/approval action and `reviews:decide` on the relevant resource. Server-derived
actor provenance records the human sponsor/team/service and approval revision at
authoring time. Fenced immutable author history includes every verified sponsor,
so changing a last writer or client name cannot permit same-sponsor self-review.
Unknown historical authorship is refused. Human reviewers also cannot approve
work sponsored by themselves. Current machine ownership never rewrites event-time
attribution. Revoking the independent reviewer's ancestry disables the exception.

## API surface and verification

All paths are relative to `/api/v6/organizations/{org_id}` and require the current
human session/org context. Every mutation requires Origin, CSRF and recent MFA.
Bearer V1–V5/MCP cannot use browser cookies.

| Method/path | Purpose |
| --- | --- |
| GET `/managed-clients` | Bounded, authorized own/team/org metadata |
| POST `/managed-clients/preview` | Explicit policy/template preview; no assignment |
| POST `/managed-clients` | Independent reviewed create/reassignment |
| GET `/managed-clients/{id}` | Authorized policy/effective tuples and secret-free receipts |
| POST `/managed-clients/{id}/credentials` | Recipient/custodian-only issue or explicit rotation |
| POST `/managed-clients/{id}/revoke` | Conditional client revocation |
| POST `/managed-clients/{id}/credentials/{key}/revoke` | Conditional credential revocation |
| GET/POST `/service-accounts` | Bounded authorized directory / independent custody approval |
| POST `/service-accounts/{id}/reviewer-approval` | Separate independent reviewer exception |
| POST `/service-accounts/{id}/revoke` | Conditional service revocation |
| PUT `/client-templates/{id}` | Explicit revisioned V5 template edit; no propagation |

`expected_revision=0` creates a new client/template. First adoption of an existing
unmanaged V4 client uses its actual current client revision; subsequent approvals
use the returned managed approval revision. Credential commands always use that
managed approval revision. Natural-key indexes start with organization identity;
key public IDs remain globally unique only for initial authentication lookup.
New storage is tenant-owned `service_accounts`, `managed_clients`,
`managed_credentials`, plus installation metadata `_managed_agents_control`.
None is a corpus search source or an ordinary org directory identity DTO.

Run `scripts/test-v6-managed-agents.sh` only in disposable infrastructure. It runs
the prerequisite unit/MongoDB/mongot/Keycloak selectors plus the three Phase 7
real selectors. Focused files retain the requested names. Reports include exact
runtime/test fingerprints. Skipped real provider/database tests are an incomplete
gate. Use fictional identities/test delivery only. No new dependency or license
is introduced by Phase 7.
