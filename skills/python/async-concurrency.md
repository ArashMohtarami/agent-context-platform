---
id: python-async-concurrency
name: Python Structured Async Concurrency
version: 1.0.0
description: Design Python async I/O with owned tasks and clients, bounded concurrency, deadlines, cancellation
  cleanup, and blocking-work boundaries. Use for concurrency correctness and event-loop performance.
category: python
trust_level: trusted
tags:
- python
- async
- cancellation
- timeouts
- backpressure
---

# Python Structured Async Concurrency

## Own work and resource lifetimes

Read the supported Python version and client APIs. Async is a calling contract, not evidence that
an operation is nonblocking. Keep synchronous HTTP/file/driver calls outside the event loop, using
a suitable async client or an explicit adapter. Thread-safe blocking I/O may use `to_thread`;
Django ORM needs its thread-sensitive adapter. CPU-heavy work needs an appropriate process/job
boundary when it would monopolize the loop.

Use a shared client owned by the application lifespan. Close it after the tasks that depend on it.
Do not create a fresh connection pool for every operation. Decide whether child failure cancels
siblings or produces partial results. On Python 3.11+, `TaskGroup` gives structured sibling lifetime
and failure propagation; do not silently convert unexpected failures into success.

```python
import asyncio
from collections.abc import Awaitable, Callable

async def collect(fetch: Callable[[str], Awaitable[str]], names: list[str]) -> list[str]:
    semaphore = asyncio.Semaphore(4)
    async def one(name: str) -> str:
        async with semaphore:
            return await fetch(name)
    async with asyncio.timeout(5):
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(one(name)) for name in names]
    return [task.result() for task in tasks]
```

This illustrative function bounds in-flight calls for a bounded input list; a million inputs would
still allocate a million tasks. For unbounded work, use a bounded queue and fixed workers.
[Task groups, deadlines and cancellation](https://docs.python.org/3.12/library/asyncio-task.html).

## Deadlines and ambiguous writes

Set an overall deadline and dependency timeouts; per-retry timeouts alone allow unbounded latency.
Cancellation is a control signal: clean up in `finally` and propagate it. Cancelling an await does
not prove a remote operation or worker thread stopped. Reconcile a possibly committed side effect
before retrying, and use stable operation keys where the provider supports them.

Backpressure must bound memory, open connections and downstream concurrency, not only average
requests per second. Retries need a finite budget, jitter and eligibility rules. Avoid holding a
lock across unrelated network waits. Protect shared mutable state or make tasks return independent
results and combine them at one owner.

## Prove the failure behavior

Test sibling failure, cancellation, deadline exhaustion and cleanup with deterministic barriers,
not arbitrary sleeps. Observe maximum in-flight work and output ordering. Check shutdown with
in-flight requests. Report partial-result semantics and retry limits alongside performance results.
