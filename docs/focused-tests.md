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
dependencies, a built `dashboard/dist/index.html` for the existing serving harness,
and an installed Chromium executable. The default executable is
`/opt/google/chrome/chrome`. Choose explicit alternatives when necessary:

```sh
ACP_FOCUSED_NODE=/path/to/pinned/node \
ACP_FOCUSED_CHROMIUM=/path/to/chromium \
  .venv/bin/python scripts/test-focused.py --phase p01-browser --plan
```

Use the same choices for `--run`. Build frontend prerequisites with the maintained
[dashboard instructions](../dashboard/README.md). The selected browser case
checks TLS API access, unauthenticated denial, fixture control and browser startup;
it does not establish SPA behavior, production hosting or external client compatibility.
Only the fresh fixture CA is trusted by the test process; no system CA is installed.

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
under the ignored `.private/v7-competitive-planning/phase-1/runs/` directory.
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
