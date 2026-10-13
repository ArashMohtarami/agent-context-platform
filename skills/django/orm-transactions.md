---
id: django-orm-transactions
name: Django ORM and Transaction Design
version: 1.0.0
description: Design Django queries, database constraints, atomic state transitions, efficient relationship loading,
  and safe schema/data migrations. Use for ORM correctness, N+1 problems, races, or migration reviews.
category: django
trust_level: trusted
tags:
- django
- orm
- transactions
- query-performance
- migrations
---

# Django ORM and Transaction Design

## Start from an invariant and workload

Identify the production database, isolation level, affected rows, predicate, expected cardinality,
and competing writers. Express uniqueness and legal state constraints in the database when possible.
`get_or_create()` cannot substitute for a missing unique constraint under concurrent writes.
Choose pessimistic locking or a conditional update based on the invariant, not habit.

A short service fragment for the companion's models:

```python
from django.db import transaction
from orders.models import Order, Shipment

def ship_order(order_id, owner_id):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id, owner_id=owner_id)
        if order.status not in {"pending", "shipped"}:
            raise ValueError("invalid_transition")
        if order.status == "pending":
            order.status = "shipped"
            order.save(update_fields=["status"])
        shipment, _ = Shipment.objects.get_or_create(order=order)
        return {"id": str(order.pk), "status": order.status, "shipment_id": str(shipment.pk)}
```

Row locks depend on backend and transaction support; SQLite does not validate PostgreSQL locking.
Keep locks short and acquire multiple resources in a consistent order. Catch `IntegrityError`
outside the relevant `atomic()` block so rollback happens before additional queries. Python model
state can remain changed after a rollback; reload rather than reusing an assumed persisted value.
[Transaction semantics](https://docs.djangoproject.com/en/5.2/topics/db/transactions/).

## Make query cost visible

Measure query counts for increasing result size. Use `select_related` for single-valued relations,
`prefetch_related` for multi-valued relations, and annotations when the database should compute a
summary. Select only useful columns without introducing deferred-field reads. Avoid fetching all
rows to count or test existence; avoid count-plus-fetch when you actually need the fetched rows.
Inspect the database plan for representative data, not only fixtures.
[Optimization guide](https://docs.djangoproject.com/en/5.2/topics/db/optimization/).

Require deterministic ordering for pagination, with a unique tie-breaker. Use keyset pagination
when deep offsets matter. Test inserts during paging against the documented consistency policy.
Cache keys include owner/tenant and relevant version/filter identity. Define invalidation and
staleness before adding a cache that could conceal query mistakes.

## Migrations and effects

Use historical models through `apps.get_model()` in `RunPython`. Backfill in bounded batches;
review whether the migration must be non-atomic for the selected backend. Specify reverse behavior
or explicitly document an irreversible data transformation. Separate adding a nullable field,
backfilling, adding constraints and removing the old field for rolling compatibility.
[Migration authoring](https://docs.djangoproject.com/en/5.2/howto/writing-migrations/).

After-commit callbacks are suitable for noncritical notifications; they do not create a durable
handoff. Use a transactional outbox for delivery that must survive a process crash. Verify rollback,
duplicate inserts, wrong-owner operations, competing writers, query budgets, and migrations from
realistic prior schema/data states. Report backend-specific limitations explicitly.
