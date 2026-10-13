---
id: data-pipeline-reliability
name: Reliable Data Pipelines
version: 1.0.0
description: Build and review repeatable ingestion and transformation pipelines with explicit schema, deduplication,
  checkpoints, late-data policy and recovery. Use for batch imports, ETL and analytics jobs.
category: engineering
trust_level: trusted
tags:
- data
- etl
- idempotency
- checkpoints
- schema
---

# Reliable Data Pipelines

## Define identity and time

Name source-of-truth inputs, immutable event/row identity, partition key, business time, ingestion
time and timezone. Specify schema versions and accepted/rejected records. A row number or processing
timestamp is rarely a safe deduplication identity. Store rejection counts/reasons without silently
dropping invalid data or logging sensitive rows.

Choose the unit of retry: file, partition, batch or event. A rerun of unchanged input should produce
the same business state. Define whether changed source data is a correction, a new version or an
error. Distinguish a duplicate operation from a legitimately repeated business event.

## Commit and checkpoint safely

Commit data before advancing the durable checkpoint, or coordinate both in one transactional
boundary. If that is impossible, design repeatable processing for the crash gap. Do not acknowledge
source progress before the destination is recoverable. Bound batch size, memory and downstream
concurrency; a generator does not guarantee bounded buffering at every stage.

Specify late/out-of-order data, replay windows, deletes and backfills. Use expand/backfill/contract
for schema changes consumed by multiple jobs. Keep intermediate artifacts traceable to input
version and transformation version, with checksums/counts where useful.

## Verification and operations

Test unchanged reruns, duplicated inputs, reordered inputs, partial failures before/after commit,
late corrections and invalid schemas. Compare record counts, reconciliation totals and selected
known results after recovery. Monitor freshness, lag, rejection rate, throughput and stuck partitions.
A green process exit does not prove every business record arrived.

Document how an operator pauses, replays and verifies a bounded partition. Use synthetic data for
tutorials and keep sample conventions in the correct project's context. Do not turn an invented
UTC partition rule into a claim about the user's production pipeline.
