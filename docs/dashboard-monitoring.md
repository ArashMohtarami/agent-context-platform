# Monitoring and audit UI (V6 Phase 14)

Complete-source release acceptance remains pending. The final-fence natural
expiry regression now passes its focused real MongoDB gate. See
[verification and known limits](verification.md).

My workspace, Team overview and Leadership overview use the Phase 8 authorized
read model. Navigation reflects explicit metadata grants; server policy remains
authoritative. An owner role grants no automatic monitoring/corpus bypass.
Personal views require `activity:read:self` for the person and its project
selector; team views require `activity:read:team` for the current delegated team
and projects. Organization metadata uses explicit `activity:read:org`.
`audit:read:org` independently authorizes governance. Team leads cannot widen
these views by changing a URL, filter, hidden control or organization header.

Visible organization, event-time team, project, client, time window, timezone,
bucket and outcome filters are sent to the server. Instants use inclusive start
and exclusive end; positive windows are at most 31 days within the served
90-day horizon. IANA timezone changes server bucket boundaries, not stored event
times. The browser draws only authorized server buckets, spacing them by time.
It does not aggregate an unrestricted event dataset or fill missing periods with
zeros. Every chart has a textual summary, sample population and table alternative,
with filtered drilldowns. Current own grant expiry is a separate effective-access
snapshot, not a historical or team-wide count.

| Measure | Declared source and denominator |
| --- | --- |
| Recorded calls and outcomes | Permitted persisted `agent_activity` gateway reports; not every REST/MCP call |
| Observed clients | Distinct verified event-time machine client IDs; sample population is permitted verified reports, not current inventory or presence |
| Mean reported latency | Finite nonnegative gateway-reported milliseconds; missing/invalid samples excluded; sample count and permitted-report denominator returned |
| Older unattributed events | Retained reports without verified event-time sponsorship; no retrospective identity/team inference |
| Access review decisions | Separately authorized governance `access_request_decided` events, divided against permitted committed governance events; not all corpus reviews |
| Grant expiry | Own current effective-access source explanations, with effective/ineffective status and recorded expiry; no broad policy graph inferred |

Gateway review counts remain unavailable: this stream has no declared corpus
review history. No process-local last-observation metric becomes a historical
series. A collector's `missing`, `unavailable`, `stale`, `partial`, `complete`
and `absent_history` states remain distinct. Declared missing intervals,
watermark, collection/retention start, declaration check time, generated time,
population and timezone are visible. Zero observations with incomplete coverage
are not a complete zero. Coverage describes collection, not a person’s output.

Activity separates verified actor/client, human sponsor, service-account identity
and event-time team. Own/team visibility uses the same Phase 8 predicate as
counts, buckets and exports. Current worker membership never relabels old events;
a team move does not reveal the previous team's history. Legacy actors remain
unknown. Last seen is not employee presence or productivity. Leadership metadata
grants reveal no corpus text or private agent memory. No prompts, transcripts,
response bodies, search terms, guessed hours/model spend, productivity rankings
or causal output scores are introduced.

The new session-only `GET /api/v6/organizations/{org_id}/audit-diff` accepts the
bounded governance filters plus `event_id`. It rechecks `audit:read:org`, current
policy and served retention under the shared policy fence. Its before/after
view allowlists status, fixed role, revision/generation, expiry, delegation depth,
fixed allowed roles, selector modes and action/resource grant shapes. Resource
IDs, target identities, names, approval references and arbitrary payloads are
withheld. Missing or unsafe fields say “not recorded / withheld”; legacy or
unsupported snapshots are unavailable. Current policy never substitutes for a
historical snapshot. Existing event lists and CSV still exclude raw changes.

Summary, timeline, diff and CSV reads aggregate within a bounded snapshot, then
recheck the current visibility and perform an atomic majority write against the
unchanged policy anchor before delivery. Aggregation holds no long-lived anchor
write lock. Revoke/narrow wins if it commits before that final confirmation;
governance mutations retain their transactional shared-anchor fence. A new or
narrower served-retention boundary also changes the anchor revision. This uses
MongoDB's documented [conditional single-document atomicity](https://www.mongodb.com/docs/manual/core/write-operations-atomicity/)
and [transaction conflict behavior](https://www.mongodb.com/docs/manual/core/transactions-production-consideration/).
Up to three attempts refresh session/policy after a transient database conflict.
An exhausted/bounded query returns an error, never invented observations.
Detail/report operations have three-second database deadlines; a cold overview
has a ten-second ceiling and can reuse a bounded source-invalidated aggregate
cache. Its displayed generation time remains the original observation time.
Every reuse still rechecks current policy. No-disk-spill bounds remain; these limits
are not measured production throughput promises. There are no new dependencies,
collections, migration versions or background collectors.

Reports require a separate `reports:export` organization grant and recent MFA.
Preparation records bounded filters; download rechecks recipient, current
membership/policy revision, retention and five-minute expiry. After rendering,
delivery independently rechecks `reports:export` as well as activity visibility;
longer-lived activity rights cannot replace an expired export grant. Expiry is
checked synchronously after the final database await. Reports are at most
5,000 rows and 2 MiB, with server-side spreadsheet-formula neutralization.
Narrowing served retention invalidates earlier report receipts; request a new
report under the current boundary. The
browser fetches using its opaque session/context, validates the same-org report
path, bounds bytes, guards late responses by context generation and releases the
temporary Blob URL. It stores no CSV, report descriptor, token or persistent data
cache in browser storage. Expired/revoked downloads fail; previously authorized
saved files cannot be remotely erased. Raw shared metrics and whole-database
backup/restore remain installation-operator operations.

## Verification

```sh
python -m pytest -q tests/unit/test_agent_activity_api.py tests/unit/test_content_metrics.py tests/unit/test_search_observability.py
python -m pytest -q tests/unit/test_v6_dashboard_read_models.py tests/unit/test_v6_report_visibility.py
V6_SUMMARIES_DEVELOPMENT_ONLY=1 bash scripts/test-v6-isolation.sh
npm --prefix dashboard run test:unit -- src/overview/charts.test.tsx src/activity/activity.test.tsx src/audit/audit.test.tsx
npm --prefix dashboard run build
npm --prefix dashboard run test:e2e -- e2e/monitoring.spec.ts e2e/audit.spec.ts
```

The integration selector reuses the requested Phase 8 aggregation/report files,
extended with actual latency samples, client cardinality, safe diffs and
concurrent read fences. Browser fixtures reuse Phase 8 preparation with identical
logical IDs and different private sentinels in two organizations. They compare
API counts, outcomes, latency samples, permitted event IDs and UTC/Asia-Tehran/
New-York bucket boundaries against independently inspected raw fixture events,
not screenshot text or page lengths. Governance totals also reconcile with raw
committed events. The second-organization fixture preserves its legacy-retirement
anchor and revoked keys, prepares an explicitly empty test root and obtains
metadata-only grants through the actual operator approval transaction. This is
fixture setup, not a new owner claim or bootstrap endpoint. Journeys exercise real persisted sessions/API/MongoDB, safe
diffs, formula-safe CSV, expiry and revoked export authority. Screenshots, local
Graphify graphs and planning remain ignored artifacts. Record the actual browser
engine/version and outcomes for each run; no other engine coverage is implied.
