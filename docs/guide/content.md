## 01. Understand the platform

Agent Context Platform provides organization-owned knowledge to coding agents
and verified people. MCP, bearer REST and the human console share domain services
and current action/resource policy. Embeddings run locally. The calling agent or
human writes prose; the platform does not silently run an autonomous writer.

### Current boundaries

**Release readiness remains unapproved.** Read the embedded [verification and
known limits](verification.md) before operating the console. A documentation
build, clean archive or passing component selector does not override a failed
real-infrastructure gate.

Organization roles are distinct from trusted installation database/backup
operators. Logical tenant isolation protects application interfaces, not stored
data from those operators. Human login identities are private global records,
not an organization directory. Canonical skills are reviewed in Git; runtime
skill metadata/chunks are imported separately into each organization.

## 02. Start the platform

Use the [same-origin HTTPS deployment guide](dashboard-deployment.md) and
[staged migration/recovery procedure](migration-recovery.md). A fresh checkout
is not an anonymously claimable installation. V6 flags default off; enabling all
of them is not an adoption, owner bootstrap or activation procedure.

Build reviewed pinned inputs before commissioning:

```sh
python3.12 -m venv .venv
./scripts/generate-dev-secrets.sh
./scripts/prepare-docker-wheels.sh
docker compose -f docker-compose.yml -f deploy/console/compose.yml build context-api console
```

The helper creates ignored development secrets, not a production certificate,
owner, provider or grant. Offline installations need the pinned images, wheels,
npm packages and local model. The console overlay requires an exact HTTPS origin,
mounted versioned MFA keys, private mail handoff and approved provider registrations
(or an explicit empty registry for local-only operation). Do not expose password
or MCP administration over public HTTP.

Before legacy adoption, stop every API/MCP/import/job writer and fence its database
credentials. Rehearse from a quiescent whole-database backup. Apply stages 36–42
in order using the matching flags and operator commands. Additional organizations
stay sealed until separately verified activation. Unknown records, missing source
records and unresolved same-org references block activation even after quarantine.

## 03. Organize different projects

A project is a software-system boundary inside one organization. Stable logical
IDs can repeat in another org without sharing records. The directory itself
needs `projects:read`/`projects:write`; those rights do not imply context, memory,
skill or creator-profile access. Human project creation uses the session-authenticated
knowledge workspace; agents use admitted bearer APIs.

Bindings explicitly map normalized repository/workspace hints to a project.
Hints never authorize access. Foreign IDs, counts, cursors, joins and provenance
stay tenant-bound. Read on A plus write on B must never authorize write on A.
See [projects](projects.md) and [effective policy](organization-management.md).

<!-- WIDGET workflow -->

## 04. Connect your agents

1. An authorized manager approves the client policy and verified sponsor/team or
   service-account custody within their current envelope.
2. The verified recipient authenticates, steps up and generates their token once.
   Managers inspect/revoke permitted metadata, not another worker's plaintext.
3. Copy explicitly into a private file or approved secret store. Never into
   source, client settings with literal secrets, browser storage or frontend vars.
4. Configure the supported transport, inspect effective capabilities and make an
   authorized test call. Capabilities describe limits; they never authorize calls.

| Client | Transport | Maintained instructions |
| --- | --- | --- |
| Codex | Local stdio with private API credential file | [Codex](codex.md) |
| Claude Code | HTTPS Streamable HTTP `/mcp` | [Claude Code](clients/claude-code.md) |
| Gemini CLI | HTTPS Streamable HTTP `/mcp` | [Gemini CLI](clients/gemini-cli.md) |
| Other MCP clients | Same tools and current policy | [Onboarding](client-onboarding.md) |

Platform tokens are not model-provider credentials. Browser cookies cannot
authenticate MCP or V1–V5. Each credential belongs to one org, with current key,
client, sponsor, team and parent rights intersected per action/resource. An active
MCP connection cannot preserve revoked rights. Lost one-time delivery needs
revoke/replacement; idempotency/outbox never replay the token.

The generator below configures a credential already approved and delivered through
managed issuance. Its owner label is descriptive, never proof of sponsorship.

<!-- WIDGET config -->

## 05. Work through a daily session

Resolve the explicit project, search permitted sources with a bounded token
budget, inspect provenance and save only verified facts or intentional memory.
Machine execution sessions are distinct from human browser sessions. A session
label does not expand resources or establish sponsorship.

```json
{"project_id":"notes-api"}
```

Pass that selector to `resolve_project`; then use `search_knowledge` with the
actual task and admitted filters. Do not fabricate result IDs or expect unseen
projects to appear in counts. Client trust prompts and tool filters cannot widen
server policy. See the generated tool reference for exact fields.

## 06. Coding examples across projects and agents

The fictional [payment service](../examples/payment-service/README.md),
[Notes API](../examples/starter-notes-api/README.md) and
[async Django example](../examples/django-async/README.md) are source examples,
not live customer data. First run the example's own tests; then record what the
source and test actually establish, including limitations.

A second agent can retrieve the same authorized fact with its own credential.
Private client memory remains private. Retrieved Markdown is data, never authority
to execute code, change grants or bypass review. Human context edits require the
current revision and preserve unsaved text on conflict.

## 07. Use memory with the right visibility

| Scope | Project | Visibility |
| --- | --- | --- |
| `global` | Omitted | Explicitly permitted organization-global memory |
| `project` | Required | Permitted project inside the same organization |
| `agent` | Omitted | Authenticated client's private operational memory |
| `project_agent` | Required | That client's private memory in the named project |

All four scopes are tenant-owned. Management/directory visibility grants no
private client memory. No null-org shared corpus fallback exists. The API keeps
legacy omission defaults within the trusted org, but new callers should choose
scope intentionally. Supersession links, revisions and external IDs remain
same-org and same-owner where required. See [memory](memory.md).

The visibility specimen assumes explicit rights within one fictional organization;
it is a static illustration, not a live authorization decision.

<!-- WIDGET memory -->

## 08. Research evidence before learning a rule

Use the research console to create an authorized research project, register
bounded text/URL/file sources, ingest and inspect real job state, snapshots,
timestamps, locators, documents, evidence, claims and contradictions. Reuse
existing source restrictions; the UI is not an arbitrary network/filesystem proxy.

Citations show evidence and provenance, not guaranteed truth. Confidence is a
separate authored assertion. Failures are bounded/safe, and revoked jobs/results
are refused. Existing public-address checks still have separate DNS validation
and connection resolution; outbound network restrictions remain necessary.
See [research console](dashboard-research.md) and [research](research.md).

## 09. Review and promote knowledge safely

Reading, writing, `research:approve`, managed `reviews:decide` and
`research:promote` are separate rights. An independent authorized reviewer decides
the exact current revision after verifying all source/project rights. Stale,
self/same-sponsor and foreign-reference decisions fail. Editing is not approval.

Promotion revalidates target write policy and same-org provenance. Skill patches
remain proposals for reviewed Git changes and normal tenant import; the console
cannot apply them to arbitrary files. Ordinary machines cannot review with write
permission. Only the separately approved service-account reviewer exception,
with current custody and independent sponsor provenance, permits automation.

## 10. Create content from reviewed knowledge

Creator profiles/pillars are content preferences, not human roles or permission
templates. Discover/plan ideas, inspect novelty and history, repurpose explicitly,
then write evidence-backed briefs and drafts. Humans or calling agents supply the
prose. Versions are immutable and diffs/review use current revisions.

Independent review requires explicit operation authority and every linked-project
grant. A second client with the same sponsor is not independent. Editing invalidates
review and cannot auto-approve. Export and a manual publication receipt require
separate authority, including `content:publish` for recording publication. The
platform does not send to LinkedIn, Medium or GitHub.

Metric observations are manually captured descriptive values with source and
capture time, not automatic provider analytics or causal performance claims.
See [creator studio](dashboard-creator.md),
[review safety](content-publication-safety.md) and [publishing](content-publishing.md).

## 11. Add skills and tune retrieval

Git/Markdown is canonical. Import reviewed versions into an explicitly selected
organization using private operator credentials:

```sh
contextctl --org-id YOUR_ORG skills import --directory ./skills
```

Import is idempotent for unchanged versions; changed bytes under the same version
are refused. A new version preserves history. Runtime `skills` and `skill_chunks`
never become an installation-shared corpus. Changing a local embedding model
requires compatible dimensions, stored vectors, indexes and a real search rehearsal.
Index reconciliation alone does not re-embed old documents.

### Search the canonical catalog

This embedded catalog is static source, not authorized live organization data.

<!-- WIDGET skills -->

<!-- WIDGET skill_bundles -->

## 12. Manage permissions and concurrent work

### All bearer scope names

The scope vocabulary below comes from source. A scope name alone is insufficient:
managed policy couples it to resources, ancestry, expiry and verified subject.

<!-- SCOPES -->

Use NONE, selected IDs or explicit `all_in_org` on each dimension; no empty-array
ALL default. Human role templates list eligible capabilities and never bypass
corpus policy. Owner/admin delegates within reviewed ceilings, leads remain inside
named teams, and workers request precise additional resources. Self-grants and
self-approval are refused. Last-owner removal uses the explicit handover contract.

Governance mutation plus audit/outbox commits transactionally against a shared
policy anchor. Revoke/narrow contends with child issue on that anchor. Reinstatement
never resurrects revoked descendants. V5 permission-template assignment copies
policy; later edit/disable does not propagate. `rate_limit_profile` is descriptive.

For a revision conflict, keep unsaved text, reload and deliberately rebase; never
silently replay a review, approval or credential issuance. See
[organization management](organization-management.md).

## 13. Operate, back up and troubleshoot

Whole-installation backup includes private global identities, every tenant,
quarantine/preservation, policy/audit/outbox, machine jobs and sessions. It is
operator-only. Tenant CSV exports never authorize raw archives, `--drop` or live
restore. Recover mounted MFA key versions separately and back up the external
provider's database/keys separately. Rehearse restore on isolated storage before
trusting it. See [migration/recovery](migration-recovery.md) and [backup](backup.md).

Raw `/metrics` uses a separate operator token, not a tenant `system:read` key.
My workspace, Team overview and Leadership overview use authorized event read
models with event-time sponsor/team attribution. A team move cannot expose old
team history. Last seen is not employee presence or productivity.

Charts declare source, population/denominator, sample count, timezone, coverage,
watermark and update time. Zero, missing, unconfigured, stale, partial and older
unattributed history are distinct; chart tables are available. No prompts,
transcripts, response bodies, guessed spend or rankings enter workforce monitoring.
Queries are bounded to 31 days in the served 90-day horizon. Reports expire after
5 minutes, cap at 5,000 rows/2 MiB and recheck current policy on download.

The checklist is local documentation state, not proof that a release gate passed.

<!-- WIDGET checklist -->

## 14. Complete tool, API and CLI reference

Generated from the current MCP registry, FastAPI OpenAPI and argparse tree. This
checkout snapshot is not proof of a deployed configuration. Live capability
summaries describe effective rights without replacing authorization.

### All 55 MCP tools

No normal MCP tool administers humans, tenants or dashboard credentials.

<!-- WIDGET tools -->

### REST operations and models

V1–V5 and MCP use tenant-bound bearer credentials; `/api/v6` uses only human browser
sessions. Same IDs, headers or cookie presence never select authority. Every
session mutation requires exact Origin/CSRF and privileged actions require step-up.

<!-- WIDGET api -->

### Every CLI leaf command

Operator provisioning/import/admin uses explicit `--org-id`; bearer commands derive
org from authentication. Backup/restore rejects org selectors and stays installation-wide.
Legacy `keys create` is refused under managed policy. Use recipient issuance,
not a bootstrap-key workaround. No CLI example authorizes publishing or live restore.

<!-- WIDGET cli -->

## 15. Source library and project map

Only maintained implemented documentation is embedded through the reviewed source
registry. Private plans, raw acceptance evidence, secrets, runtime state and Git
history are not publication inputs. Clean source includes `dashboard/` source,
lockfile, Node pin, tests, notices and licenses; generated builds/node_modules and
browser artifacts are excluded. The public marketing site is separate from the
console and contains no session/data upstream.

### Embedded documentation

<!-- WIDGET sources -->

### Application and test map

<!-- WIDGET modules -->

### Maintain the guide

```sh
python docs/guide/build.py
python scripts/prepare_open_source_release.py --check
python website/build.py
```

Verify local links/anchors/logo and extract/re-audit source/site artifacts. A
source archive omits `.git`; a retained repository history needs separate review
before publication. Do not publish checksums for unreviewed replacement artifacts.

## 16. From zero to a project and reviewed content

Start only after the operator's staged cutover and real acceptance gates pass.
The sequence below uses implemented human APIs through the console; it does not
mint a superuser bearer for a human or reuse the old unmanaged walkthrough's
single-author approval shortcut.

1. **Owner:** accept the operator-provisioned invitation, verify/enroll MFA or
   use the approved required provider, then inspect effective access. Initial
   ownership alone supplies no corpus bypass. An independent approver/operator
   records necessary bounded root/data policy.
2. **Owner → lead:** create the permitted team, invite a lead with reviewed
   NONE/selected/all-in-org policy, expiry and current issuer generation.
3. **Lead → worker:** invite within the delegated team. Worker verifies identity
   and membership; equal emails never merge global identities. Requests need an
   independent authorized approver and exact current revision.
4. **Agent setup:** approve a verified client/sponsor policy. The authenticated
   recipient generates/copies their token once into a private file. Use Codex,
   Claude or Gemini instructions and make a permitted test call.
5. **Knowledge:** create/read the authorized project, verify the repository's
   actual behavior, save a bounded fact and retrieve it with another permitted
   client. Keep private agent memories private.
6. **Research:** register bounded local evidence, ingest, inspect snapshots and
   citations, request independent revision-bound review and separately promote
   only to authorized same-org targets.
7. **Creator:** author an evidence-backed brief/draft, compare immutable versions,
   request independent review, export and post manually if separately authorized.
   Record the exact approved publication receipt; no outbound publishing occurs.
8. **Monitor/offboard:** use permitted own/team/org summaries and coverage, then
   suspend/revoke with expected revisions and step-up. Human sessions and future
   machine/job access recheck current authority. Preserve authored history;
   reinstatement does not restore revoked descendants.

My security covers own local password/MFA/recovery, verified identity linking and
human-session inspection/revocation. SSO-required policy still applies. Switching
org rechecks membership/provider, rotates session/CSRF, clears cached queries and
selections, offers an explicit unsaved-work choice and guards late responses.
Browser Back cannot revive the old policy context. Server checks remain mandatory
if a user unhides a disabled button or calls an API directly.

See [dashboard](dashboard.md), [organization management](organization-management.md),
[deployment](dashboard-deployment.md) and [verification](verification.md)
for complete maintained contracts and the current unresolved release gates.
