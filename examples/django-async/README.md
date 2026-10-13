# Async Django boundary example

This isolated Django 5.2 teaching project demonstrates an authorized async ORM read,
a synchronous transactional write reached through `sync_to_async`, CSRF protection,
and safe repeat shipment operations. It changes no platform dependencies or database.

From the platform repository root:

```bash
python3.12 -m venv /tmp/agent-django-demo-venv
/tmp/agent-django-demo-venv/bin/python -m pip install -r examples/django-async/requirements.txt
/tmp/agent-django-demo-venv/bin/python examples/django-async/manage.py test orders
/tmp/agent-django-demo-venv/bin/python examples/django-async/manage.py check
```

The test runner creates/discards its test database. No migration or server start is
needed for the tests: Django synchronizes the example's unmigrated models.

To experiment locally, use `manage.py migrate --run-syncdb` and
`manage.py createsuperuser`, then wire a local login flow appropriate to your test.
The example intentionally has no login UI or provider calls. Keep `demo.sqlite3`
out of Git. Its fixed fallback secret and local settings are only for tests.

SQLite verifies the example's async request, ownership, rollback and unique shipment
behavior. PostgreSQL integration tests with independent connections are required to
establish row-lock and competing-writer behavior. A shipment here is a database
record, not an external fulfillment action. Safe retry applies to this operation;
it is not a general idempotency protocol for arbitrary payloads or providers.

In the platform, create `orders-django` as a separate project, search
`django async transaction`, load `django-async`, `django-orm-transactions` and
`django-testing`, inspect this code, run the suite, and save only a reviewed
project-specific decision. Detailed agent instructions are in the HTML usage guide.
