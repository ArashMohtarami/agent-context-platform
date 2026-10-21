# Organization console

The console at `/console/` provides human access to organization management,
knowledge, research, creator workflows and authorized monitoring. Its source,
tests, lockfile and dependency notices live in [`dashboard/`](../dashboard/README.md).
It uses the same domain services and policy engine as the agent APIs.

Console serving is disabled by default. An operator must complete the tenant,
identity, policy and sponsored-machine cutovers before enabling it. Enabling a
flag does not perform those cutovers. Release acceptance is a separate gate;
see [verification and known limits](verification.md). The public marketing site
is independently hostable and contains no console session or organization data.

## Sign in and choose an organization

Local enrollment requires an invitation and verification. Use the delivered
single-use code, establish your password and complete local MFA enrollment.
There is no public signup or anonymous first-owner claim. An installation
operator provisions the initial owner through the explicit reviewed bootstrap.

An organization can be `local_only`, `sso_preferred` or `sso_required`. Required
SSO accepts that organization's approved provider and assurance; signing in to
another organization's provider is insufficient. Provider downtime creates no
automatic local fallback. See [local identity](local-identity.md) and
[SSO and identity linking](sso-identity.md) for recovery and revocation limits.

The organization picker lists your own verified active memberships. Switching
reauthorizes the target membership and provider policy and rotates the browser
session, CSRF token and policy context. A dirty form offers to keep editing or
discard and switch. Requests are cancelled, private caches and selections are
cleared, and late responses from the old context are rejected. Browser Back does
not authorize entry to a previous organization. Server checks remain mandatory
even when a navigation item is hidden or disabled.

Browser sessions use Secure, HttpOnly, host-only cookies. Passwords, bearer
credentials and provider tokens are not stored in browser local/session storage
or frontend environment variables. Human cookies do not authenticate V1–V5 or
MCP, and agent bearer keys do not authenticate the session-only `/api/v6` APIs.

## Owner, lead and worker journeys

| Person | Typical journey | Boundary |
| --- | --- | --- |
| Owner/admin | Inspect effective access, create permitted teams, invite a lead, approve bounded delegation, review offboarding and ownership handover | Role titles do not supply corpus access; every operation needs an explicit current assignment |
| Team lead | Work within delegated teams, invite workers, review exact access requests, approve bounded client policy and revoke permitted client metadata | Cannot elevate to owner/admin, manage another team, self-grant or recover another worker's plaintext key |
| Worker | Accept an invitation, inspect own effective access, request precise resources, generate an approved client credential once and use permitted knowledge | Management visibility does not reveal private agent memory or another worker's credential |

The [organization-management guide](organization-management.md) explains each
workflow, expiry, revision conflicts and independent approval. Choose NONE,
selected resources or explicit `all_in_org` for each action/resource dimension.
An empty managed assignment grants nothing. Read on project A and write on
project B never authorize write on A. Organization-global knowledge and imported
skills require their own explicit policy.

## My security and agent setup

My security supports local password change, MFA enrollment and recovery,
verified identity linking, and inspection/revocation of your own human browser
sessions. Human sessions are distinct from machine execution sessions. Linking
authenticates both identities with step-up and invalidates old sessions; equal
email addresses never establish a link. A password reset neither removes MFA
nor restores suspended membership. A local factor does not bypass required SSO.

For an agent, first obtain an independently approved bounded client grant. Sign
in as the authorized recipient, complete the required step-up and generate the
credential once. Copy is explicit. Use the client's safe environment/private-file
instructions; never put the token in source, a shared configuration or a browser
cache. A retry returns safe metadata, not the secret. Lost delivery requires
revocation and replacement. See [managed agents](managed-agents.md) and the
[client onboarding guide](client-onboarding.md).

V5 agent permission templates copy policy onto explicit assignments. Editing or
disabling a template does not modify already assigned clients or issued keys.
Creator profiles, permission templates, human roles and machine clients are
different objects. `rate_limit_profile` is descriptive metadata, not an enforced
rate-limit setting.

## Knowledge, research and creator work

The knowledge workspace provides permitted projects, context, skill metadata and
versions, memory and search. Context edits use current revisions; conflicts
preserve unsaved text. Memories are intentional records in organization-global,
project, agent or project-agent scope. Every scope stays within one organization;
private agent scopes also retain client ownership. Canonical skills are reviewed
in Git and imported per organization. The console does not edit arbitrary server
files. See [knowledge workspace](dashboard-knowledge.md).

Research shows registered sources, bounded ingestion jobs, snapshots, timestamps,
locators, evidence, claims, contradictions and briefs. Reading, writing,
`research:approve`, `research:promote` and managed review are separate rights.
Decisions require an independent authorized reviewer and the exact current
revision. Promotion checks every linked target and provenance reference. Skill
patches remain proposals for reviewed Git changes and normal import. Untrusted
evidence is rendered as inert data. Network controls and existing DNS-resolution
limits still apply; see [research console](dashboard-research.md).

Creator studio supports profiles/pillars, ideas, novelty and repurposing plans,
history, evidence-backed briefs, drafts, immutable versions, review, manual
export, publication receipts and manually captured metric observations. The
calling human or agent writes prose; the dashboard has no autonomous writer.
Editing invalidates review. Publishing needs separate `content:publish` authority
and records a manual receipt; it sends nothing to external publishing platforms.
A second client with the same verified sponsor is not an independent reviewer.
Ordinary machines cannot review with `content:write`; only a separately approved
service-account reviewer exception permits automation. See
[creator studio](dashboard-creator.md).

## Monitoring, audit and reports

My workspace, Team overview and Leadership overview use authorized server read
models. Each chart has a textual summary and table, visible filters, source,
population, sample count, timezone, coverage, watermark and last-update metadata.
Zero recorded events, absent history, an unconfigured collector, missing
intervals, stale declarations and partial coverage remain distinct states.
Process-local metrics are not historical chart series.

Verified actor identity is separate from event-time sponsor/team labels.
Unattributed legacy history stays labelled. A team move does not grant access to
the previous team's events. Executive metadata rights do not reveal corpus text
or private agent memory. Last seen is not employee presence or productivity.
Prompts, transcripts and response bodies are not workforce-monitoring sources;
the console does not estimate hours, productivity rankings or model spend.

Queries cover at most 31 days inside the served 90-day horizon. Physical storage
retention is installation-managed and declared separately. CSV reports have a
5-minute lifetime and a 5,000-row/2 MiB cap; oversized requests must be narrowed.
Download rechecks current authority and expiry, redacts bodies and neutralizes
spreadsheet formulas. Reports are not database backups. See
[monitoring policy](dashboard-monitoring.md) and [authorized summaries](authorized-summaries.md).

Report delivery rechecks natural grant expiry after its final policy fence.
Overview data may reuse a bounded, source-invalidated aggregate cache, with its
original generation time; authorization is checked for every response. Complete
release acceptance remains pending; see [verification and known limits](verification.md).


## Operate and verify

Use the [deployment and recovery guide](dashboard-deployment.md) for HTTPS,
mounted secrets, invitation handoff, optional Keycloak and maintenance cutover.
Logical tenant isolation does not isolate data from trusted installation
database/backup operators. Organization roles do not authorize whole-installation
archives, raw shared metrics or live database restore.

The frontend supplies lint, typecheck, component, build and Playwright scripts.
Real MongoDB/mongot/OIDC/browser gates and their limitations are described in
[verification](verification.md). Automated accessibility checks supplement
keyboard, focus, zoom and visual review; they do not establish WCAG conformance.
