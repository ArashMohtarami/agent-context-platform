# Fictional agent-value fixtures — initial inputs only

Original MIT Python 3.12 projects for a limited local pilot. No agent/provider
attempt has run; grader/reference checks are not coding-agent outcomes, real MCP
commissioning, compatibility certification or production approval.

There are 8 development and 24 heldout decision families, three proposed attempts
per arm. The four fictional project clusters are Larch Parcel, Cobalt Studio,
Reed Harbor and Juniper Folio. Each has six heldout categories: bug repair,
feature/API, updated decision, cross-file boundary, repository-sufficient control
and insufficient knowledge. Development allocation is 2/2/1/1/1/1. Twenty
heldout coding tasks and four heldout safe actions have separate denominators;
the independent coding-family count is 20, not 60 repeats. This small study has
no assumed power guarantee. No sample or design change uses heldout outcomes.

`tasks.json` is the exact task/input/check-budget contract, `split.json` fixes
families/projects/seeds, and `knowledge/index.json` binds permitted document
bytes/revisions. Ordinary fictional handbooks were authored before prompts.
Retired/current revisions stay in the same family and split. Development
snapshots do not include heldout decision records. Common project overviews
contain no task decision. Repository-sufficient requirements are in the ordinary
fixture README; the extra records are irrelevant operational context for those
tasks. These are new multi-file service slices, not the exposed V7 microtasks.

`dataset.materialize` copies an exact per-task file allowlist into a new
canonical workspace. It never copies a directory, checkout, Git history,
controller, sibling task, hidden grader or reference. All arms receive normal
code/docs and two useful named visible checks. M adds the complete permitted
project/split snapshot in browsable Markdown. C has the identical record IDs,
revisions, status and content hashes specified by `dataset.snapshot`, to be
loaded through real MCP in a later phase. No C-specific supporting excerpt is
selected. The equality is a data contract here; no MCP delivery has been tested.

Each service slice has `api.py` (interface/transaction), `domain.py` (behavior),
`repository.py` (snapshot persistence), protected README/visible checks, and its
own prompt. Graders inspect actual interface output and final state across
multi-command cases, including invalid requests, retries, ordering, stale rules,
partial writes and regression behavior. They do not compare patch text or force
a helper/algorithm. An independently structured transaction adapter is accepted
as an alternative implementation. References and two wrong variants per task
remain operator-private, alongside hidden expected results. No hidden material
belongs in an agent's input tree or public export inventory.

The three editable implementation files are a deliberately small output
allowlist. Protected-check mutation, extra/missing files, symlinks, hardlinks,
missing/duplicate checker cases, abnormal exit, malformed/missing child reports,
timeouts and quota failures cannot pass. Reference and hidden checks are outside
the candidate process; only request inputs enter its pipe. Its filesystem has
no checkout, home, Git history, sibling workspace, expected labels or references.

The model-free grader requires Linux, installed `/usr/bin/bwrap` (validated with
0.9.0), system Python 3.12 and `libseccomp.so.2`. It creates fresh filesystem,
PID/IPC/user/UTS/cgroup namespaces and a read-only candidate snapshot. Because
this environment denies network-namespace loopback setup, it explicitly retains
the host network namespace **with all socket/network syscalls denied by seccomp**;
there is no unsandboxed fallback. Process creation, namespace/mount operations,
ptrace/process-memory access, signaling and other escape surfaces are denied.
CPU 3s, memory 256MiB, output 1MiB, file descriptors 64, core dumps 0 and wall 5s
bound each grader child. A whole focused job is separately limited. No candidate
process inherits provider/application credentials or calls a test runner.
This is a narrow fixture grader, not the later agent execution/key sandbox.

Safe-action responses go in `response.json` using `schemas/safe-response.json`.
Automated checks establish unchanged project files and a structured clarification,
not adequacy of language. Correct references stay `pending_human_review` with
success=null until two independent blinded reviews and any adjudication. No
model judge or fabricated reviewer is substituted. Unchanged files do not prove
a future agent made no external tool call; that needs its later event ledger.

`inventory.json` lists every maintained public input explicitly. `seal.py`
computes a deterministic SHA256 commitment over that inventory and a separate
complete private checker/reference inventory. The initial seal stays private;
its public commitment view contains hashes, never hidden contents, and may be
reviewed for later publication. A private seal location and
private bundle are explicit operator inputs. From the checkout root:

```sh
.venv/bin/python -m benchmarks.agent-value.seal create --private-root PRIVATE_BUNDLE --private-seal NEW_PRIVATE_SEAL
.venv/bin/python -m benchmarks.agent-value.seal verify --private-root PRIVATE_BUNDLE --private-seal EXISTING_PRIVATE_SEAL
```

These commands do not run code candidates, agents, providers or services and
refuse to replace a different existing seal. Initial author/source/unit review
is separate from independent task/design/license approval, which remains pending
and must precede calibration. The commitment says execution_authorized=false.
No model/effort/budget, paid runner, real MCP setup, skill change, statistical
analysis or presentation is introduced in this phase.

The focused profiles are `v8-p01-fixtures`, `v8-p01-runner` and `v8-p01-export`;
`v8-p01-graders` selects only the 33 sandbox/grader cases after a sandbox fix.
`v8-p01-mutants` selects six feature-task cases when reviewing near-correct
mutations that retain happy paths but fail a declared edge condition.
inspect their exact saved plan before `--run`. No activation, service or provider
dependency is implicit. Preserve the existing V7 manifest and identity algorithm;
ordinary source-byte changes invalidate old evidence in the usual way. Source
and sdist inventories add individual licensed inputs, never this whole directory.
Raw runs, private bundles and private seals remain excluded. Do not publish
heldout inputs before completing/reviewing the study; later reproduction release
acknowledges their exposure. All original code/data here is covered by LICENSE.

Sandbox mechanics follow the upstream [bubblewrap manual](https://github.com/containers/bubblewrap/blob/main/bwrap.xml)
and [libseccomp API](https://github.com/seccomp/libseccomp/blob/main/include/seccomp.h.in).
No upstream implementation was copied or package installed.
