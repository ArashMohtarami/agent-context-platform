---
id: architecture-decisions
name: Evidence-Based Architecture Decisions
version: 1.0.0
description: Evaluate and record architecture decisions with constraints, invariants, alternatives, evidence and
  rollout/reversal criteria. Use for meaningful design choices rather than routine implementation detail.
category: architecture
trust_level: trusted
tags:
- architecture
- adr
- tradeoffs
- boundaries
---

# Evidence-Based Architecture Decisions

## Frame the decision

Name the problem, who experiences it, current workload, operational constraints and what must remain
true. Distinguish measured facts from estimates. Read the existing design and decision history before
introducing a new abstraction, database or service. Preserve the chosen product unless a change is
part of the request. A diagram should expose ownership and failure paths, not decorate a proposal.

Compare a small set of viable alternatives against the actual constraints: consistency, isolation,
latency, throughput, recovery, deployability, team skills and ongoing cost. Include the option of a
smaller change to the existing system. Quantify thresholds only where there is evidence, and mark
assumptions that require a benchmark or prototype.

## Evaluate failure and evolution

Trace the write/read path through partial failure, duplicate requests, concurrent writers and
restoration. Specify who owns the source of truth, which derived state is rebuildable, and which
side effects need reconciliation. A queue/cache/search index adds lifecycle and recovery work; it
does not remove the original invariant.

Choose component boundaries around data ownership and release needs. Avoid distributing a single
atomic invariant without a clear compensation/idempotency design. Define version compatibility,
rollout order, observability, fallback and a point at which reversal becomes costly.

## Write a durable decision

Use the repository's ADR format. Record context, decision, rejected alternatives with reasons,
consequences, evidence, owner and revisit trigger. Link canonical diagrams/schemas and tests;
keep transient discussion out of the final decision. Store a short verified project-context summary
with provenance after review, rather than replacing the Git ADR with a memory note.

Example revisit trigger: "Move the job queue to a dedicated worker service if oldest-job age exceeds
the service target at the measured peak workload, after ruling out database query cost." This is
more useful than an unsupported claim that a design is scalable. Deliver remaining assumptions and
the smallest experiment needed to resolve them.
