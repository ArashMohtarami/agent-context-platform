# Verification and known limits

The implementation and its acceptance evidence are separate. **Release readiness
is not approved.** Full release reconciliation remains open. An earlier complete
attempt had blocking failures; later focused evidence resolves individual issues
without approving the current public source as a release. Do not deploy a
management console on the strength of component tests or documentation builds.

## Required evidence

| Gate | Required proof | Current status |
| --- | --- | --- |
| Tenant adoption/isolation | Real MongoDB/mongot; preserved pre-V6 IDs/references, repeated logical IDs in two orgs, all REST/MCP/jobs/search/export boundaries | Focused evidence exists; final complete-source reconciliation remains required |
| Identity and authority | Real local/MFA/session flows and supported Keycloak; provider substitution, account linking, coupled grants, independent review, concurrent revoke/issue and owner protection | Full final-source acceptance remains open |
| Distribution and recovery | Real same-origin TLS images, local and OIDC setup, materialized all-collection backup and isolated recovery including encryption keys | Focused rehearsals do not substitute for the final release gate |
| Browser and accessibility | All committed journeys plus keyboard/focus/true 200% zoom, light/dark, 360/768/1440px, conflict/empty/error/coverage states | Manual review exists with limitations; the final complete browser run remains required |
| Performance | Declared hardware/corpus/sample counts and successful response latency, including skew and concurrent authorization changes | Focused measurements include passing and failing results; final complete-source reconciliation remains required |
| Documentation/artifacts | Focused tests, rebuilt guide/site, reproducible archives, extracted source/license/secret audit | Must be recorded against the final reviewed source |

## Observed blockers and measurement limits

A real persisted report test reproduced an authorization-expiry race at the
final policy fence. The fix is applied: current grants are evaluated
synchronously after the last database await. Four focused real MongoDB cases
pass, covering export/activity expiry during rendering and the final CAS.
Complete final-source regression reconciliation remains required.

An earlier complete attempt ran 3,333 unit tests and eleven isolation rehearsal
tests successfully, with no skips. All 68 integration modules completed, with
three failed tests and two errors. The browser suite passed 49 of 50 journeys;
its failed journey is the directory latency measurement. A local-only hosting
case failed during API startup, and an organization-switch case had a disposable
database teardown replication timeout after its assertions passed. Both errors
were retained as failed outcomes, including the teardown error after assertions.

Later genuine reports pass the exact affected hosting and org-switch nodes,
including clean teardown. Their runtime and test fingerprints match the committed
baseline before the focused runner was added; the relevant org-switch sources
are unchanged. A fresh focused check also passes the original local-only
TLS-routing/startup case and its nearby local MFA/session/bearer regression after
the focused hosted-fixture cleanup changes. Its API image verifies the current
runtime fingerprint. These are scoped resolutions, not a new full release run.

A subsequent run passed 3,347 unit tests and all eleven real isolation rehearsal
tests, then exposed a recovery-fixture readiness gap: restored records passed
their count checks, but the lexical probe reached a newly created search index
still in `INITIAL_SYNC`. That attempt was stopped and retained as incomplete.
The fixture now explicitly waits for both real search indexes to report READY
and queryable before recovery work. All six focused backup/recovery cases pass
with the wait; their original restore, count, lexical and vector assertions
remain required. A fresh complete-source run must still pass all modules.

The million-event overview workload failed at 1,111 ms warm p95 for the 90/10
tenant distribution, exceeding its 1,000 ms limit. The 50/50 distribution passed
at 588 ms. All twenty responses in each distribution succeeded; cold fills took
3.8 seconds and 2.4 seconds. These are one authenticated subject's twenty
concurrent identical requests after a cold fill, not twenty distinct people or
a continuous-write capacity claim. The tenant/time covering index and bounded,
source-invalidated aggregate cache preserve separate current-authority checks;
real late-insert/update/delete, foreign-insert and revocation checks pass.
Refusals are never successful latency samples.

Measurements used a shared four-physical-core/eight-logical-CPU laptop with about
16 GiB RAM, rather than a dedicated reference host. Real hybrid retrieval passed
a focused 100,000-matching-chunk-ID-per-org workload using the local 384-dimensional
model, with repeated synthetic text prototypes. That establishes neither
relevance quality nor the entire mixed reference corpus. A new full background
fixture includes context, all memory scopes, drafts/candidates, people, teams,
clients and projects. The real mixed-corpus run passes at 307 ms warm p95,
including all six skill/context/shared-memory lexical/vector paths. It uses
twenty serial samples paced within the session POST limit and verifies reviewed
`all_in_org` skill access above the ID-preselection ceiling. Repeated synthetic
prototypes establish throughput and isolation, not relevance quality.

Policy p95 in the complete attempt passed at 26.1 ms for repeated grants and
10.1 ms for distinct-resource grants across one hundred assignments. Sponsor
offboarding with five hundred clients passed; dedicated transport tests cover
active REST/stdio/HTTP MCP revocation. The clean ingestion-contention workload
failed its absolute directory budget: 1,148 ms baseline and 725 ms loaded p95,
against a 500 ms limit, despite passing the relative contention limit. Both
8 KiB console jobs completed and persisted six 384-dimensional chunks. The
browser's first twenty concurrent HTTPS directory requests also failed at
1,109 ms p95, with all responses successful. Its query plan examined 21
documents for the requested twenty rows and cursor probe. Earlier passing
profiled measurements do not replace these clean failures. No capacity or
throughput certification is claimed.

Follow-up focused checks pass after reducing fresh policy/session query round
trips and aligning integration fixtures with the shipped console's UTC decoding
and bounded 50/50 connection pool. First-wave HTTPS directory p95 was 389 ms;
million-event overview warm p95 was 604 ms for 90/10 and 558 ms for 50/50.
The two-job ingestion workload measured 173 ms baseline and 194 ms loaded
directory p95, a 12.1% increase within the 25% limit. Both 8 KiB jobs completed,
persisting six real 384-dimensional chunks; all twenty reader starts overlapped
ingestion. Connections initialize as configured; current authority is still read
from MongoDB on every request, without a cross-request authority cache.
Earlier failures and timing variability remain in the review
record; focused and instrumented diagnostic runs do not constitute complete
release approval.

The focused browser runner now supports the existing production-image fixture
with unique owned containers/networks and verified cleanup. A fresh directory
attempt against its in-pytest API fixture returned twenty HTTP 200 responses but
failed at 859 ms p95 against the unchanged 500 ms limit. That is a different
measurement surface from the original production-image journey and is retained
as a failure. An earlier attempt failed certificate trust before collecting any
latency samples. The focused browser now pins only its freshly generated fixture
certificate key in its temporary Chromium profile; Node verifies the fixture CA.
No system CA is installed. The original journey and its assertions are unchanged.
On the original production API/console image surface, the final focused attempt
passed: twenty first-wave HTTP 200 responses, 317 ms p95, 10,000 added fictional
members without added assignments, and 21 examined documents for the cursor probe.
It used the shared four-core/eight-logical-CPU, approximately 16 GiB host. The
in-pytest and production-image measurements are reported separately; they are not
pooled or selected as interchangeable repeats. This single passing wave does not
establish continuous-load capacity or close full release reconciliation.

Automated frontend checks and an agent-operated real-browser manual review
supplement each other. The review covers keyboard/focus, dialog return, true
browser zoom, themes, responsive layouts and representative data states. It is
not a physical assistive-technology review or a WCAG 2.2 AA conformance claim.
Heavy workspace modules are route-split with session-preserving loading/error
states. Build gzip budgets and lab Event Timing do not establish field Web
Vitals or assistive-technology conformance.

## Reproduce and reconcile

Use a disposable environment only. The full runner requires the pinned Node
version, a real Chromium executable, the reviewed local embedding model cache,
Docker and supported image/tool availability. It refuses inherited development
selectors, skipped/missing tests and source changes during the run. Reports and
screenshots belong in ignored operator storage, never the public guide registry.

For an individual named blocker, inspect and run the explicit `p02-hosting` or
`p02-browser-directory` profile described in the
[focused runner guide](focused-tests.md). Each selects only its named cases and
required services. Keep failed attempts, teardown outcomes and all successful
response samples; a later pass does not erase an earlier failure or approve the
full release gate below.

```sh
python -m pytest tests/unit -q
python -m pytest tests/integration -q
npm --prefix dashboard run lint
npm --prefix dashboard run typecheck
npm --prefix dashboard run test:unit
npm --prefix dashboard run build
npm --prefix dashboard run test:e2e
bash scripts/test-v6-acceptance.sh
```

The integration and browser commands need the disposable fixture environment;
see [Contributing](../CONTRIBUTING.md) and [deployment](dashboard-deployment.md).
The runner maps actual test functions in
[`tests/v6_acceptance_matrix.json`](../tests/v6_acceptance_matrix.json) and retains
real JUnit results. A command that exits successfully after skipping required
infrastructure is an incomplete gate. MongoDB mocks, prefiltered fake data,
first-page lengths and screenshot text cannot establish enforcement or totals.

Documentation-only checks are narrower:

```sh
python -m pytest -q tests/unit/test_public_release.py tests/unit/test_content_skill_catalog.py tests/unit/test_backup_deployment.py
python scripts/prepare_open_source_release.py --check
python website/build.py
python -m pytest tests/documentation -q
```

Retain the actual commands, source fingerprints, image IDs, counts, failure/skip
results, hardware/corpus, archive hashes and residual limits in the private
review record. Approval requires every blocking failure resolved against the
final source; a clean archive alone is not approval. No runner publishes,
deploys or pushes a release.
