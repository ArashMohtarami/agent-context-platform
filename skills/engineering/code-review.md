---
id: code-review
name: Risk-Focused Code Review
version: 1.0.0
description: Review a change for concrete correctness, authorization, concurrency, migration and operational regressions
  using evidence from code and tests. Use for requested reviews and high-risk implementation checks.
category: engineering
trust_level: trusted
tags:
- review
- correctness
- security
- regression
---

# Risk-Focused Code Review

## Trace the changed behavior

Read the diff, its callers, affected models/configuration and relevant tests. Identify a concrete
trigger and before/after outcome. Prioritize incorrect results, lost data, cross-owner disclosure,
unbounded work and incompatible rollouts over taste. Do not demand a framework rewrite when a
small change can satisfy the contract.

Follow data across validation, authentication, resource grants, service logic, persistence and
serialization. Inspect list/search/export paths as carefully as detail updates. For concurrency,
check conditional writes, uniqueness, retry identity, transaction scope and side-effect ordering.
For migrations, consider old/new application versions and the real database's lock behavior.

## Support each finding

A useful finding includes location, trigger, incorrect outcome and why existing behavior/checks do
not prevent it. Reproduce or construct a minimal counterexample when practical. Distinguish proven
bugs from questions and optional improvements. Do not claim a vulnerability from a suspicious
function name alone; show how an unauthorized caller reaches the sensitive operation.

Verify tests cover the observable contract rather than the implementation's happy path. A fake
store does not prove driver semantics, and an import check does not prove the installed agent
connected. State the limits of the evidence used.

## Deliver a decision

Order findings by user impact, keep duplicates together, and identify a minimal repair direction.
When fixing a review finding, test the concrete trigger and nearby affected behavior. Report any
remaining uncertainty and unrun check. If no actionable finding remains, say so without implying
that all possible runtime behavior has been proven correct.
