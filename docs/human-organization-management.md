# Human organization management

Phase 6 adds session-only member, team, invitation and access-request APIs on
Phase 5 governance transactions. It adds no dashboard, public signup, machine
sponsor relationships or credential issuance. All examples below use fictional
people, organizations and resources. Mail goes only to the configured private
local test sink; this repository does not send invitations over the network.

## Activation and storage

`V6_HUMAN_MANAGEMENT=false` is the default. Enabling it requires
`V6_MANAGEMENT_POLICY=true` and the verified Phase 1–5 prerequisites. Migration
**41**, allocated after actual head 40, requires an explicit stopped-writer
operator cutover. API startup cannot silently perform this cutover. Stop every
API, stdio/HTTP MCP consumer and background writer, verify a restorable operator
backup, then use the operator environment with the new flag enabled:

```sh
contextctl --org-id fictional-org management activate \
  --writers-stopped --worker-schema-version 41 \
  --approval-reference fictional-reviewed-cutover
```

Restart only schema-41 workers. The tenant control seal and local/SSO/policy
markers reject schema-40 processes. Adopted-key adapter records are also upgraded
to the `legacy_adoption_v41` discriminator with an advanced revision, preserving
exact reviewed envelopes/expiries. The old queued-job resolver checks the previous
discriminator and refuses these records even without consulting a schema marker.
The domain schema remains 37; worker
compatibility is a separate recorded field. Repeating migration 41 is safe.
Unknown populated reserved collections are refused. Rollback requires a sealed,
verified operator restore; tenant restore is not exposed.

The following new collections are tenant owned and accessible only through the
human management boundary: `teams`, `team_memberships`,
`management_invitations`, `access_requests`, `access_decisions`.
`_human_management_control` is installation metadata. Every natural-key index
starts with `org_id`. Validators reject unknown fields. Existing memberships,
ObjectIds, authored knowledge, actor history and governance events are preserved.
Neither ordinary corpus search nor an installation backup changes ownership.

Phase 5 bootstrap deliberately did not assign team management capabilities.
Activation creates **no grants**. To approve those capabilities, an independent
host operator reviews a bounded JSON `RootApproval` document and runs:

```sh
contextctl --org-id fictional-org management approve-root \
  --user-id fictional-owner --policy-file reviewed-root.json \
  --writers-stopped --worker-schema-version 41 \
  --approval-reference fictional-independent-root-review
```

The file contains `tuples`, `ceiling`, `targets`, `allowed_roles`, and an aware
`expires_at` no more than 365 days away. Use tuples contain only capabilities
allowed to an owner by the fixed management catalog. The ceiling contains the
exact reviewed capabilities/resources the owner may issue, including any
previously approved corpus ceiling that should remain delegable. Existing
owner corpus-use tuples remain unchanged. This is the explicit operator
bootstrap/recovery exception, with approval reference and before/after policy
in transactional governance audit/outbox. It is not a browser self-grant.

For example, creating teams needs an explicit
`teams:manage / organization / include [fictional-org]` tuple. Reading them needs
`teams:read` with that organization or named team IDs. Inviting a previously
unknown person without a team needs independently reviewed `members:invite`
and `grants:delegate` member selectors and parent targets that explicitly cover
future members (`all_in_org`). Such an approval is consequential and explicit;
NONE remains the default. A named-team invitation instead uses that exact
team envelope, as described below. Included resource IDs must already exist;
creating a team and reviewing its named ceiling can be separate operator steps.

Root approval replaces the management-use policy and full issuance ceiling,
updates generation/revision, and permanently invalidates old descendants.
It does not merge unrelated permissions into an implicit broad ceiling. An
existing unique root is updated, so accepted ownership handover still transfers
one exact governance root. Use-only personal corpus recovery is not transferred.
Unbound Phase 5 lead assignments remain preserved but become ineffective under
schema 41 until a new explicitly reviewed named-team assignment is issued. No
team is inferred from a name, email, owner field or dormant broad selector.

## Boundaries and team delegation

All endpoints require an opaque human browser session, HTTPS, current active
organization and `X-Session-Context`. Every mutation also requires matching
Origin, CSRF and recent MFA. Bearer REST/MCP cannot use these cookies. Submitted
org/team/user identifiers select resources; they never establish authority.

Member directory DTOs expose only `org_id`, `user_id`, tenant display name,
role, membership status and revision. Searches are bounded display-name prefix
queries, qualified by org and permitted members/teams **before** pagination.
There is no global email/user search, factor/login data or other-org membership
lookup. Cursors are signed and bound to org, observer, filters and policy
revision. A policy change requires a fresh page. Team member DTOs additionally
expose the named team edge's status and revision for safe conditional edits.

Owner/admin role titles do not themselves grant team or directory access.
Leads require explicitly delegated named-team tuples. In Phase 6, direct broad
member management grants cannot bypass a lead's team boundary. Leads may read,
invite, suspend/reinstate team members, inspect grants and approve requests only
inside their currently delegated team and resource ceiling. Team structure
creation/renaming/suspension uses the fixed `teams:manage` permission; the initial
lead template does not receive it. Placing an existing person into a new team
requires explicit team management and cannot be used by a lead to acquire
control over arbitrary people. A lead cannot change their own team edge.

Team-scoped `Issue` commands add `team_id` and `expected_team_generation`.
The server captures the subject's current team-edge revision. The complete
assignment ancestry, membership revision, active team generation and active edge
revision are checked on each policy resolution. A team suspension advances its
generation. Suspension/removal of an edge advances its revision. Reinstatement
advances rather than resets these values; old assignments/descendants stay dead.

A lead's parent assignment must itself be bound to the same named team and hold
`grants:delegate / team / include [that-team]`. It can issue a leaf assignment to
a currently verified member of that team even when the new person's user ID
was not previously known. Such a leaf has depth zero, NONE targets and no
allowed child roles. Its action/resource tuples must fit that same parent's
ceiling, role and expiry. This explicit team envelope never substitutes a broad
member target set or permits recursive widening.

## Invitations and requests

An invitation stores the exact bounded reviewed policy, parent ID/generation,
optional team generation, issuer, recipient membership revision and expiry
(maximum seven days, within the assignment lifetime). The same parent must hold both invitation and delegation authority for the
selected member/team. The secret is random,
purpose-hashed and never returned by management APIs or audit. Delivery uses
only the private test sink. The usual local invite/verification challenge is
30 minutes; a longer proposal expiry does not extend either challenge.

New local recipients use the existing invitation-only enrollment and browser-
bound verification endpoints. Existing accounts authenticate their existing
password and MFA; an invitation cannot reset credentials or MFA. An already
verified active member may accept through `POST /api/v6/me/invitations/accept`
using their own recent-MFA session and the delivered secret. The narrow self
route can accept an invitation for another organization, but it cannot switch
active org or satisfy that organization's SSO policy. Explicit switching still
requires the target provider/assurance from Phase 4.

A local email selects only the exact local identity namespace. It never links
an OIDC identity with the same email. SSO users need an explicitly linked local
identity for local enrollment; no JIT registration or email-based account merge
is introduced. Tenant management responses do not reveal whether a local login
already existed globally.

Enrollment and verified acceptance recheck current issuer membership and parent
ancestry, reviewed resources, role, target, expiry and team boundary. Verification,
one-use consumption, active membership, team edge, assignment and governance
audit/outbox commit atomically. A revoke/narrow/suspend racing acceptance touches
the same org policy anchor. Losing transactions refuse or require a fresh retry;
they do not issue from a saved snapshot. Revoked/expired proposals cannot restore
suspended memberships or team edges. Renewing a pending invitation creates a new
reviewed record, revokes previous pending proposals/challenges, advances the
pending membership revision and preserves history. Old verification links cannot
activate the renewed proposal. A lead cannot use a lower requested role to
demote an existing higher-role member.

Access requests store the exact requested policy, membership revision, policy
revision, deadline (at most seven days) and immutable identity of the requester.
They grant no authority. Submission validates that the proposed independent
parent and resources belong to the org and can cover the request. The returned
`policy_revision` is the revision after submission. Approval/denial must present
that exact revision **and** the current request revision. Any intervening org
policy mutation makes the request stale; the requester cancels/resubmits rather
than silently changing it. This is deliberately conservative.

Only the named parent's currently authorized subject can approve, inside that
parent's current action/resource/role/team/expiry envelope. A requester cannot
approve themselves; unrelated assignments cannot supply missing authority.
Decision commands contain no replacement policy. A unique tenant/request index,
conditional pending revision, shared-anchor CAS, assignment insertion and durable
decision/audit/outbox transaction allow exactly one decision. No decision edit
or deletion endpoint exists. Expiry is checked per operation, independently of
cleanup. Histories remain available within authorized boundaries.

## API surface

Paths below are relative to `/api/v6/organizations/{org_id}` unless marked self.
The Phase 5 assignment/delegation and accepted ownership-transfer APIs remain
available and use the same new team-aware policy evaluation.

| Method/path | Gate and behavior |
| --- | --- |
| GET `/members` | `members:read` named member/team; bounded prefix and cursor |
| GET `/members/{user_id}/effective-access` | `grants:read` subject/team; authorized sources only |
| POST `/members/{user_id}/suspend` | `members:suspend`, expected membership revision |
| POST `/members/{user_id}/reinstate` | `members:reinstate`, verified membership, new revision |
| POST `/members/{user_id}/offboard` | `members:suspend`, preserved revoked membership |
| GET `/teams` | explicit org/named `teams:read`; lead sees delegated teams |
| POST `/teams` | explicit org `teams:manage`, immutable tenant/team ID |
| PATCH `/teams/{team_id}` | org/named `teams:manage`, expected team revision |
| GET `/teams/{team_id}/members` | permitted named team and member projection |
| POST `/teams/{team_id}/members/{user_id}` | explicit placement/lifecycle authority; expected team and edge revisions; zero means a new edge |
| GET `/invitations` | authorized member/team proposal DTO; no hashes/email |
| POST `/invitations` | `members:invite` plus same parent's current issuance envelope |
| POST `/invitations/{id}/revoke` | current invitation authority and expected revision |
| POST self `/api/v6/me/invitations/accept` | exact verified recipient, one-use challenge, recent MFA |
| GET `/access-requests` | own requests or explicit `grants:read` member/team boundary |
| POST `/access-requests` | verified self; reviewed proposal, exact current revision |
| POST `/access-requests/{id}/decisions` | independent current parent issuer; exact revisions |
| POST `/access-requests/{id}/cancel` | requester only, pending revision |

A verified active member can always inspect their own effective-access explanation
and own assignment sources through the existing self routes, including when all
use selectors are NONE. This diagnostic reveals their own parent IDs/generations
needed to submit an independent request; it grants no delegation or corpus access.
Other-subject inspection still requires explicit `grants:read`.

Team leads cannot inspect another team's assignments or effective-access sources
merely because a person belongs to both teams. Other-subject effective-access
explanations omit ancestor IDs outside the selected source. No endpoint returns
a private global identity document.

## Offboarding and deferred machine relationships

Organization suspension/offboarding advances only that membership's revision
and revokes its browser sessions in the same transaction as audit. It does not
disable the global user, destroy knowledge, rewrite actor/audit history or affect
another organization's membership/sessions. Leads cannot suspend an organization
membership if that would reach another team outside their envelope; they can
suspend only their authorized team edge instead. Owners cannot be suspended,
revoked or demoted through these APIs. Ownership requires the independent accepted
handover and current last-owner protections from Phase 5.

Pending human job authority records include verified user, membership revision,
original opaque-session public ID and worker version. Execution, persistence and
result replay recheck that session, membership, current auth policy and exact
source resources; reinstatement or a fresh login cannot revive the old job
session. SSO job checks receive the same mounted provider registry as browser
sessions. A standalone checker without that registry refuses SSO authority.

Managed credentials with unknown sponsorship remain refused through the shared
REST/MCP/job resolver. No sponsorship is inferred from `owner_id`. The separately
reviewed, expiring adopted-key adapter remains the explicit Phase 5 compatibility
exception and conveys no human sponsor or machine reviewer identity. Phase 7
must create verified sponsor relationships and establish their complete REST,
stdio/HTTP MCP and job revocation acceptance. Phase 6 makes no such lifecycle claim.

## Validation

Focused unit targets: `test_v6_memberships.py`, `test_v6_invites.py`,
`test_v6_access_requests.py`. Real replica-set targets:
`test_v6_membership_lifecycle.py`, `test_v6_access_request_races.py`.
`./scripts/test-v6-management.sh` runs all unit tests and disposable prerequisites
(isolation/search, local identity, Keycloak/org switching and policy fencing)
before the human management integration gate. Each new selector receives a fresh
disposable replica set, with a default 3 GiB MongoDB limit for the larger fixtures.
Each report records runtime and
test-source fingerprints. Mocked repositories do not establish atomicity or
revocation. Development-only selectors are not a complete acceptance gate.

No dependency or license change is introduced. The existing maintained crypto,
OIDC and MongoDB libraries remain pinned. No dashboard performance or visual
result is claimed by these API tests. There is a bounded evaluation capacity of
1,000 assignments, 1,000 teams and 1,000 team edges per organization; exceeding
it fails closed rather than silently truncating authorization. Directory pages
are at most 100 rows. Scaling those limits requires measured representative
performance and a separately reviewed policy-resolution design.
