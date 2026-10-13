---
id: retrieval-evaluation
name: Hybrid Retrieval Evaluation
version: 1.0.0
description: Evaluate and tune lexical/vector/hybrid retrieval using representative queries, authorized candidate
  sets, deduplication, context budgets and failure-mode evidence. Use for search quality and ranking changes.
category: database
trust_level: trusted
tags:
- search
- retrieval
- lexical
- vector
- evaluation
---

# Hybrid Retrieval Evaluation

## Separate stages and evidence

Trace query normalization, authorization filters, lexical candidates, vector candidates, merge,
ranking, version selection, deduplication and context budgeting. A relevant result is not necessarily
true, current or authorized. Preserve source IDs, versions, scores and provenance. Do not repair
ranking by weakening project/client visibility filters.

Create a small maintained query set from real tasks. Include exact identifiers, conceptual queries,
rare terms, framework names, ambiguous words, no-answer queries and wrong-project controls.
Label expected useful sources/sections, not only one exact phrasing. Keep training/tuning examples
separate from a held-out set when making repeated tuning decisions.

## Measure useful retrieval

Inspect recall at the candidate stage, useful results near the top, duplicate rate, stale-version
rate, token utilization, latency and empty/error behavior. Compare lexical-only, vector-only and
hybrid against the same inputs. A deterministic fake embedding proves plumbing, not meaning quality.
Use the real configured local model and representative data for semantic-quality conclusions.

Keep model ID/dimensions consistent across vectors and indexes. A configuration number does not
convert existing embeddings. Re-embedding and search-index rebuilding are separate operations.
Evaluate chunk boundaries: independent sections need enough framework/task context to be useful;
large code fences should remain intact rather than becoming invalid fragments.

## Failure and rollout checks

Test unavailable embeddings, unavailable search coordinator, index lag, bounded limits and budget
truncation. Verify all unauthorized candidates are excluded before they can become snippets.
In this platform, `search_skills` uses unified hybrid retrieval restricted to `active_skill`;
`POST /api/v1/skills/search` is a separate unscored literal fallback. Do not compare their scores
as though they were the same algorithm.

Record before/after query results and tradeoffs. Change one supported ranking/filter parameter at
a time and retain an easy rollback. The offline Git catalog's metadata weights are local discovery,
not a substitute for live semantic evaluation.
