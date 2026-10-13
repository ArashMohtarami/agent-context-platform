---
id: django-async
name: Async Django Engineering
version: 1.0.0
description: Implement and diagnose Django async views, async ORM evaluation, ASGI middleware boundaries, cancellation,
  and safe synchronous transaction bridges. Use when concurrency or async-safety is part of the task.
category: django
trust_level: trusted
tags:
- django
- async
- asgi
- orm
- sync-to-async
---

# Async Django Engineering

## Decide which boundary benefits

Confirm the Django version and ASGI deployment before changing call styles. Async benefits waiting
on independent I/O; it does not make a database query or CPU calculation intrinsically faster.
Map each operation as async I/O, synchronous thread-sensitive work, or CPU work. Preserve ordinary
synchronous views when concurrency is not useful. Check installed middleware for adaptation and
measure the real request path. [Middleware capability declarations](https://docs.djangoproject.com/en/5.2/topics/http/middleware/).

## Evaluate queries deliberately

Building `filter()`, `order_by()`, `select_related()` and `values()` querysets is lazy. Evaluate
using the available async method, such as `aget()`, `aexists()`, `acount()` or `async for`.
Do not call `list(queryset)`, `len(queryset)`, `bool(queryset)` or synchronous terminal methods
inside an async view. Deferred fields and unloaded relationships can issue hidden queries.
Prefer a bounded `values()` DTO or preloaded relations before serialization. These APIs and their
limitations are versioned: [QuerySet reference](https://docs.djangoproject.com/en/5.2/ref/models/querysets/).

Illustrative fragment using the companion example's `Order` model and an authenticated user:

```python
from django.http import JsonResponse
from orders.models import Order

async def order_detail(request, order_id):
    user = await request.auser()
    if not user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=401)
    try:
        row = await Order.objects.filter(owner_id=user.pk).values("id", "status").aget(pk=order_id)
    except Order.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)
    return JsonResponse({"id": str(row["id"]), "status": row["status"]})
```

## Keep transactions in one synchronous island

For Django 5.2, put transactional work in one synchronous function and await it through
`sync_to_async(..., thread_sensitive=True)`. Keep all ORM evaluation inside that helper, returning
plain evaluated data. Do not carry a connection, cursor or unevaluated queryset across the boundary.
Disable persistent Django connections (`CONN_MAX_AGE=0`) for this async deployment; select supported
backend pooling separately. Do not enable `DJANGO_ALLOW_ASYNC_UNSAFE` to suppress a failure.
[Async support and adapter constraints](https://docs.djangoproject.com/en/5.2/topics/async/).

```python
from asgiref.sync import sync_to_async
from orders.services import ship_order

async def mark_shipped(order_id, owner_id):
    return await sync_to_async(ship_order, thread_sensitive=True)(order_id, owner_id)
```

A cancelled await does not reliably interrupt an already-running synchronous database helper.
Use database timeouts and retry-safe operation identity. Reconcile a possibly committed write
before retrying it. Thread-sensitive ORM calls may serialize; do not infer parallel DB execution
from `gather()`.

## Concurrency, disconnects and tests

Use an owned async HTTP client, a bounded concurrency budget and an overall deadline for external
calls. Keep errors attributable to the dependency. Clean up streaming subscriptions on disconnect
and re-raise cancellation after cleanup. Never use an untracked `create_task()` as durable work.
The Python concurrency skill covers structured task ownership and failure behavior.

Exercise the real async view with `AsyncClient`. Test unauthenticated access, wrong owners, missing
records, CSRF for writes, transaction rollback and safe retries. Measure request latency and
middleware adaptation under ASGI. The companion uses SQLite to teach boundaries; run PostgreSQL
integration tests before claiming row-lock/concurrent-writer correctness.
