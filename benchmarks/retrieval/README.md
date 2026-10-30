# Fictional retrieval benchmark

This is an initial, provider-free retrieval study, not a product superiority or
release approval claim. It makes no paid model calls. Results and raw payloads
stay in ignored private operator storage until their contents are reviewed.

The original MIT corpus contains 71 distinct records, 60 questions, two
organizations that reuse logical IDs, two project boundaries, current and retired
decisions, canonical fictional skills, shared memory and client-owned memory.
All names, engineering choices and sentinels are invented. No customer records,
third-party prose, model weights or private identities are distributed. The
local embedding model retains its own license; see [model-lock.json](model-lock.json)
and the [pinned upstream model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/README.md).
The four tenant-owned [skill files](skills/org-a/fact-retry.md) represent two logical
skills with deliberately repeated IDs. They are reviewed benchmark fixtures,
imported by the existing canonical parser/chunker/importer. They are not added to
the platform's maintained skill registry or represented as external research.

## Frozen contract

[freeze.json](freeze.json) hashes the corpus, versioned relevance labels,
family split, settings, task checks and model lock before any ranking run.
Twenty development questions and forty held-out questions use disjoint fact
families. Paraphrases, historical revisions and foreign copies stay in the same
split. Each question has one primary category and explicit org/project/client,
coupled access metadata, permitted graded support, forbidden identities and text
sentinels. Dev/held-out category counts are respectively 4/8 exact, 4/8 conceptual,
4/8 decisions, 4/8 conflicting/outdated, 2/4 no support and 2/4 forbidden-only.
Answerable denominators are 16/32; unsupported-return denominators are 4/8.
Seal revision two records a task-only ASCII/Unicode oracle correction; retrieval
corpus, labels, split, model and settings stayed byte-identical. The amendment is
recorded before the first quality measurement under that contract.

Labels were authored from the fictional facts before rankings. A held-out run
exposes that version: future tuning must use development cases and acknowledge
prior exposure. Amendments require a new version and an explained new seal;
never relabel a hard question to improve a result. No ranking or cutoff tuning
is part of this baseline.

All arms use the same currently permitted record revisions, top five and 4,000
estimated content tokens. Markdown uses body text exported through the real
current managed search envelope, Unicode casefold/word tokenization, BM25
k1=1.2/b=0.75, positive matches only and stable identity ties. It has no platform
importance, recency or source bonus. The export contains only allowed data;
foreign/private corpus rows are never supplied then filtered by the local arm.
Current credential/policy is rechecked per query; snapshot reuse is bound to the
policy fingerprint. Before measurement, the actual authorized export must equal
the frozen scope's intended active records. A mismatch blocks the run. Private
memory needs both `memory:read` and `search:read` for the selected client owner,
and both actions for its project when project-scoped. Shared organization memory
and skills likewise retain their separate source-read and search grants.

Platform lexical uses the existing service with no embedder and only the lexical
method; vector weight really renormalizes. Hybrid uses the current default
weights (.45/.25/.15/.10/.05), 90-day recency half-life, .85 overlap deduplication,
10x vector candidates capped at 1,000. At limit five each per-source query takes
five candidates and at most fifty approximate-vector candidates. There is no
new score cutoff. The fixed scoring reference is 2026-10-24 UTC; authorization
and expiry still use real time. Source timestamps are pinned outside the query.

The existing real MiniLM revision, artifact SHA256s and 384 dimensions are locked.
CPU is explicit rather than auto; Torch has two threads, one query worker, batch
32, normalized vectors, 256 texts/call, 20,000 chars/text, no downloads or remote
code. A missing or changed artifact blocks the real run. Fake vectors cannot
produce quality evidence. Model weights are an operator-supplied prerequisite,
not part of the source archive.

## Run only the selected checks

From the platform root, inspect each complete graph before executing it:

```sh
.venv/bin/python scripts/test-focused.py --phase p06-unit --plan
.venv/bin/python scripts/test-focused.py --phase p06-unit --run
.venv/bin/python scripts/test-focused.py --phase p06-export --plan
.venv/bin/python scripts/test-focused.py --phase p06-export --run
.venv/bin/python scripts/test-focused.py --phase p06-runner --plan
.venv/bin/python scripts/test-focused.py --phase p06-runner --run
```

For the actual study, explicitly select a canonical local Hugging Face cache
containing the exact locked artifacts. This input contains model files only;
never use deployment secrets, database volumes, backups or client configuration.
The runner hashes the selected artifacts in both plan and execution, and the
loader opens that exact snapshot rather than a mutable cache alias. Remove
inherited deployment/runner flags first; the runner rejects them by name.

```sh
export ACP_FOCUSED_MODEL_CACHE=/absolute/path/to/reviewed/model-cache
.venv/bin/python scripts/test-focused.py --phase p06-real --plan
.venv/bin/python scripts/test-focused.py --phase p06-real --run
```

The real profile starts only its uniquely owned authenticated MongoDB/mongot
fixture, creates fictional reviewed managed rights and recipient-issued key,
imports fixtures, requires six READY/queryable indexes, actual probes and exact
authorized-export equality, runs the study, tests project refusal and revocation, then reclaims only
its own resources. It does not commission the user's installation. The test
fixture's direct corpus rows are not an installer. No activation evidence bypass
or production verifier change is introduced. Existing release gates remain open.

Quality has exactly 180 attempts: 60 x three arms. Three first-study requests are
separate. Three round-robin timing passes add 540 attempts (723 total), concurrency
one, fixed query order, no query-vector cache. Readiness probes touch all arms,
the authorized export, model and indexes, so the first request is not a cold-cache
claim. Model load, successful per-pass distributions, all errors and hardware are recorded
separately. Timings include real credential/policy resolution and service work;
they are **not** HTTP, MCP, installed coding-client or provider response timings.
Markdown timings also include that current-policy check; they are not standalone
offline Markdown timings. `setup_ms` starts in the test body after prerequisite
fixtures; the focused runner separately records the whole job including stack
preparation and teardown. Neither is human commissioning time. Each outcome is
written to a private journal immediately; failed controls retain the query report.

## Scoring

[scorer.py](scorer.py) and [report-schema.json](report-schema.json) define the
machine-readable contract. Identity is org + source + logical ID + revision.
Multiple chunks of a record collapse at its first position before top-five
metrics. Cross-tenant copies never collapse. Raw chunk counts remain available.
Scoring respects returned order including ties; only BM25 uses identity tie
breaking. Short complete lists are not padded. Transport truncation is an
incomplete error, not an empty success.

Recall@5 is distinct relevant returns / all permitted relevant records. MRR@5 is
the reciprocal rank of the first relevant record. nDCG@5 uses graded gain
2^grade−1 and log2(rank+1), normalized by ideal DCG. Answerable queries receive
equal macro weight. No-label queries have undefined recall/MRR/nDCG and separate
unsupported retrieval: any successful nonempty result counts as unsupported.
A denial, timeout or error stays in the attempted denominator, earns zero
answerable relevance, earns no correct-abstention credit and supplies no successful
latency sample. Successful-only relevance is shown beside intention-to-run.
Missing/extra/duplicate outcome rows fail reconciliation.

Every raw payload, including tail items and failed payloads, is checked for
forbidden identities/text. Any forbidden item blocks its arm. Errors and
unsupported results are reported per category. Successful p50/p95 use nearest
rank; an empty sample has null quantiles. Context bytes and token estimates use
the current regex estimator and its hash, not billed tokens or provider savings.
No record text is cut to fit; oversized records are skipped. Identity framing is
reported separately from the service's content-token budget. Complete rendered
context, including that framing, must also fit 4,000 estimated tokens; exceeding
it is an incomplete attempt, not silent truncation.

The frozen investigation targets are held-out hybrid recall >=.80, absolute
recall lift >=.10 over Markdown, no unsupported-return increase and zero forbidden
items. Report misses. Paired recall deltas include a fixed-seed 2,000-resample
family-cluster percentile interval; this small fictional study cannot establish
general superiority. Raw private results include every repeat and category.

## Optional coding-task study — benefit not measured

[tasks.json](tasks.json) defines twelve tasks with executable behavioral checks;
[reference.py](tasks/reference.py) is the checker oracle and must never enter an
agent workspace. Unit checks verify both reference behavior and rejection of
unfinished/constant solutions. These checks establish the task harness, not a
model's ability to solve the tasks.
Eight tasks use development fact families; four use separate task-only families.
No coding task reuses a retrieval held-out family.

[coding.py](coding.py) builds a paired 108-attempt plan: twelve tasks x three
attempts x repository-only / permitted Markdown / platform retrieval. Supply an
actual pinned agent version, model snapshot, repository commit, time/input/output
limits and total monetary cap only after a provider/budget is explicitly chosen.
Each arm receives the same prompt, starter repository and budget in a fresh,
independent workspace/session; order is randomized with a recorded seed.
Repository-only receives no knowledge; the other arms receive the equivalent
permitted supporting decision. Grant only that task's project and sources.
Never supply queries, test expectations, the oracle or earlier transcripts.

`paired_plan(...)` returns exact inputs/session identities without launching a
provider. Use the selected coding agent in an isolated workspace with an outer
deadline; retain version, model, commit, prompt hash, order, budget consumption,
status and elapsed time. Stop the study when the total cap is reached. A timeout,
failed check or missing attempt is not a pass. `check_solution(module, task)`
executes the declared cases against an explicitly reviewed module; it is not a
code sandbox. Run untrusted agent code only in its disposable bounded sandbox.
`reconcile(plan, results)` retains missing/failed rows and per-arm denominators.
Do not infer coding benefit from this phase's provider-free retrieval results.

## Source/export review

Everything here is original MIT code, documentation or fictional data, covered by
[LICENSE](LICENSE). No dependency or image pin is added. Public source exports
and the source distribution explicitly list these maintained files. Unlisted
benchmark files, raw runs, model weights and candidate solutions are excluded.
The guide source registry is unchanged. Keep reports private until review for a
separate public results page; this phase does not publish one.
