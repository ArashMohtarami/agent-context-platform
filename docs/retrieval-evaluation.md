# Initial retrieval evaluation: evidence and limits

Reviewed for this public readout on **29 October 2026**. This is one fictional
corpus, not a market comparison or production release gate. Raw operator runs
remain private. Only aggregate metrics and non-sensitive source/model identities
are published here after review against the original successful report.

## Result

Hybrid and the permitted Markdown baseline both retrieve all 32 held-out supporting
records in the top five. There is **no recall advantage**. Hybrid returns unrelated
permitted material on 8/8 unanswerable cases, versus Markdown 7/8. No forbidden ID
or text is returned across723 attempts. These are retrieval results, not generated
answers; unsupported retrieval is not a measured model hallucination rate.

Coding-task benefit, provider/token savings, user adoption, vendor-client
compatibility and production reliability are **not measured** by this study.

| Split | Arm | Answerable | Recall@5 | MRR@5 | nDCG@5 | Unsupported / no answer | Errors |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| dev | markdown | 16 | 0.937500 | 0.859375 | 0.878850 | 3/4 | 0 |
| dev | lexical | 16 | 1.000000 | 0.770833 | 0.828384 | 4/4 | 0 |
| dev | hybrid | 16 | 1.000000 | 1.000000 | 1.000000 | 4/4 | 0 |
| holdout | markdown | 32 | 1.000000 | 0.924479 | 0.943517 | 7/8 | 0 |
| holdout | lexical | 32 | 0.937500 | 0.854167 | 0.874759 | 8/8 | 0 |
| holdout | hybrid | 32 | 1.000000 | 0.955729 | 0.966584 | 8/8 | 0 |

The frozen targets were hybrid recall>=0.80, >=0.10 absolute recall lift over
Markdown, no unsupported-rate increase and zero forbidden returns. Recall and
forbidden targets pass; lift and unsupported-rate targets miss. The paired
held-out recall difference is0.00, family-cluster bootstrap95% interval[0.00,0.00]
(2,000 resamples, seed20261024,16 families). This ceiling on a tiny fictional
corpus is not population equivalence or superiority.

A later development-selected final-score cutoff0.80 removed3/4 development
unsupported returns while retaining16/16 support. One retrospective replay of
the already-exposed heldout removed8/8 unsupported returns but lost support on
three answerable queries:32/32 ->29/32. It was rejected; production ranking is
unchanged. No cutoff was retuned after that replay. Future tuning needs a newly
documented independent frozen-family evaluation design.

## Corpus, split and access

The [licensed fictional corpus](../benchmarks/retrieval/corpus.json) has71 records,
60 labeled questions:20 development/40 heldout, split by fact family. There are
16/32 answerable and4/8 no-permitted-support queries. Category counts are4/8 each
exact, conceptual, decision and conflicting/outdated;2/4 each no-support and
forbidden-only. Every answerable query has singleton grade3 support. Tiny scorer
examples independently check multiple relevant IDs/grades. Labels/families were
frozen before ranking; the holdout is now exposed and cannot be called blind.
Seal revision2 corrected only a task Unicode oracle before quality measurement;
retrieval labels/corpus/settings did not change.

Matching logical IDs in two organizations, another project, current/archived
facts, reviewed fictional Git skills, shared and client-owned memories check
boundaries. The actual server-authorized export must equal all 28 intended active
records **before** measurement. Partial exports invalidate comparison. Every arm
rechecks current managed credential/policy per query; Markdown receives only the
permitted export, keyed by policy fingerprint. Four separately expected denials
(second project and three revoked-key arms) are not successful/abstaining latency
samples. Any forbidden raw payload blocks that arm, regardless of average quality.

Heldout category denominators and recall:

| Category | Questions | Markdown recall | Lexical recall | Hybrid recall |
| --- | ---: | ---: | ---: | ---: |
| exact | 8 | 1.000000 | 1.000000 | 1.000000 |
| conceptual | 8 | 1.000000 | 0.875000 | 1.000000 |
| decision | 8 | 1.000000 | 0.875000 | 1.000000 |
| conflicting | 8 | 1.000000 | 1.000000 | 1.000000 |

All four forbidden-only questions return **permitted but unsupported** material
in each arm; none leaks the forbidden record. For four no-support questions,
unsupported counts are Markdown3/4, lexical4/4, hybrid4/4. Every category has zero
request errors in the accepted study. Two earlier complete-query reports with
only26 of28 intended allowed records are invalid and excluded, not selected as
interchangeable repeats. Seven failed fixture attempts remain in private history.

## Equal retrieval budgets and scorer contract

All arms: same permitted active revisions, top5,4,000 estimated content tokens
and complete identity-framed context also<=4,000. No partial chunk is returned.
Markdown is deterministic Unicode BM25(k1=1.2,b=0.75), positive matches only,
stable ID ties on authorized body text. Platform lexical uses the current service
with no embedder, so vector weight is actually removed/renormalized. Hybrid uses
real local MiniLM vectors/indexes and unchanged weights0.45/0.25/0.15/0.10/0.05,
90-day recency half-life,0.85 deduplication and bounded candidates. Scoring time
is fixed2026-10-24UTC; authentication/expiry still use actual time.

[Scorer](../benchmarks/retrieval/scorer.py) and
[report schema](../benchmarks/retrieval/report-schema.json): deduplicate record
identities(org/source/logical ID/revision) at first rank before top5; retain
actual tie order. Short complete lists are not padded. Recall=distinct relevant
returns/all permitted relevant IDs; MRR=first relevant reciprocal rank;
nDCG uses gain2^grade-1/log2(rank+1), ideal-normalized. No-label relevance is
undefined and scored separately for unsupported nonempty retrieval. Authorization
failures are errors, never correct abstention or fast successful samples. Failed
answerable queries contribute0 in intention-to-run; failures and missing/duplicate
outcome rows cannot disappear. All raw payloads/tails are checked for forbidden
IDs/text, including failed responses. UTF-8 bytes and current regex token estimates
include complete provenance framing; these are not provider-billed tokens.

## Timing, context and resources

One quality set=180 attempts(60x3 arms); three round-robin warm passes=540 more;
three first-study requests=723 total. Repetitions are timing samples, not new
labeled questions. Concurrency1, fixed query order, no vector cache. Readiness
already probes every arm/model/index: first-study is **not cold OS/model cache**.
All successful samples are retained. Nearest-rank distributions below pool the
same60 questions per arm/pass; errors are0. No new latency target is invented.

| Arm | Stage | Success n | p50 ms | p95 ms | max ms |
| --- | --- | ---: | ---: | ---: | ---: |
| markdown | first study | 1 | 31.873 | 31.873 | 31.873 |
| markdown | quality | 60 | 36.860 | 47.136 | 56.182 |
| markdown | warm1 | 60 | 50.136 | 59.861 | 66.424 |
| markdown | warm2 | 60 | 46.575 | 66.496 | 100.320 |
| markdown | warm3 | 60 | 43.647 | 60.092 | 72.967 |
| lexical | first study | 1 | 58.300 | 58.300 | 58.300 |
| lexical | quality | 60 | 53.192 | 67.393 | 72.031 |
| lexical | warm1 | 60 | 63.893 | 80.648 | 83.985 |
| lexical | warm2 | 60 | 61.836 | 89.701 | 384.040 |
| lexical | warm3 | 60 | 57.961 | 72.643 | 89.368 |
| hybrid | first study | 1 | 78.621 | 78.621 | 78.621 |
| hybrid | quality | 60 | 81.128 | 103.555 | 107.669 |
| hybrid | warm1 | 60 | 97.210 | 123.787 | 196.921 |
| hybrid | warm2 | 60 | 94.741 | 134.474 | 200.725 |
| hybrid | warm3 | 60 | 92.303 | 115.842 | 137.155 |

Timing includes real current authentication/policy plus the domain-service result,
not HTTP/MCP/vendor apps or provider time. Markdown also pays current-policy
resolution; its timing is not standalone offline Markdown. Hardware: shared
Intel i7-8565U,8 logical CPUs,16,185,620KiB RAM; process peak RSS712,892KiB;
MongoDB/mongot2 CPUs each, owned tmpfs. Existing image/model caches, no downloads.
Model load5,602.648ms; inner setup45,629.070ms begins after prerequisite fixtures;
whole focused job155.244s includes stack/test/teardown. None is human commissioning
time or a cold-download measurement. Variability is visible; do not select the
best wave or compare these service timings with a competitor's managed benchmark.

Quality-set complete context totals (not maxima or billed tokens):

| Split | Arm | Items | Body bytes / estimated tokens | Framed bytes / estimated tokens |
| --- | --- | ---: | --- | --- |
| dev | markdown | 84 | 13493 / 2500 | 16765 / 3592 |
| dev | lexical | 86 | 14046 / 2580 | 17202 / 3698 |
| dev | hybrid | 100 | 16315 / 3048 | 19999 / 4348 |
| holdout | markdown | 177 | 28750 / 5431 | 35708 / 7732 |
| holdout | lexical | 179 | 29568 / 5487 | 36172 / 7814 |
| holdout | hybrid | 200 | 32940 / 6153 | 40277 / 8753 |

## Reproduce and inspect source identities

Use the [benchmark instructions](../benchmarks/retrieval/README.md), exact
[freeze](../benchmarks/retrieval/freeze.json), [settings](../benchmarks/retrieval/settings.json)
and [model lock](../benchmarks/retrieval/model-lock.json). The corpus/scorer are
original MIT fixtures; weights are not distributed and retain their own license.
Real quality requires actual local model/indexes, never deterministic fake vectors.

```sh
# Explicitly choose an already reviewed model-only cache containing locked artifacts.
export ACP_FOCUSED_MODEL_CACHE=/absolute/path/to/reviewed/model-cache
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p06-real --plan
env -u DOCKER_HOST .venv/bin/python scripts/test-focused.py --phase p06-real --run
```

Inspect the complete graph first. This selects only the one real study and its
owned disposable MongoDB/mongot fixture, not production activation or the full
suite. No paid provider call; no deployment env/live volume/client configuration
input. Missing required infrastructure is incomplete. A changed input needs new
evidence, not relabeling an old report.

Study source is preserved in commit `e73be0ce412445c5c14bf1e2ee1ed83d2c8524de`;
the measurement used its then-uncommitted benchmark implementation over the same
runtime. Exact SHA256 identities, rather than the commit timestamp, bind it:

| Input | SHA256 / immutable identity |
| --- | --- |
| benchmarks/retrieval/freeze.json | `4d6855f2f663a16721f485b8eb7ef24558abf9406ef3363ff79e139c696a73f8` |
| benchmarks/retrieval/corpus.json | `6c6ab1d7563e8da19b0a7e748dc84e04ad039ace7b168ca4a93d5d662041c58d` |
| benchmarks/retrieval/queries.json | `af7b3adf1d0e538771ce70048eb8961a5fd7359378bde97832f71c34498c66c6` |
| benchmarks/retrieval/split.json | `d911b42afd7ddbbe2ca7e4df953f35f272e497fd4e1816d7e638957a6dccc173` |
| benchmarks/retrieval/settings.json | `7b16ff1863466c0fb01a74e5ba1c94850c926db32877bde32b51f4269d4e23ea` |
| benchmarks/retrieval/scorer.py | `a27bc2e7a05fd00206e1085a60ba969157a4477c5eef4a5312397d7bc5f74933` |
| benchmarks/retrieval/live.py | `311caea8e36a11b8f6b7b169bdb63a174ad68d85a0b407c8aa90847b779d2c46` |
| benchmarks/retrieval/model-lock.json | `dedccf21c7e46d6c2b88018135a55940de2d5e624e40d75b8cae3617d2e7a3db` |
| tests/integration/test_retrieval_evaluation.py | `172c73e02787f0f97b2a8308f4fcb2c2e8961aa05ca6aa5a5262ece536bc3792` |
| Runtime app fingerprint | `4d220377dec7a935f775c3a927280f46a832dc6bf69c32e503022cfc7c0b20c6` |
| Test-suite fingerprint at measurement | `4d37e270743165efa5dc015a9928a30f2e2344499c02b908db7947c05e1f55fe` |
| Accepted raw private report | `a5d8cab42381fb702fa6612b9292062dde02e0eb47fc5edc5469cf13ed148e83` |
| Token estimator | `1d8b4b71425658408e1dd0e53302e77176e58251d0bbcb1991ef9b57ae1ca9b0` |

Model `sentence-transformers/all-MiniLM-L6-v2`, revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`:11 locked artifact SHA256s,
91,607,178 bytes, CPU 2 Torch threads, normalized 384D, max 256 word pieces,
no downloads/remote code. Python 3.12.3, PyMongo 4.14.1, Torch 2.14.0+cpu,
sentence-transformers 6.0.1; image digests are pinned in the maintained disposable
fixture. Access filters, query families and complete budgets are versioned inputs.

Raw runs contain private operator paths/configuration and are not a public
artifact; this readout deliberately omits credentials, grants, runtime identities,
absolute paths, logs and record payloads. Hashing a private report is provenance,
not independent access to its contents. Reproduce the maintained harness for
query-level inspection; no claim of publicly downloadable raw evidence is made.

The optional12-task/108-attempt paired coding harness is implemented and tested,
but no agent/provider attempts were authorized or run. See its exact common
prompt/repo/model/budget/fresh-session contract in the benchmark guide. Task
benefit, maintenance effort and repeat use remain unmeasured. A fictional relevance
ceiling is not evidence that a team should adopt a service instead of Markdown.
[Production release reconciliation](verification.md) remains open.
