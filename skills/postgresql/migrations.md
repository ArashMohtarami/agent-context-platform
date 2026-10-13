---
id: postgresql-migrations
name: PostgreSQL Schema Evolution
version: 1.0.0
description: Plan PostgreSQL schema/index changes around lock behavior, rolling compatibility, bounded backfills
  and recovery. Use for migrations and persistence changes that can affect live availability.
category: database
trust_level: trusted
tags:
- postgresql
- migrations
- locks
- backfill
- indexes
---

# PostgreSQL Schema Evolution

## Inspect the live-change assumptions

Read the PostgreSQL version, migration tooling, table size, traffic, replicas and application rollout
model. Identify the lock and rewrite behavior of each proposed DDL statement using the version's
documentation. Set deliberate lock/statement timeout policies and avoid waiting indefinitely behind
an unrelated long transaction. [ALTER TABLE reference](https://www.postgresql.org/docs/current/sql-altertable.html).

A small development table cannot establish production migration duration. Explain the estimate and
validate with representative data when the operation could block critical traffic. Review every
generated migration; generation does not account for the application's availability requirements.

## Expand, migrate, contract

Add compatible structures first, deploy readers/writers that tolerate both versions, backfill in
bounded resumable batches, verify completion, enforce the new invariant, then remove the old
structure in a later compatible rollout. Dual writes need reconciliation and a defined authority;
they are not automatically consistent.

Choose index/constraint creation strategy for the actual version and workload. Concurrent index
creation has transaction and failure-recovery constraints; inspect invalid indexes after a failure.
Where supported, separate introducing an unvalidated constraint from validating existing rows.
Do not claim an operation is lock-free simply because it avoids a full table rewrite.

## Recovery and evidence

State whether reversal restores schema only or also lost/transformed data. Keep a verified backup
and recovery plan for destructive transformations. Repeating a migration should not duplicate a
backfill or silently skip incompatible state. Use stable batch boundaries and checkpoint progress
after commits.

Test upgrades from realistic old schema/data, application compatibility on both sides, constraint
failures, interrupted backfills and the recovery approach. Observe lock waits and replication lag
during rollout. Deliver the commands, order, verification evidence and remaining timing assumptions.
