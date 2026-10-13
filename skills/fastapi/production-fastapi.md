---
id: fastapi-production
name: FastAPI Production Engineering
version: 1.1.0
description: Production service boundaries, lifecycle management, and API validation for FastAPI.
category: backend
trust_level: trusted
tags:
  - python
  - fastapi
  - api
---

# FastAPI Production Engineering

## Application composition

Create the FastAPI application through a factory so tests can supply configuration and controlled
dependencies. Keep route handlers thin: parse transport input, call an application service, and map
safe domain outcomes to HTTP responses. Business decisions belong in services, while persistence
queries belong in repositories.

## Lifecycle

Own long-lived clients in the application lifespan. Connect and verify dependencies before accepting
traffic, publish initialized handles through explicit application state or dependencies, and close
them in a `finally` path. Liveness should only show that the process runs. Readiness should fail when
a required database or coordinator is unavailable.

This lifespan fragment assumes the project's composed `resources` adapter:

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await resources.connect()
    try:
        yield
    finally:
        await resources.close()
```

## Request validation

Use Pydantic schemas for external contracts and reject unexpected fields on write requests. Apply
bounded limits to lists and searches. Return stable public identifiers rather than leaking database
implementation details. Map absent resources to 404, invalid input to 422, and dependency failures
to safe service errors without exposing stack traces or credentials.

## Operational behavior

Use structured logs with request identifiers, method, path, status, and duration. Do not log query
secrets, authorization values, or full sensitive payloads. Run the service as a non-root user behind
explicit network boundaries, pin dependencies, and keep generated OpenAPI documentation aligned
with response and error schemas.

## Authorization and retry-safe writes

Use dependencies for verified identity and operation grants, then enforce resource ownership in the
query/service. A request model's `project_id` or owner field is not proof of access. Apply the same
policy to list/search/export and nested resources. Define whether unauthorized IDs produce 403 or
an indistinguishable 404 and keep the public contract consistent.

For creation retries, compare stable operation identity and payload. For updates, filter by the
revision actually read and report a conflict; do not silently accept a lost update. Keep external
side effects outside an ambiguous transaction, using reconciliation/outbox where needed. Cookie
credentials require deliberate CSRF protection; CORS is not an authorization mechanism.

## Async and boundary verification

An async route must await async clients or explicitly bridge blocking work. Framework threadpool
behavior does not make arbitrary sync calls inside `async def` nonblocking. Bound body/page/search
size, concurrent work and dependency deadlines. In-process background tasks do not guarantee a
job survives process death.

Exercise lifespan in tests, restore dependency overrides, and test response serialization as well
as service results. Verify wrong-owner requests, invalid input, stale revisions and uncertain retry
outcomes. [Lifespan reference](https://fastapi.tiangolo.com/advanced/events/). For actual database
semantics, use an appropriate integration test rather than only a mocked repository.
