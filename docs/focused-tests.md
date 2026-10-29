# Focused test runner

Use the maintained profiles in [v7-phases.json](../tests/focused/v7-phases.json)
to run the cases relevant to the focused-runner change. Profiles are explicit;
there is no default suite or arbitrary extra-argument passthrough.

From the checkout root, inspect a plan before running it:

```sh
.venv/bin/python scripts/test-focused.py --phase p01-unit --plan
.venv/bin/python scripts/test-focused.py --phase p01-unit --run
```

Planning collects only named Python files/nodes or the named browser file/title.
It starts no services or test fixtures. The output includes concrete parameterized
nodes, child argv, deadlines, fixture resources, readiness and cleanup operations.
Generated passwords, ephemeral ports, database names and Docker IDs are runtime
bindings; they are not credentials to supply manually. Execution recollects the
same files and validates the saved graph before starting services. Source, tool
selection or collection changes require a new plan. Each plan can run once.

| Profile | Selected behavior | Infrastructure | Job limit |
| --- | --- | --- | --- |
| `p01-unit` | Runner validation, processes, result reconciliation, ownership and two existing verifier regressions | None | 180 seconds |
| `p01-stack` | One real startup, search query and intentional-error cleanup case | MongoDB + mongot | 600 seconds |
| `p01-browser` | One browser check plus one separately reconciled Python serving harness | MongoDB + mongot + HTTPS Keycloak/API | Browser 120 seconds; serving startup 240 seconds and shutdown 30 seconds |
| `p01` | The preceding three jobs, sequentially | At most one disposable stack | The same per-job limits |
| `activation` | Three required unit modules, eleven non-cutover real cases, then the cutover case | Separate disposable MongoDB + mongot stacks for real and cutover jobs | 180 / 900 / 300 seconds |
| `p02-runner` | Evidence-path, hosted ownership, narrow selection and plan regressions | None | 60 seconds |
| `p02-hosting` | Original local-only TLS/startup case and nearby MFA/session/bearer regression | MongoDB + mongot + Keycloak + cached production API/console images | 900 seconds; original API readiness 90 seconds |
| `p02-browser-directory` | Only the original browser performance journey and its serving harness | The same production-image fixture | Browser 120 seconds with original 90-second test limit |

The `activation` profile is selected separately. It supplies the cutover case with
the genuine earlier JUnit reports. It preserves the existing verifier's six-module,
source fingerprint and no-skips requirements. Unit-only profiles do not run it.
This rehearsal is not production activation or release approval; the existing
[verification contract](verification.md) still applies.

## Prerequisites and isolation

Install the pinned Python development environment described in
[CONTRIBUTING](../CONTRIBUTING.md). Real fixtures require Linux, a local Docker
daemon at `/var/run/docker.sock`, Compose, and the exact images already available
locally from [the isolation definition](../tests/infrastructure/v6-isolation.compose.yml)
and, for browsers, [the OIDC definition](../tests/infrastructure/v6-oidc.compose.yml).
The runner uses `--pull never`; missing infrastructure is an incomplete check.
MongoDB and mongot each have a 2 GiB container limit; optional Keycloak has 1 GiB.
Replica and search readiness each have a 120-second limit, and OIDC readiness has
180 seconds. Individual infrastructure commands have 30-second limits, startup
commands 60 seconds and replica probes 10 seconds.

Browser profiles also require the Node version in
[dashboard/.node-version](../dashboard/.node-version), installed dashboard
dependencies and an installed Chromium executable. The `p01` browser fixture
also needs a built `dashboard/dist/index.html`; hosted profiles serve image assets.
Hosted profiles use the cached
`acp-v6-api:phase15` and `acp-v6-console:phase15` images built from the maintained
Dockerfile; startup uses `--pull never`, and the fixture verifies the API runtime
fingerprint and records actual image IDs. The default browser executable is
`/opt/google/chrome/chrome`. Choose explicit alternatives when necessary:

```sh
ACP_FOCUSED_NODE=/path/to/pinned/node \
ACP_FOCUSED_CHROMIUM=/path/to/chromium \
  .venv/bin/python scripts/test-focused.py --phase p01-browser --plan
```

Use the same choices for `--run`. Build frontend prerequisites with the maintained
[dashboard instructions](../dashboard/README.md). The `p01` browser case
checks TLS API access, unauthenticated denial, fixture control and browser startup;
it does not establish SPA behavior, production hosting or external client compatibility.
Node verifies the fresh fixture CA. Chromium's temporary profile receives only
the generated loopback certificate's public-key pin, while `ignoreHTTPSErrors`
stays false. Chromium's [verifier contract](https://chromium.googlesource.com/chromium/src/+/HEAD/services/network/ignore_errors_cert_verifier.h)
describes this scoped allowlist; unrelated keys use ordinary verification. No
system CA is installed. The Phase 2 performance profile uses the original
production-image surface, preserves the first concurrent wave and all original
assertions, and keeps failed attempts with their actual sample counts.

Inherited `V6_*`, `PYTEST_*`, Compose and routing overrides are refused, including
`DOCKER_HOST` and `DOCKER_CONTEXT`. Use a clean shell or explicitly unset a known
override, for example `env -u DOCKER_HOST ...`; never point the fixture at a live
database. Provider credentials and application connection settings are not passed
to child processes, and focused pytest disables deployment dotenv loading before
test modules import. No paid model call or model download is part of these profiles.

Every stack uses fresh credentials, private state, unique names, loopback ports
and ephemeral database/search storage. Cleanup validates the exact names, run
labels and Compose project of **all** resources before removing any. It never
prunes Docker resources or stops another project. Process timeouts and catchable
interrupts stop owned process groups and trigger cleanup. An uncatchable kill,
host crash or unavailable daemon can leave resources: retain the private plan and
logs, verify their recorded ownership and remove only those specific resources.

## Results and extending a profile

Plans, genuine JUnit, child logs, temporary fixture data and reconciled JSON stay
under the ignored `.private/v7-competitive-planning/phase-1/runs/` directory;
`p02-*` profiles use the separate `phase-2/runs/` directory.
Keep this data private. Summaries record selected, collected, passed, failed,
skipped and expected-failure counts. Missing reports/counts are incomplete, never
successful zero-test runs. Any required skip, xfail, error, failure, timeout or
selection mismatch fails the check. Logs preserve the underlying child failure.

Before adding a profile, map its behavior to exact files/nodes or one browser
title; review fixture subprocesses and dependencies, then set expected counts and
bounded deadlines. Directory selectors, wildcard selectors, options and arbitrary
shell commands are rejected. A named module may contain parameterized cases:
planning resolves them into the final exact node list, and execution reconciles
every case. Do not add broad wrappers as children or weaken release gates.

The maintained manifest and fixtures are MIT project source. Private plans,
reports, generated credentials and local toolchains are excluded from public
source archives, package builds and the guide inventory.

## Disposable commissioning (Phase 3)

`p03-lifecycle` selects the evaluation lifecycle's 26 pure planning/refusal cases;
`p03-config` selects four concrete service-input/preflight cases. `p03-unit` is an
explicit 62-case inventory combining lifecycle, production deployment/Compose
and the two named runner regressions; it is not automatically run by other jobs.
Select the changed boundary, inspect its plan, then run that same plan.

`p03-bootstrap` selects the genuine activation dependency in its original order
(21 unit, eleven real non-cutover, one cutover), then exactly one distributed
empty-store lifecycle case. It executes `scripts/evaluate.sh`, real private
MongoDB/mongot, current API/console images, strict scoped CA trust, actual
invitation/password/verification/MFA endpoints, independent owner approval and
recipient-only issuance. It exercises a real SIGTERM/resume, repeated start,
stop and scoped reset. No privileged fixture rows stand in for commissioning.

```sh
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p03-bootstrap --plan
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p03-bootstrap --run
```

Use the same cached `acp-v6-api:phase15` and `acp-v6-console:phase15` build inputs
already described above, built from the reviewed source with the pinned
Dockerfile. The API runtime fingerprint and service image IDs are checked. No
pull/build/model download occurs in this profile. Its distributed case retains
the 1,800-second limit; the dependency's original 180/900/300-second limits stay
separate. Only one real stack runs at a time. Required skips/failures are incomplete.

The completed activation dependency gets a receipt only after all three genuine
jobs reconcile, so the commissioning job can consume it while its parent profile
is still running. Production XML verification is unchanged. Cached receipts must
match current source, selectors, reports and actual service image IDs. A passing
evaluation does not approve a production release.

Phase 3 reports live privately under
`.private/v7-competitive-planning/phase-3/runs/`. Failed evaluator cleanup retains
only scoped logs and redacted manifest/config diagnostics; invitation messages,
backups and secret files are not evidence exports. Runtime evaluation state is
separately owned under `.private/evaluations/` and excluded from source/builds.
See [the implemented evaluation path](evaluation.md) for operator review, trust,
human effort, one-time delivery, stop/resume and cleanup requirements.

## Connection preview and diagnosis (Phase 4)

`p04-unit` inventories the 80 named integration/configuration cases. `p04-doctor`
selects only the 46 new doctor/CLI cases when that is the changed boundary.
Neither starts services or activates isolation. The console component check is
only `src/clients/onboarding.test.tsx` with non-watch Vitest, not the default
unit-test wrapper.

`p04-real` selects the genuine 21+11+1 activation dependency and one distributed
Phase 3 commissioning/doctor case. Before planning/running it, build the current
API with cached pinned inputs into the separate `acp-evaluation-api:p04` tag:

```sh
docker build --network=none --pull=false --target production -t acp-evaluation-api:p04 .
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p04-real --plan
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p04-real --run
```

It uses the cached `acp-v6-console:phase15` console for commissioning; the new
console preview is separately exercised by `p04-browser` on the freshly built
SPA and real in-pytest HTTPS/session fixture. That profile selects only
`e2e/evaluation-onboarding.spec.ts` title `connection preview preserves policy and
guides recipient diagnostics`. Global setup only attaches to the selected owned
fixture. It does not run other journeys or provide installer evidence.

The aggregate `p04` is an explicit union; do not run it merely to repeat valid
boundary evidence. Inspect the chosen profile's plan first. New phase reports
stay under ignored `.private/v7-competitive-planning/phase-4/runs/`. Real access
proof includes scoped CA validation, effective read-only grants, actual retrieval,
forbidden write and credential revocation over both MCP transports. These checks
use fictional recipients, no paid provider and no active client config. A vendor
label on a protocol connection is not vendor-client compatibility certification.

## Notes API handoff (Phase 5)

Run the six teaching tests from `examples/starter-notes-api` with
`../../.venv/bin/python -m pytest test_app.py -q`, then return to the root.
The runner deliberately accepts only named maintained test paths; it does not
gain arbitrary example-directory execution or nested test wrappers.

`p05-unit` selects the handoff validation/checkpoint/gate module and the existing
guide walkthrough module (31 resolved cases). The separately selected
`p05-errors` is the three-case safe MCP error-envelope parser regression; use it
when only that boundary changed. `p05-runner` selects eight exact
source-identity/saved-plan/argv regressions. `p05-smoke` selects only
`tests/integration/test_agent_handoff.py::test_real_protocol_handoff_and_boundaries`
with a 600-second job deadline. It runs the distributed example using real
disposable MongoDB/mongot, scoped TLS, a stdio child and fresh HTTP MCP session,
independently approved recipient-issued clients and actual connected revocation.
Private reports contain exact returned identity/content and source hashes, never
credentials. This maintained synthetic fixture is not installer evidence.

`p05-regressions` selects only the three existing connected-stdio revocation
variants, three connected-HTTP variants and the one coupled project/private-memory
case (seven total; 900 seconds). `p05-references` selects only the new real
private-memory own-client reference case and the existing foreign-reference/
project/global-memory case (two total; 600 seconds), for the narrow reference
fix exposed by the handoff. Inspect the selected `--plan` before `--run` as
above; no activation dependency is implicit. The aggregate `p05` is an explicit
union, not a request to replay passing cases. Evidence stays under ignored
`.private/v7-competitive-planning/phase-5/runs/`. Handoff code/fact, Notes source/
tests and the guide source now join the runner fingerprint; edits invalidate the
affected plans. The production activation verifier is unchanged.

See the [repeatable recipe](../examples/agent-handoff/README.md) and labeled
[storyboard](../examples/agent-handoff/storyboard.md). Neither protocol sessions
nor fixtures establish installed vendor-client compatibility or release approval.

## Retrieval benchmark

The [fictional retrieval benchmark](../benchmarks/retrieval/README.md) has separate
`p06-unit` (40 cases), `p06-export` (2 cases), `p06-runner` (9 cases) and `p06-real`
(one real study) profiles. Inspect `--plan` before `--run`. The real profile
requires an explicitly selected, hash-locked offline model cache and only its
owned MongoDB/mongot fixture. It checks the actual permitted corpus before the
60-query, three-arm study, records every attempt privately and verifies project
refusal and credential revocation. It does not select activation or browser work.

`p06-model`, `p06-lexical` and `p06-task` select the named affected guards after
model-loading, fixture/parser and task-oracle corrections. They are narrower
follow-ups, not extra release gates. Query labels, actual current grants and
record identities must agree; a partial authorized export is an invalid quality
comparison. Reports, including failures and invalidated attempts, stay private
until reviewed. Coding-task benefit requires a separately authorized provider
study and remains unmeasured by the retrieval run.
