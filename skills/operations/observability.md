---
id: service-observability
name: Service Observability and Capacity
version: 1.0.0
description: Instrument service behavior with bounded safe logs, useful metrics, correlation and capacity evidence.
  Use for operability, diagnosis and performance work.
category: operations
trust_level: trusted
tags:
- observability
- metrics
- logging
- tracing
- capacity
---

# Service Observability and Capacity

## Measure the user outcome

Name the operation and service target before adding telemetry. Measure request outcome, latency,
rate, saturation and dependency cost. For asynchronous work, include queue age and completion, not
only acceptance. Distinguish database persistence from index visibility and external delivery.
Health checks should describe actual capabilities: liveness is process health, readiness covers
required dependencies, and an optional component may have a documented degraded mode.

## Keep telemetry safe and bounded

Use stable route/operation labels rather than raw URLs, request IDs, user IDs or arbitrary query
text as metric labels. Keep correlation IDs in structured logs/traces where appropriate. Redact
credentials and avoid logging full prompts, private source bodies or result content by default.
A logging failure must not turn a completed business operation into an accidental duplicate retry.

Record enough timing to separate application, database/search, model, external network and queue
cost. Use a monotonic clock for elapsed durations. Inspect tail latency and errors under load;
report sample size, environment and workload. Compare before/after using the same conditions.

## Capacity and actionable signals

Budget workers, concurrent requests, connection pools, model memory and queue consumers together.
A per-process limiter does not provide a cluster-wide budget. Test overload/backpressure and
shutdown, not only peak successful throughput. Alerts should identify an actionable symptom with
context and an owner; avoid a page for every harmless transient dependency error.

Verify metric label growth, secret redaction and correlation for an allowed and denied operation.
Use a representative smoke workflow after deployment/recovery. Report degraded capabilities and
the practical limits of a single-instance benchmark.
