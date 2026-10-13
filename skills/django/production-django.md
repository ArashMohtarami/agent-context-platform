---
id: django-production
name: Production Django Engineering
version: 1.0.0
description: Build and review Django applications with explicit domain boundaries, authorized queries, validated
  input, migration discipline, and production settings. Use for synchronous Django features and service design.
category: django
trust_level: trusted
tags:
- django
- models
- views
- security
- deployment
---

# Production Django Engineering

## Select the actual contract

Read the installed Django/Python versions, settings modules, URL configuration, apps, middleware,
models, forms/serializers, and deployment entrypoint before proposing a change. Examples here target
Django 5.2 APIs; use the project's version documentation when it differs. Preserve the current
framework and architecture. Async request work belongs with `django-async`; DRF endpoint contracts
belong with `django-rest-framework`.

Trace the feature from URL to validated input, authorization, query/service, and response. Keep
simple reads in views or selectors when that is clearer; introduce services for multi-step business
operations. Avoid a repository wrapper that merely repeats every ORM method. Give an operation a
visible invariant, such as "one shipment per order", and a database constraint where possible.

## Ownership and input boundaries

Derive owner/tenant identity from authenticated server state. Scope the queryset before fetching a
record; a random UUID does not grant access. Apply the same restriction to lists, detail views,
exports, bulk actions, nested resources, and admin actions. Decide deliberately whether unauthorized
records produce 403 or an indistinguishable 404, and keep that contract consistent.

Use forms/ModelForms or serializers for external data, choosing writable fields explicitly.
Model `save()` does not call `full_clean()` automatically. Database constraints remain necessary
for races. Compute sensitive fields, prices, ownership, and privilege flags on the server. Keep
HTTP safe methods free of business mutations. Cookie-authenticated writes retain CSRF protection.

## Data and side effects

Bound query counts and result sizes. Load related data explicitly, choose deterministic pagination,
and distinguish "missing" from dependency failure. Use a short transaction for a multi-row invariant;
keep network I/O outside row locks. After-commit callbacks avoid effects on rolled-back data, but
critical delivery needs a durable outbox or a queue design that handles the crash gap.

For schema changes, review the generated migration, lock cost, backfill size, concurrent application
versions, and recovery approach. Never edit an applied migration to change its historical meaning.
Use expand/backfill/contract for incompatible changes instead of combining deletion and rollout.

## Deployment and verification

Run the project checks and `manage.py check --deploy` with the intended production settings. Inspect
DEBUG, host/proxy trust, HTTPS/session/CSRF cookies, secrets, static/media storage and worker/database
budgets. Treat uploaded files as untrusted data. A passing checklist is only one input to deployment.
[Deployment settings reference](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/).

Verify allowed and denied owners, invalid input, duplicate retries, rollback, pagination and missing
records at the HTTP boundary. Use the real database engine for locking and query-plan conclusions.
Deliver the resulting behavior, applicable tests, migration impact and any remaining assumptions.
The runnable `examples/django-async` project demonstrates scoped reads and an atomic write helper;
it is a teaching application, not a production deployment template.
