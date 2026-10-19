# Verification and known limits

The implementation and its acceptance evidence are separate. **Release readiness
is not approved.** The latest complete acceptance attempt has blocking failures;
this documentation and clean export do not override them. Do not deploy a
management console on the strength of component tests or documentation builds.

## Required evidence

| Gate | Required proof | Current status |
| --- | --- | --- |
| Tenant adoption/isolation | Real MongoDB/mongot; preserved pre-V6 IDs/references, repeated logical IDs in two orgs, all REST/MCP/jobs/search/export boundaries | Focused evidence exists; final complete-source reconciliation remains required |
| Identity and authority | Real local/MFA/session flows and supported Keycloak; provider substitution, account linking, coupled grants, independent review, concurrent revoke/issue and owner protection | Full final-source acceptance remains open |
| Distribution and recovery | Real same-origin TLS images, local and OIDC setup, materialized all-collection backup and isolated recovery including encryption keys | Focused rehearsals do not substitute for the final release gate |
| Browser and accessibility | All committed journeys plus keyboard/focus/true 200% zoom, light/dark, 360/768/1440px, conflict/empty/error/coverage states | Manual review exists with limitations; the final complete browser run remains required |
| Performance | Declared hardware/corpus/sample counts and successful response latency, including skew and concurrent authorization changes | Blocking million-event overview failures; full reference corpus unmeasured |
| Documentation/artifacts | Focused tests, rebuilt guide/site, reproducible archives, extracted source/license/secret audit | Must be recorded against the final reviewed source |

## Observed blockers and measurement limits

A real persisted report test reproduced an authorization-expiry race at the
final policy fence: an export right expired while a longer activity-read right
remained valid. A candidate fix has passed a focused diagnostic, but it is not
accepted until applied and verified against final source with its regression.

The current million-event overview workload is not within its budget. Both
90/10 and 50/50 tenant distributions produced policy/deadline refusals, and the
isolated successful request exceeded the target. Refusals are not successful
latency samples. A private covering-index experiment still scanned 900,000 keys
and exceeded the deadline; it does not establish a shipped fix.

Measurements used a shared four-physical-core/eight-logical-CPU laptop with about
16 GiB RAM, rather than a dedicated reference host. Real hybrid retrieval passed
a focused 100,000-matching-chunk-ID-per-org workload using the local 384-dimensional
model, with repeated synthetic text prototypes. That establishes neither
relevance quality nor the entire mixed reference corpus. The remaining context,
memory, draft/candidate populations, ingestion contention and high-cardinality
policy shapes need representative measurement. No capacity or throughput
certification is claimed.

Automated frontend checks and an agent-operated real-browser manual review
supplement each other. The review covers keyboard/focus, dialog return, true
browser zoom, themes, responsive layouts and representative data states. It is
not a physical assistive-technology review or a WCAG 2.2 AA conformance claim.
The built frontend still emits a large-chunk advisory; measured gzip budget
checks do not imply route splitting or field Web Vitals certification.

## Reproduce and reconcile

Use a disposable environment only. The full runner requires the pinned Node
version, a real Chromium executable, the reviewed local embedding model cache,
Docker and supported image/tool availability. It refuses inherited development
selectors, skipped/missing tests and source changes during the run. Reports and
screenshots belong in ignored operator storage, never the public guide registry.

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
