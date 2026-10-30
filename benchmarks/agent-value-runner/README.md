# Agent-value experiment controls — model-free Phase 2

This is a small separate driver around the selected installed Codex CLI0.166.0,
not a replacement agent runtime. It prepares a literal Codex invocation and a
sealed three-arm schedule without starting processes, services, models or paid
requests. Only an explicitly authorized **synthetic stub validation** path can
launch. No vendor/model attempt, coding success or cash saving is established.
Unsupported accounting blocks all paid launch, even with an authorization file.

The driver lives separately from `benchmarks/agent-value/` so that Phase1's exact
initial task/checker seal remains unchanged. Those maintained prompts were pushed
to Git at the user's request: treat them as exposed source inputs, not an
unpublished blind holdout. Independent design/exposure/license review remains a
gate before calibration. Hidden labels/references/seals remain operator-private.

Use the checkout's Python environment, from its root:

```sh
.venv/bin/python -m benchmarks.agent-value-runner.driver plan --config REVIEWED_CONFIG.json
.venv/bin/python -m benchmarks.agent-value-runner.driver status --run-id RUN_ID
.venv/bin/python -m benchmarks.agent-value-runner.driver run --run-id RUN_ID --authorization APPROVAL.json
.venv/bin/python -m benchmarks.agent-value-runner.driver resume --run-id RUN_ID --reconciliation RECEIPTS.json
.venv/bin/python -m benchmarks.agent-value-runner.driver owned-cleanup --run-id RUN_ID
```

All placeholders are explicit local operator inputs, never shell templates used
by the driver. `plan` prints/saves its exact argv/environment/sandbox graph,
source hashes, task-seal hash, input/prompt/skill/tool-schema/price hashes and every
attempt ID. New state is under ignored `.private/v8-agent-value-planning/phase-2/experiments/`.
State paths are canonical, privately permissioned and ownership-checked. No
arbitrary executable/argv/environment/service/credential argument is supported.
Only the reviewed installed binary checksum is accepted; no active login/config
or model enumeration is read. The plan includes no provider secret value or hash.

Configuration keys are exactly `version`, `mode`, `study`, `task_ids`, `repeats`,
`model`, `effort`, `service_tier`, `currency`, `total_cap`, `attempt_reservation`,
`price_sha256`, `tool_schema_sha256`, `cli`, `bounds`, `stub_scenario`.
Empty, unknown, duplicated, mixed-split or widened inputs fail closed. Bounds
require positive wall/CPU seconds, memory/workspace/output bytes, turns, tool
starts and event count. Positive integer budget units avoid floating point.
The currency/price basis and unit conversion need prospective approval for real
use; `TEST` units in stubs are synthetic and are not a quote. No account/model,
effort, price, token cap or user budget is chosen implicitly.

`codex-plan` permits the complete sealed development8 or heldout24 task set
with all three arms and three repeats, or the exact development calibration12
tranche. The tranche is one attempt per arm on `harbor-development-import`,
`harbor-development-precision`, `parcel-development-pack` and
`parcel-development-tracking`. It retains positions7–9,31–33,43–48 of the
original development72 schedule; subsets and extra repeats fail closed.
A fixed digest ordering assigns balanced
arm permutations (12 of each for72 heldout blocks), seed20261030, with separate
development seed20261031. All schedule bytes are bound to the plan. A changed
sample needs a new reviewed prospective design. `stub-validation` permits only
one to four named development tasks, all three arms, one repeat, synthetic model
and `TEST` currency. Tests contain the complete literal synthetic configuration;
it does not authorize a provider. Price/tool-schema placeholders are explicitly
synthetic there and cannot close a real configuration gate.

An authorization receipt must bind version, receipt ID, named approving budget
owner, exact plan hash, purpose, currency, total cap, per-attempt liability,
complete ordered attempt IDs and an unexpired timezone-aware expiry. It is an
operator-supplied approval record, not generated independent human authority.
The agent cannot read it. A retained root registry binds each authorization to
one run, refusing replay after cleanup or a second create. Default no credentials are accepted, inherited or
placed in task tools, homes, plans or pytest. Future real key brokerage and
allowlisted Codex/MCP egress are not implemented and block paid execution.

One nonblocking file lock permits one worker in the fixed control root. Before
launch the full conservative liability is atomically reserved and the launched
state is persisted. Spent plus outstanding liability cannot exceed the cap.
Missing/error/timeout/interrupted attempts remain visible and uncertain liability
stays reserved. Resume requires complete unique receipt reconciliation, releases
each reservation once and never launches/retries an old ID. Real billing receipt
adapters remain unsupported; only labeled synthetic reconciliation is accepted.
Subsequent explicit run may launch remaining scheduled IDs after reconciliation.
No best-of, silent replay or zero-cost failed call. Cleanup refuses unresolved
liability, wrong owner/path, links and foreign files/directories; it neither
signals a saved PID nor prunes resources. The subprocess boundary reuses the
maintained focused `Child.stop` descendant/group cleanup, and state writes reuse
the evaluation lifecycle's atomic fsync/replace helper. Shared helpers are unchanged.

The synthetic process graph uses fixed system Python and the maintained stub,
Linux bubblewrap and libseccomp. Immutable copied task inputs mount at `/inputs`,
identical common skills at `/skills`, and a size-limited disposable tmpfs at
`/work` supplies a fresh client home. There is no host/home/checkout/Git/grader/
reference/sibling/credential/Docker mount. All network/socket and process-creation
syscalls are denied. Host networking is retained solely because this environment
denies network-namespace setup; denied sockets mean no declared egress at all for
stubs. CPU/address-space/file-output/FD/core quotas and wall/stream/event limits
are enforced. Raw stdout/stderr is never written to a log. Child text is filtered
in memory into known numeric events, then discarded; malformed/oversized/unknown
streams cannot authorize spend. A timeout/KeyboardInterrupt stops owned children
and preserves unknown liability. The stub never grades a task or impersonates
Codex. A writable coding-agent/tool/credential/route boundary needs later proof;
the restrictive stub sandbox is not claimed to run a real coding CLI.

`usage.py` recognizes only documented Codex completed-turn numeric counters and
event types, pinned to0.166.0. Turn records use local monotonic turn identities;
orphan/duplicate completions, failed/incomplete turns, new fields/versions and
unknown retry/compaction events produce explicit incomplete coverage. Numeric
request/turn/cumulative receipts are reconciled as one view: deduplicate IDs,
reject conflicting duplicates/mixed views, and use only the final monotonic
session watermark rather than summing cumulative updates. Generic normalization
subtracts cache/reasoning subcounts only under proven inclusion; synthetic
examples prove arithmetic, not the installed CLI's semantics. Current docs do
not establish its complete failed-request billing, inclusion or resolved-model
coverage: raw known numbers remain available, normalized totals/resolved model/
cash spend remain null. Unknown is never silently zero. Tool/message/command/
argument/auth/reasoning text and arbitrary unknown field names are discarded.

`adapter.json` records official sources and the unresolved semantics. Current
[noninteractive documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
shows turn usage events. The [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
documents effort/tier, isolated configuration and skills. Documentation is newer
than the pinned binary: no config/tool support beyond observed help is certified
by stubs. Required paid gates include hard request/input/output limits, receipt
coverage, resolved model/routing, key brokerage, real tool/skill config equality,
real MCP commission/activation, independent review and explicit selected budget.

Two native skill wrappers preserve the exact bodies of trusted MIT Git sources
`skills/python/testing.md` and `skills/engineering/debugging.md`, with reviewed
name/description frontmatter conversion. Source/package hashes are pinned in
adapter.json and each plan; disposable packaging is identical in R/M/C, with
equal discovery instructions. No new canonical skill or runtime import is needed.
No special MCP skill is enabled; any future such guidance is a separately sealed
development ablation. No marketplace/global install or active configuration edit.
Independent review/installed discovery and catalog-token overhead remain gates.

Focused profiles are separately selected `v8-p02-runner` (four exact nodes,180s),
`v8-p02-usage` (three,60s), `v8-p02-mapping` (one,60s) and `v8-p02-export`
(three affected E1 nodes,180s). `v8-p02-ledger` selects only the strengthened
budget/resume node (one,90s), retaining the other three runner cases. Inspect each saved `--plan` before `--run` using
`scripts/test-focused.py`. Provider keys never pass through that runner. All
inputs are original MIT project sources; inventories are exact file lists,
never an open results/private directory. This phase stops before real MCP setup,
calibration, skill activation or any provider launch.

Public CI runs portable contracts in `tests/unit/test_agent_value_tasks.py`,
`test_agent_value_runner.py` and `test_agent_value_usage.py` without private
labels, a Codex installation or sandbox prerequisites. The original sealed
grader and installed-CLI/OS validation modules live at
`tests/integration/test_agent_value_tasks.py` and
`tests/integration/test_agent_value_runner.py`. Their exact focused selectors,
counts and assertions are preserved in `tests/focused/v8-phases.json`, including
the prerequisite-bound `v8-p01-fixtures` and `v8-p02-runner` profiles. Missing
private seals, reviewed CLI inventory or sandbox support still fail those
explicit validations; public CI neither skips them after collection nor creates
replacement labels. Portable unit success does not establish private grader,
installed vendor CLI, OS isolation, model or provider evidence.

The Phase4 additions are model-free preparation. `calibration.py` reconciles
all12 IDs and refuses stale, duplicate and synthetic-success records; it never
grants a final lock. `delivery.py` produces an explicit complete M/C snapshot
using the existing API content normalization and exactly the `get_context`
metadata fields. Actual API identities/revisions are distinct from authored
document revisions. Controller/storage fields stay outside agent inputs. Its
grader bridge verifies the complete protected overlay, rejects symlinks and
reconstructs only permitted code for the unchanged sealed behavioral grader.
It does not import a snapshot, issue a credential or certify live MCP use.

`analysis.py` implements the prescribed paired family bootstrap and project
sensitivity with a pinned SHA256 rejection stream, type7 interval quantiles,
nearest-rank observed-success latency and all-attempt efficiency ratios.
Missing numerators stay null; any zero-denominator bootstrap draw makes the
ratio interval undefined. Every function leaves claim approval false. The
`v8-p04-calibration` profile selects exactly three portable behavior nodes;
`v8-p04-regressions` selects only the changed mapping R1 and inventory E1 nodes.
Neither profile launches an agent/provider or reads private reference solutions.

ChatGPT Plus is a supported native CLI sign-in route; a separate API key is
not required for that route. Subscription allowance and purchased credits are
different from API cash prices ([official pricing](https://learn.chatgpt.com/docs/pricing),
[authentication](https://learn.chatgpt.com/docs/auth)). No active login is copied
and no purchase is made by this driver. A Plus selection does not establish
remaining quota, a per-session credit ceiling, complete failed-request usage
or prevention of purchased-credit consumption. Those adapter/account controls
must be verified before execution; the unsupported-run guard remains active.
