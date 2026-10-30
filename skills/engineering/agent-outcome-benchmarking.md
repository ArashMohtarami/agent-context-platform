---
id: agent-outcome-benchmarking
name: Agent Outcome Benchmarking
version: 1.0.0
description: Compare coding agents, MCP knowledge delivery and model configurations through behavioral
  task outcomes, matched controls, actual token/cost accounting and uncertainty. Use for agent
  benchmark design, execution and evidence review; retrieval relevance alone needs a retrieval evaluation.
category: engineering
trust_level: trusted
tags:
- agents
- benchmarking
- evaluation
- mcp
- tokens
- cost
---

# Agent Outcome Benchmarking

## Match the question to the controls

Compare repository-only access, the repository with permitted knowledge in ordinary Markdown,
and the same knowledge through MCP. MCP versus repository-only measures information plus delivery;
MCP versus equivalent Markdown tests delivery. Keep normal documentation, file search and useful
instructions in the baseline. Do not force Markdown to paste every document or give MCP the gold
supporting excerpt. Include repository-sufficient tasks where retrieval may only add overhead.

Pin the real installed agent version, requested/resolved model, reasoning effort and mode, prompts,
code revision, ordinary tools, skills, permissions, context policy and stopping budgets. An equal
effort setting does not imply equal computation. Disable unplanned routing/fallback or disclose
the confound. Common skills must be identical across arms; extra MCP instructions are another
intervention requiring a named configuration or separate ablation.

## Grade effects and protect the split

Use meaningful multi-file tasks and immutable behavioral checks. Verify reference solutions and
plausibly wrong solutions, and accept valid alternative implementations. Agent exit status or a
claim that tests passed is insufficient: run the original checks in a separate bounded grader.
Distinguish coding completion from correct clarification on insufficient information. An access
denial is neither useful abstention nor a completed coding task.

Split by independent decision/fact family, including revisions and paraphrases. Freeze tasks,
knowledge, checks, settings and analysis before heldout execution. Tune on development only.
Agents must not reach hidden checks, reference patches, sibling attempts or answer-bearing Git
history through parent directories, mounts or network access. A published or previously inspected
holdout is exposed; a new filename does not make it independent.

## Measure complete usage and cost

Use documented numeric provider receipts, not context-size estimates. Preserve input, cached
input, cache writes, output and reasoning counters with their inclusion semantics. Reasoning may
already be included in output and cached tokens in input; cumulative counters cannot be summed as
fresh turns. Include discovery/tool schemas, skill instructions, compaction, retries and billed
failed calls. Unknown usage stays unknown rather than zero. Do not retain reasoning text or secrets.

Report total measured tokens/spend across successful and failed attempts, then divide by actual
completions for tokens/cost per completion. With no completions the ratio is undefined. State
usage coverage, pricing date/currency/cache rules and whether cost is billed or estimated.
Subscription usage does not establish cash savings. Cross-provider tokenizers differ.

Call repeated context reads, failed-attempt tokens or reviewed unnecessary work explicit proxies;
do not label all reasoning or exploration "waste." Token reduction supports an efficiency claim
only alongside adequate matched quality. Show both all-attempt and matched-success comparisons,
since reporting only the successful runs can hide expensive failures.

## Keep paired attempts and uncertainty

Start fresh workspaces, sessions and client configuration for every attempt; prevent cross-run
memory or writes from changing later knowledge. Randomize/balance arm order and record host/cache
conditions. Keep every declared repeat, timeout, missing outcome and infrastructure failure.
Report scheduled, launched, gradable and successful denominators with separate failure reasons.
Three attempts on one task are not three independent tasks, and best-of-three is not first-try success.

Predeclare the primary comparison and meaningful effect. Report paired deltas, category wins and
losses, and uncertainty clustered by independent task/family; retain repetitions together.
Flag small/degenerate samples and project-level dependence. A lower-cost-model comparison needs
within-model controls and a justified frozen noninferiority margin. Failure to detect a difference
is not equivalence or proof that one model became generally stronger.

## Bound real execution and state the claim

Use actual installed-client MCP sessions and real permitted storage/search for transport and
retrieval claims. Fake vectors or stub executables prove plumbing only. Do not inject a retrieved
answer into a prompt and label it an MCP run. For handoffs use distinct credentials and a fresh
second session, retain record identity/revision, and count authoring/review effort and both sessions.

Paid runs require the user's selected provider/model and explicit budget. Reserve bounded spend
before launch; journal uncertain calls and reconcile receipts before retrying. Isolate credentials,
grader and control state from agent-controlled code. Stop/clean up only owned resources. Keep
ordinary policy, provenance, revision, private-memory and revocation boundaries intact.

Report successful latency distributions with sample counts and failures separately; keep cold
setup/indexing, warm task time and observed human effort distinct. Publish reviewed numeric data,
source/config hashes, missing measurements and limits. Tables and animations must show the same
final evidence, with static/reduced-motion access. Negative or inconclusive results are valid;
a harness, protocol smoke or retrieval score does not establish agent benefit or release approval.
