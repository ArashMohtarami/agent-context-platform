# Security

The platform authorizes sensitive knowledge and authority at the server. Retrieved
content is labelled data, not instructions. [Verification and known limits](verification.md)
records unresolved release blockers; documented controls are not a conformance or
capacity certification.

## Organization and operator boundaries

All served domain records, imported skills, memory scopes, jobs, counts, joins,
search candidates and exports belong to one organization. Missing trusted context
fails closed; an org header/path/body selector is never authority. References,
provenance and supersession must stay in the same org. Tenant/resource filters
apply before lexical/vector selection, with defensive result checks. Cursors bind
org, subject, action and filters. Extra organizations remain sealed until explicit
activation. See [database contracts](database.md) and [isolation](tenant-isolation.md).

Logical isolation does not protect stored data from trusted installation database,
secret-mount and backup operators. Private global human identities are not tenant
directory data. Raw whole-database archives, restore, provider configuration and
shared process metrics are installation operations; an org owner is not an
installation operator. Authentication-disabled multitenant startup is refused.
Public health/readiness does not supply domain authority. No public owner claim,
public enrollment, tenant restore or unscoped worker compatibility mode exists.

## Browser identity and SSO

Human sessions authenticate only `/api/v6`. Bearer V1–V5/MCP refuse browser cookies;
V6 rejects bearer authentication. Opaque random sessions/challenges are hashed in
MongoDB. Cookies are Secure, HttpOnly, host-only, SameSite=Lax; idle and absolute
expiry are checked per request. Mutations require exact HTTPS Origin and CSRF,
current session/org/policy context and recent MFA for privileged work. Rotation,
logout, revoke, org switching and sensitive identity changes invalidate old
contexts. No persistent browser token or `VITE_*` secret is used.

Each authenticated request reads current user, membership and organization
authority from the exact stored session bindings, including their epochs and
revisions. The internal read join follows the exact hashed session to its verified
same-org membership before reading global user and login-identity records. It is
not a directory source and never matches email/name. Joined identity rows stay
private. Expiry is checked again after authority reads and in the final atomic
session update. No authority rows are cached across requests. Session mutations
join the exact original context bindings on their existing transaction session;
the anchor, user, membership and browser-session write fences remain required.
Policy reads use one bounded org-filtered aggregation for the current ancestry
inputs, including reads on an existing transaction session. Global users join
through same-org membership IDs; row-count, execution-time and MongoDB
document-size limits refuse excess work instead of returning partial authority.
Installation controls are checked before private joins on ordinary reads and
outside an older transaction snapshot on transactional reads. The shared policy
anchor still fences revocation against issuance and delivery. This is not a
persistent policy cache.

Invitation-only local enrollment uses Argon2id through the maintained password
library, with pinned work parameters and bounded concurrency. It does not use the
machine-token hash or reversible password encryption. MFA seeds use versioned
installation-mounted AES-GCM keys; recovery codes are random hashed, single-use
values. Reset leaves MFA and suspended memberships intact. Purpose-bound,
expiring challenges consume atomically with identity audit. Delivery is a private
local test/operator sink; no production SMTP or real invitation sending is claimed.
See [local identity](local-identity.md).

OIDC uses Authorization Code + S256 PKCE, state/nonce and exact issuer, subject,
audience, signature, expiry and redirect checks. Discovery/endpoints are bounded
and allowlisted HTTPS, not an arbitrary URL proxy. Equal email never links
identities; linking authenticates both with step-up and session invalidation.
Provider roles/groups are not platform grants. Required SSO checks the target
org's approved registration and trusted assurance; org A login cannot satisfy
org B's different provider. Downtime supplies no automatic local fallback.
Explicit operator-approved break-glass is bounded and requires local MFA.
Provider/account revocation follows documented session/revalidation bounds, not
unsupported instant revocation. See [SSO contracts](sso-identity.md).

## Roles, grants and review

Every managed permission couples an action to a resource selector: NONE,
selected IDs or explicit `all_in_org`. Empty managed policy grants nothing.
Read-A/write-B cannot authorize write-A. Organization-global knowledge/skills need
explicit rights; management visibility never implies private agent memory.
Role templates only define eligible capabilities. There is no owner corpus bypass.

Children remain within current parent generation, resource/action envelope,
allowed targets/roles, depth and expiry. Self-grants, self-ceiling changes,
foreign references and last-owner removal fail. Governance mutation and immutable
audit/outbox share a replica-set transaction and conditional policy-anchor fence;
parent revoke/narrow races child issuance on that same anchor. Reinstatement or
widening never resurrects revoked descendants. Ordinary domain audit remains
best-effort and is not the governance durability contract.

Content/research review needs operation-specific authority, source readability,
exact current revision and verified independent authorship. `content:write`
alone never authorizes managed approval. Normal machines cannot review; a
separately approved service-account exception is bounded by custody, review
operations/resources, expiry and sponsor-based self-review checks. A second
client with the same sponsor is not independent. Promotion and publication have
separate permissions; publication records a manual receipt without outbound
publishing. See [organization management](organization-management.md).

## Machine credentials and offboarding

A credential has public key ID and 32 random secret bytes; only its SHA-256 hash
is stored, compared in constant time. High-entropy machine tokens use a different
contract from human passwords. Verified sponsorship/custody is required; old
`owner_id` text is not proof. Legacy keys must be claimed explicitly or revoked
before managed activation. A reviewed legacy adapter means `all_in_org` only in
its adoption org, never a cross-org/global admin key.

Approval and issuance are separate. Only the authenticated authorized recipient
receives plaintext once. Leads inspect/revoke allowed metadata, never another
worker's token. Retries/outbox/idempotency do not replay plaintext. Lost delivery
requires revoke/replacement. Template edits/disable do not change assigned clients
or keys. Current key/client/sponsor/team/delegation rights intersect per action
and resource on REST, stdio and HTTP MCP preflight, including active sessions.
Jobs/results recheck current authority; already committed work is not undone.
See [managed agents](managed-agents.md) and [client setup](client-onboarding.md).

## Secrets, monitoring and audit

Keep secrets in private mounted files/approved secret storage, never source,
images, argv, URLs or browser storage. Back up all retained MFA key versions
separately from raw database archives. MongoDB tooling receives passwords via
private temporary files. Structured logs have a field allowlist and bounded
failure classifications; do not assume every historical log or arbitrary driver
trace is safe to publish. Review artifacts before sharing them.

Governance and activity records are content-free. Private retrieval diagnostics
may contain search queries and are excluded from workforce monitoring, as are
identity audit, prompts, transcripts and response bodies. Event-time sponsor/team
labels do not rewrite verified actor identity. Team moves cannot reveal previous
team events. Metadata rights do not reveal corpus. Charts declare source,
population, timezone, watermark/coverage and unavailable/partial states; process
last observations are not historical series or productivity measures.

`/metrics` requires the separate mounted `OPERATOR_METRICS_TOKEN_FILE`; a tenant
`system:read` grant is insufficient. Tenant reports have current policy checks,
5-minute expiry, 5,000-row/2 MiB bounds and CSV formula neutralization. Reports
are not raw backups. See [monitoring](dashboard-monitoring.md),
[observability](observability.md) and [backup](backup.md).

Report delivery reevaluates natural grant expiry synchronously after its final
policy-anchor check, with no subsequent database await. Four real MongoDB cases
pass for export/activity expiry during rendering and the final fence. Complete
final-source expiry/revocation reconciliation remains required; see
[verification and known limits](verification.md) before deployment.


## Deployment, queries and untrusted rendering

The [HTTPS console overlay](dashboard-deployment.md) removes API host ports and
trusts only its configured ingress proxy. SPA fallback is restricted to console
navigation; API/MCP/missing assets retain errors. API and HTML are no-store;
successful hashed assets are immutable. API CSP denies execution/framing;
the separate static console permits local scripts/fonts/connect and the styles
needed by its primitives, with frame protections and no CDN dependency. The
public marketing site is separately hostable with no session/API upstream.

Queries use typed value fields, bounded predicates and escaped search terms;
callers cannot supply Mongo operators, shell commands or arbitrary filesystem
paths. Strict validators and tenant-first natural-key indexes supplement
repository guards. Managed mutations use expected revisions and current policy.
Request body, concurrency, auth-work, candidate and pagination limits bound work.
Limits are safeguards, not demonstrated throughput promises.

Evidence ingestion retries at most five confirmed-aborted internal storage
transactions, with bounded backoff and a fresh session/policy fence each time.
Unknown commit outcomes, caller-owned transactions, human review decisions and
credential issuance are not automatically replayed by this storage hook. A
policy revision change still refuses the operation.
This distinguishes MongoDB's
[transient transaction and unknown commit labels](https://www.mongodb.com/docs/manual/core/transactions-in-applications/).

Markdown rendering disables raw HTML, scripts and unsafe URL schemes. Research
documents remain untrusted evidence. Skills are canonical reviewed Git imports;
the console cannot write arbitrary server files or auto-apply proposed patches.
The platform cannot make retrieved text safe to follow as privileged instructions.

## Research ingestion boundaries


The configured providers constrain acquisition and retained material. These
controls require an appropriate deployment network policy when accepting
untrusted source submissions.

| Boundary | Review outcome |
| --- | --- |
| URL ingestion | Registered HTTP(S) sources only; public-address preflight checks, bounded redirects/time/size, approved content types, and no ambient proxies. A connected peer is checked after response headers where available. DNS results are not pinned to the connection; use outbound restrictions for private and metadata networks. |
| File/PDF ingestion | Bytes only, no paths or execution; MIME/signature checks, parser/page/stream/extracted-text ceilings, unsafe filename, archive, and executable rejection. |
| Git/GitHub ingestion | Fixed argument invocation with a sterile environment; no shell, hooks, checkout, submodules, scripts, code execution, or credentials. File/repository ceilings and binary, symlink, unsafe-path, and likely-secret exclusions apply. |
| Authorization and evidence | V1 scopes plus linked-project and preserved-record authorization guard research projects, sources, claims, citations, candidate review, and promotion. Private source material is not returned before these checks. |
| MCP | Allowlisted Pydantic tools forward to scoped REST routes only. There is no arbitrary URL, filesystem, shell, MongoDB, or Git command input. |
| Abuse and logs | Request-size and request-concurrency middleware remain in place. New V3 source-count, fail-fast ingestion-concurrency, and chunk-count limits stop persistent or expensive work early. Structured logs redact common credentials and URL userinfo, including exception text. |

Residual network boundary: DNS validation and network connection use separate
resolutions for both web and repository acquisition. A post-response peer check
cannot prevent a request already issued to a changed address; Git exposes no
connected-peer hook. Enforce outbound restrictions against private, loopback,
link-local, and cloud metadata destinations for untrusted ingestion. These
application checks alone do not establish comprehensive SSRF prevention.
