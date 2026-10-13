---
id: systematic-debugging
name: Systematic Debugging and Incident Diagnosis
version: 1.0.0
description: Diagnose failures through a minimal reproduction, bounded hypotheses, boundary evidence and verified
  repairs. Use for incorrect behavior, intermittent failures and performance regressions.
category: engineering
trust_level: trusted
tags:
- debugging
- reproduction
- observability
- root-cause
---

# Systematic Debugging and Incident Diagnosis

## Stabilize the question

Capture the failing request/input, expected outcome, actual outcome, environment/version and first
known occurrence. Separate a deterministic defect from a dependency outage, configuration mismatch
or a race. Preserve useful evidence before changing the system. During an incident, use the
operator's authorized mitigation scope and avoid destructive "reset everything" shortcuts.

Create the smallest reproduction that retains the failure. Trace identifiers and timing through
boundaries; compare a failing case with a successful control. Inspect logs/metrics without exposing
credentials or private payloads. Form a hypothesis with a predicted observation and a bounded
experiment, changing one relevant variable at a time.

## Diagnose common hidden causes

For intermittent writes, inspect concurrent updates, duplicate deliveries, timeout ambiguity,
replica/transaction requirements and cache/index lag. For slow requests, decompose database, model,
network, queue and serialization time; distinguish median from tail latency. For async stalls,
inspect blocking calls, middleware adaptation and pool/concurrency saturation.

A temporary workaround is not root-cause evidence. Do not widen permissions, suppress validation,
or disable safety checks to make an exception disappear. If a library/version is involved, verify
its actual installed contract and a primary source before relying on memory.

## Repair and verify

Prefer the smallest change that restores the invariant. Add a regression test when the defect is
meaningful and testable. Reproduce the original failure and check the nearest boundary cases.
Explain the cause, resulting behavior, evidence and operational limitations; remove temporary
instrumentation or keep it deliberately with bounded safe fields. Document a follow-up only when
it resolves a concrete remaining issue.
