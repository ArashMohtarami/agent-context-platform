# Authorized summaries and reports

Phase 8 adds server read contracts, not a dashboard. It requires an activated
Phase 7 installation, verified human browser sessions and current organization
policy. Existing bearer/MCP interfaces cannot authenticate these routes with a
browser cookie. There are no new dependencies or migrations: schema head 42 is
unchanged. Existing classified governance storage holds immutable checkpoint
and report descriptors. Report contents are generated on download, never cached
on disk, in an outbox or in an idempotency record.

| API | Contract |
| --- | --- |
| `GET /api/v6/organizations/{org_id}/overview` | Own/team/org summary, outcomes and sparse hour/day buckets |
| `GET /api/v6/organizations/{org_id}/activity` | Same visibility, observed count and signed keyset pagination |
| `POST /api/v6/organizations/{org_id}/reports` | Approve bounded filters; return an expiring recipient-bound download path |
| `GET /api/v6/organizations/{org_id}/reports/{report_id}/download` | Reauthorize, apply current retention and generate neutralized CSV |

Existing `/api/v6/me/effective-access` and
`/api/v6/organizations/{org_id}/members/{user_id}/effective-access` provide the
Phase 6 authorized explanations. No global user search or private identity DTO
is introduced. Overview and activity accept `source=gateway_activity|governance`,
`scope=own|team|org`, inclusive `start`, exclusive `end`, IANA `timezone`,
`bucket=hour|day`, and optional exact `team_id`, `project_id`, `client_id`,
`outcome` filters. Governance uses org scope and rejects team/project/client
filters. UTC-aware instants are required. Windows are positive, at most 31 days,
within the last 90 days, and end no later than the request time. Pages contain
1–100 events, ordered by event identifier. Cursors bind source, filters, tenant,
subject and current policy revision. Changing any of these requires a fresh page.

## Visibility and attribution

Roles grant no automatic access. Own views require `activity:read:self` on the
caller as `member`, plus that same action's `project` selector. Team views require
`activity:read:team` on the named currently delegated active team, plus that same
action's project selector. NONE is empty. A read/write grant on another action
cannot widen these selectors. Events without a classified project are excluded
from these project-bounded views. Current worker membership is never joined to
historical activity. A team move grants no access to the worker's previous team.
The prior team can still inspect its permitted historical events.

An explicit `activity:read:org` assignment on `organization` permits the safe
organization metadata stream, including legacy unattributed events and events
outside corpus read grants. This supports approved executive metadata views;
it does not grant any corpus body. `audit:read:org` independently permits the
redacted governance stream. Team leads cannot use an org selector to bypass
their current team boundary. Foreign filters yield no foreign totals.

Machine actor/client, service-account identity, event-time sponsor and event-time
team are separate fields. `occurred_at` is the source record's server-created
timestamp; gateway reports do not establish a remote tool's independent clock.
New governance writes capture verified actor and a single unambiguous active
team at commit time; multi-team actors have no inferred single team label.
Legacy governance events and unsponsored activity retain `legacy_unattributed`.
No historic record is rewritten from today's memberships. Output is an allowlist:
time, opaque event ID, declared action/outcome, actor kind/ID, sponsor/team and
project ID. It excludes names, emails, targets, policy changes, approval text,
keys, prompts, retrieval queries, transcripts, response bodies and resource
payloads. Unknown operation/action text becomes a fixed fallback label.

## Sources, completeness and retention

`gateway_activity` reads persisted V4 `agent_activity`. Its operation/outcome
labels are gateway reports, with server-derived sponsorship; it is a best-effort
stream, not every REST request, employee activity, cost or productivity.
`governance` reads committed transactional `governance_events`. It excludes
`summary.*` control records. Best-effort domain `audit_logs`, private identity
security history, retrieval logs containing query text and process-local metrics
are excluded. Neither readiness nor last-observation gauges become history.

An operator explicitly checkpoints the known stream through the current snapshot:

```sh
contextctl --org-id fictional-org summaries checkpoint --declaration-file checkpoint.json
```

The bounded JSON declaration contains `source`, UTC `coverage_start`,
`retained_from`, `available`, optional `gaps` (at most 32 positive `start`/`end`
intervals), and an `approval_reference`. Coverage cannot be
backdated before schema 42 preparation. The earliest retained observations can
precede guaranteed collection; those observations remain partial/unattributed.
Subsequent checkpoints preserve the collection start and only advance the
retention floor. Checkpointing acquires the shared policy anchor and commits
its declaration with governance audit/outbox. It does not advance the policy
revision or fabricate a background collector. Operators must refresh the
checkpoint when they can attest to continued collection; after 15 minutes its
state is stale. Declared gaps are preserved across checkpoints. Restoring an
unavailable source records its outage interval; a window overlapping a gap
remains partial. Gaps wholly before an advanced retention floor can be retired.
`watermark` is the declared snapshot-through instant, not a
process observation or the last event in one page. Population and collection
contract are included in every response.

Coverage states are `missing` (no declaration), `unavailable` (disabled source),
`stale`, `absent_history` (window wholly before the served retention floor),
`partial` (window crosses coverage/retention/watermark, or any best-effort gateway
stream), and `complete` (covered transactional governance stream). A separate
`observation_state` distinguishes recorded events from no recorded events.
`observed_count=0` alone never claims collection was complete. Sparse buckets
represent recorded events only: missing buckets are not synthesized zeros.
Counts, buckets and details use one MongoDB pre-aggregation visibility predicate,
within one snapshot serialized with policy revocation. A page length is never a
total. Timezone changes bucket boundaries, not stored event instants.

The served retention floor applies to detail, aggregate and downloads even when
installation archives retain older events. Physical deletion/archive retention
remains explicitly `installation_managed`; Phase 8 does not claim an automatic
governance TTL or tenant backup/restore. Whole-database backups and raw shared
metrics remain operator-only.

## Reports and operational bounds

`reports:export` on the organization is additionally required. The fixed Phase 5
catalog allows owner/admin/auditor export assignments; an executive aggregate
assignment or a team lead role alone cannot export. Creation requires CSRF,
Origin and recent MFA. Downloads require the same recipient, active browser
session, recent MFA, unchanged policy/membership revision, current source
visibility and an unexpired ten-minute descriptor. Revocation/reinstatement or
widening cannot resurrect an old report. Links grant no bearer authority.
Current retention can further narrow a previously requested report.

Reports are synchronous because their work is bounded: at most 5,000 rows and
2 MiB UTF-8 CSV, with a three-second MongoDB operation deadline and no disk
spilling. Overflow refuses delivery; it never silently returns the first page.
Narrow the time/filter window. CSV quoting and formula neutralization cover
`=`, `+`, `-`, `@` after whitespace/control/BOM prefixes. Download headers include
`X-Summary-Metadata` (source, window, timezone, observed population/count and
coverage), `no-store`, `nosniff` and a fixed attachment filename. No persisted
report payload, async job or downloadable temporary file is needed at these
bounds. These are limits, not measured production performance results.

The focused real gate runs in disposable MongoDB using matching project/client
IDs in both organizations. Use `scripts/test-v6-summaries.sh` for prerequisites
plus Phase 8, or `V6_SUMMARIES_DEVELOPMENT_ONLY=1 scripts/test-v6-isolation.sh`
for the complete two new integration files on independent disposable stacks.
Neither command reads deployment credentials or sends real mail.
