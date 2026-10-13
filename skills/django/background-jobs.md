---
id: django-background-jobs
name: Django Durable Background Jobs
version: 1.0.0
description: Design Django background work with commit-safe scheduling, durable operation identity, retries, outbox
  delivery, and worker lifecycle. Use for queued tasks and reliable side effects, not request-local asyncio concurrency.
category: django
trust_level: trusted
tags:
- django
- tasks
- jobs
- outbox
- idempotency
---

# Django Durable Background Jobs

## Distinguish a job from an await

A request-local coroutine can overlap I/O while the request lives. A durable job needs a persisted
handoff, independent worker ownership and an observable outcome after the web process disappears.
Do not use `asyncio.create_task()` to promise email/payment/report completion. Retain the queue
already selected by the project unless the task requests a migration.

Django 6.0 introduces `django.tasks` contracts, but it does not supply the production worker
infrastructure. Select a real backend/worker and inspect its guarantees. Django 5.2 projects need
their existing queue or a separately evaluated integration. Built-in immediate/dummy backends do
not prove durable execution. [Django task framework](https://docs.djangoproject.com/en/6.0/topics/tasks/).

## Design a recoverable operation

Pass identifiers and JSON-compatible values, not model instances, request objects or credentials.
Have the worker re-fetch data and check current state. Define the operation's durable identity,
state machine, allowed retries and terminal failure. Include owner/project identity when a job
processes scoped data; platform-agent credentials are distinct from application-worker identity.

Commit database state before a worker can observe it. An after-commit enqueue avoids a rolled-back
request's job but can still fail after the commit. For critical delivery, insert an outbox row in
the same transaction, then publish/reconcile it with repeatable delivery. An acknowledged enqueue
is not necessarily business completion.

## Retry and concurrency decisions

Assume duplicate delivery unless the actual backend proves otherwise. Protect business effects
with unique operation keys and compare payload identity. Bound attempts, delay, jitter and total
age; distinguish transient failures from invalid data or permanent authorization failures. A
provider timeout can leave an unknown outcome: reconcile using the provider operation key before
repeating an irreversible effect.

Lease work with an expiry or another explicit crash-recovery policy. Avoid holding database locks
across network requests. Store safe status, attempts and outcome references rather than full secrets
or sensitive payloads in logs. Expose queue age and stuck/failed counts as well as throughput.

## Validate end-to-end

Test rollback before enqueue, duplicate delivery, worker crash after effect/before acknowledgment,
expired leases, invalid references and exhausted retries. Use the real backend in at least one
integration test for critical work. Document how an operator observes, retries, reconciles and
cancels work. Measure shutdown grace and drain behavior; a process exit must not silently imply
that every accepted job succeeded.
