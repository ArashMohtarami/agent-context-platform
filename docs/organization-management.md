# Organization management

Human management uses session-only `/api/v6` endpoints and the current verified
organization. People, teams, invitations, assignments, delegations and access
requests are tenant-scoped. A submitted organization, team or user ID selects a
resource; it never establishes authority. See the [console guide](dashboard.md)
for navigation and [verification](verification.md) for release-gate status.

## Roles and effective permission

The fixed human templates are `org_owner`, `org_admin`, `executive`, `team_lead`,
`reviewer`, `contributor`, `auditor` and `viewer`. `installation_operator` is a
separate host boundary. The authoritative capability catalog is
[`catalog.py`](../app/security/policy/catalog.py).

A template lists eligible capabilities. An actual assignment must name each
action, resource dimension and selector, and be within current ancestry, target,
role, depth and expiry limits. Grant authority, metadata monitoring and data use
are separate. An owner title is not an unrestricted corpus bypass.

| Selector | Meaning |
| --- | --- |
| `none` | No resources; the default for new managed assignments |
| `include` | Only the explicitly selected existing IDs |
| `all_in_org` | Existing and future resources in this organization, with separate explicit issuance authority |

Empty arrays never become ALL in managed policy. The reviewed legacy-adoption
adapter preserves explicit `all_in_org` only within its adoption organization.
Read-A/write-B assignments remain separate; their actions and resources cannot
be combined into write-A. Project-directory rights do not imply context rights.
Organization-global knowledge, imported skills and private client memory each
have explicit policy dimensions.

Effective-access views explain only authorized sources. A worker can inspect
their own access; a lead's view is limited to the delegated management boundary.
Global users, login identities, factors and other organizations' memberships are
not exposed through tenant directory search. Directory search uses permitted
tenant display names, with bounded pagination and policy-bound cursors.

## Initial owner and bounded delegation

The installation operator provisions the initial owner and records independent
approval of the root ceiling and minimal project-directory bootstrap policy.
The policy engine activates explicit assignments transactionally. This is the
documented operator bootstrap exception, not anonymous ownership or an owner
corpus bypass. Further self/corpus grants require independent approval or the
documented operator recovery procedure.

Owner/admin delegation can appoint executive/lead policy within its reviewed
ceiling. An executive needs explicit delegation authority. Leads can appoint
permitted member roles within their named team and parent envelope; they cannot
appoint owner/admin or widen themselves. Team-member leaf assignments have no
further child delegation. Self-grants and self-ceiling changes are refused.

Every child is bounded by parent generation, allowed targets/roles, remaining
depth, exact action/resource policy and expiry. Parent revoke/narrow and child
issuance contend on the shared organization policy anchor. Governance mutation,
immutable audit and outbox commit together; audit failure refuses the mutation.
Ordinary domain audit retains its documented best-effort contract.

## Invite a lead or worker

1. Inspect your effective management and delegation rights. Create/manage a
   team only if that action is authorized; a lead cannot place arbitrary people
   into a team to acquire authority over them.
2. Select the recipient/team, exact role and resource policy, and expiry.
   Review NONE/selected/all-in-org explicitly. Invitations retain the reviewed
   parent generation and team boundary, not just a role name.
3. Submit with current revisions and recent MFA. The configured private local
   mail sink provides operator handoff; this distribution makes no SMTP delivery
   claim. Never use real recipients in verification fixtures.
4. The recipient verifies the invitation and authenticates an existing identity
   or completes invitation-only local enrollment and MFA. Acceptance rechecks
   current issuer authority, ownership, generations and expiry and consumes the
   purpose-bound secret atomically.

An invitation cannot reset an existing person's credentials/MFA, restore a
suspended membership or satisfy another organization's required SSO provider.
Equal emails never link identities. Renewing a proposal invalidates the previous
pending challenge; it does not revive an expired or revoked grant.

## Request and approve access

A worker requests exact action/resource policy with the current membership and
policy revision and an expiry. The request grants nothing. An independent
authorized approver checks the exact current revision, resource ownership and
their still-current parent envelope before deciding. A lead cannot approve
beyond delegated team/resources. Self-approval, stale revisions and foreign
references fail. Decisions are immutable; an edit needs a new reviewed request.

After a conflict, reload the current policy and deliberately retry. The console
does not silently replay a review or credential issuance. Expiry, suspension and
generation changes are current authorization inputs, not delayed TTL cleanup.

## Approve a client, then issue its credential

Client policy approval and secret issuance are separate actions. The lead or
other authorized manager approves the bounded client policy and verified human
sponsor/team relationship. Old descriptive `owner_id` is not verified ownership.
Legacy keys must be explicitly claimed to a verified owner/service account or
revoked before managed activation.

The recipient authenticates and generates their own credential once after the
required step-up. Leads inspect/revoke permitted metadata; they cannot recover
another worker's token. Retry returns safe metadata, and lost delivery requires
revocation/replacement. Rotation, expiry and revocation are explicit and
revision-checked. Tokens are not replayed through idempotency or outbox state.

Unattended automation uses an explicit organization service account with an
authorized custodian. Effective rights intersect the credential, client and
current sponsor/team/delegation policy for each action/resource in REST and MCP.
Capability previews describe limits and never authorize operations themselves.
Permission-template edits/disable do not propagate to assigned clients or keys.

Normal machines cannot approve content/research merely with write permission.
The separately approved service-account reviewer exception also requires current
custody, operation-specific review/read rights and independent reviewer ancestry.
Verified human and event-time sponsor provenance enforce self-review exclusion;
a different client belonging to the same sponsor is not independent. See
[managed agents](managed-agents.md) and [management policy](management-policy.md).

## Offboard, reinstate and hand over ownership

Inspect the impact on membership, teams, assignments, clients, credentials,
browser sessions and pending jobs. Suspend only within your management boundary,
using the expected revision and step-up. Future authorized work and result
downloads are denied after the current policy revocation fence; already committed
work, authored knowledge and historical audit are preserved.

Membership suspension advances its authority revision and revokes human browser
sessions. Team suspension/removal advances the relevant generation/edge revision.
Reinstatement advances authority again and does not reactivate revoked children.
Removing membership in one organization does not suspend another membership.
Team moves do not rewrite event-time attribution or expose previous-team history.

The last active owner is protected. Ownership uses a distinct offer/acceptance
handover with verified active membership, recent MFA and current revisions.
The reviewed governance root transfers; personal corpus assignments do not
automatically transfer. Ordinary role editing/suspension cannot remove the owner.
If a legitimate independent approver is unavailable, use documented installation
operator recovery with explicit approval and transactional audit; do not invent
a browser self-grant or anonymous owner claim.

## Operator boundary and further contracts

Logical tenant isolation protects application interfaces. Installation operators
with database, secret mounts or raw backups remain trusted and can access all
organizations' stored data. An org owner is not automatically such an operator.
Whole-installation backup/restore, raw shared metrics, provider configuration,
adoption and cutover remain outside ordinary organization permissions. A tenant
CSV export never authorizes a database `--drop` or live restore.

See [deployment/recovery](dashboard-deployment.md),
[human-management APIs](human-organization-management.md),
[identity/SSO](sso-identity.md), and [monitoring policy](dashboard-monitoring.md).
