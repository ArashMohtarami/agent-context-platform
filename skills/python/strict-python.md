---
id: strict-python-engineering
name: Strict Python Engineering
version: 1.1.0
description: Typed, testable Python practices for production services.
category: python
trust_level: trusted
tags:
  - python
  - typing
  - testing
---

# Strict Python Engineering

## Purpose

Build Python services whose contracts are visible to readers and checked by tooling. Prefer small
modules with explicit inputs and outputs. Keep I/O at boundaries and keep core transformations
deterministic so they remain easy to test. Treat type annotations as executable design constraints,
not decoration added after implementation.

## Type boundaries

Annotate public functions, class attributes, and meaningful local collections. Use domain-specific
types instead of passing unstructured dictionaries through multiple layers. Validate data when it
crosses a filesystem, network, API, or database boundary. Once data is validated, preserve its typed
form instead of repeatedly coercing it.

Avoid `Any` unless an untyped dependency forces it at one narrow adapter. Prefer protocols for
dependencies consumed by services and concrete implementations at composition roots. Do not weaken
validation merely to silence a test or type checker.

```python
from typing import Protocol

class Clock(Protocol):
    def now(self) -> float: ...

def elapsed(clock: Clock, started_at: float) -> float:
    return max(0.0, clock.now() - started_at)
```

## Async behavior

Use clients whose calling contract matches the service. Do not call a blocking client directly
from an async request handler. Keep CPU-heavy work outside the event loop, and make ownership of
clients and task lifecycles explicit. Application startup should validate required dependencies;
shutdown should close resources even when request handling failed.

## Verification

Tests should cover meaningful behavior and failure paths. Run the relevant behavior tests and the project's applicable lint/type checks before handing off a change. Prefer assertions about observable behavior over tests that
mirror implementation details. Integration tests should use real infrastructure where driver or
database semantics matter.

## Advanced boundary decisions

Use immutable dataclasses/value objects for validated domain values and protocols for behavior a
service actually consumes. `TypedDict` describes a dictionary's static shape; it does not validate
runtime JSON. Narrow untyped dependencies at an adapter and keep unknown values out of core logic.
Preserve omitted/null semantics and exception causes when mapping safe domain failures.

Choose sync or async from the existing call path and client contract. Prefer a simple synchronous
core for deterministic work, with I/O owned by adapters. Avoid converting every function to async
or creating a generic framework for one operation. Define state transitions, operation identity
and resource ownership before optimizing concurrency.

## Delivering a meaningful change

A service change needs evidence for the invariant it can break: denied ownership, duplicate request,
conflicting update, rollback or dependency failure as relevant. Type/lint checks complement behavior
tests. Check actual pinned APIs before copying a newer example. State what changed, what verified
it, and any real deployment/schema limitation. For async cancellation and bounded task lifetimes,
retrieve `python-async-concurrency`; for test design, retrieve `python-testing`.
