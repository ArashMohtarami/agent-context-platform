---
id: django-testing
name: Django Behavioral and Async Testing
version: 1.0.0
description: Test Django and async Django behavior with appropriate test classes, AsyncClient, real permission/CSRF
  boundaries, query budgets, commit hooks, and production-engine integration checks.
category: django
trust_level: trusted
tags:
- django
- testing
- async
- csrf
- transactions
---

# Django Behavioral and Async Testing

## Select the layer that proves the behavior

Use pure unit tests for deterministic domain calculations, request tests for routing/validation and
authorization, and database integration tests for constraints, transaction boundaries and query
cost. Keep a small set of real workflow tests for critical paths. Identify the actual regression or
invariant first; assertions should still matter if the internal function names change.

Django `TestCase` wraps tests in transactions; use `TransactionTestCase` when commits or transactional
behavior itself must be observed. `captureOnCommitCallbacks(execute=True)` can exercise registered
callbacks in a `TestCase`. Use `AsyncClient`/async test methods for async views, and `Client` with
`enforce_csrf_checks=True` when proving cookie-authenticated write protections.
[Testing tools](https://docs.djangoproject.com/en/5.2/topics/testing/tools/).

## Fixtures and boundaries

Create at least two owners and records for both. Test anonymous access, each permitted action, and
attempts to list/read/change/delete the other owner's record. Keep expected public error fields
stable without matching sensitive implementation exceptions. Test nonexistent and unauthorized IDs
against the chosen disclosure policy. Cover omitted, null, invalid and oversized values where those
cases alter behavior.

Use factories/fixtures that describe domain states; avoid a giant shared dataset that accidentally
satisfies every relation. Freeze time through a boundary consumed by the operation. Mock an external
network adapter when testing domain behavior; use a local fake server when HTTP serialization,
timeout or response parsing is the contract under test.

## Async and database behavior

Await async client/ORM methods. Put synchronous setup outside async contexts or use an explicit
thread-sensitive bridge. Test error cleanup and cancellation for a long-lived stream or dependency
call. Ensure no test passes only after enabling async-unsafe Django settings. Isolate settings and
restore dependency overrides to prevent order-dependent results.

For a multi-row operation, inject a failure between writes and observe persisted rollback. Verify
repeated safe operations create one record. Run concurrent writers against the actual production
engine with independent connections. SQLite is useful for a fast companion example but cannot
establish PostgreSQL row-lock or isolation correctness.

## Useful delivery evidence

Check query-count growth as fixtures increase, not one magic count unrelated to a requirement.
Run migration checks and test old-to-new schema/data transitions when migrations change. Scope the
checks to the change and explain any unrun integration check. The `examples/django-async` suite
exercises real async requests, wrong-owner reads/writes, CSRF, idempotent shipments and rollback.
Start from those observable contracts, adapting them to the real project's authentication model.
