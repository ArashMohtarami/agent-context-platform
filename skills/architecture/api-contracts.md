---
id: api-contracts
name: API Contracts and Compatibility
version: 1.0.0
description: Design and evolve REST or tool API contracts with explicit validation, authorization, retry semantics,
  pagination, errors and compatibility. Use when clients and services need a stable shared boundary.
category: architecture
trust_level: trusted
tags:
- api
- contracts
- pagination
- idempotency
- compatibility
---

# API Contracts and Compatibility

## Define inputs, outputs and authority

Identify callers, ownership, operations and supported versions. Specify required/optional/null fields,
bounds, unknown-field policy and server-derived fields. A schema validates shape; authorization
still decides which resource may be read or changed. Document success and safe error envelopes with
representative accepted and rejected examples.

Keep authentication, operation scopes and per-resource grants distinct. Apply resource predicates
before fetching data. Decide public missing/forbidden behavior and test it consistently across list,
detail, search, export and mutation. Do not let caller-supplied headers assign a trusted identity.

## Retries and concurrent change

Use safe/idempotent HTTP semantics deliberately. For retryable creation, define a stable operation
key, payload identity, response replay and retention. A changed payload with the same key should
not become an accidental new operation. For updates, compare the version actually read using a
revision or ETag and return an explicit conflict when it changed. Reload and reconcile instead of
blindly incrementing the expected revision.

Set deadlines and bounded page/search limits. Choose deterministic ordering and define cursor
meaning under insertion/deletion. Keep tokens opaque to clients and bound/validate decoded cursor
state. Explain whether a successful asynchronous acceptance means queued, persisted or complete.

## Compatibility and verification

Distinguish additive schema changes from behavioral breaks. New required fields, narrower accepted
values, changed error semantics or different ownership behavior can break clients without changing
the URL. Plan reader/writer rollout order and deprecation evidence. A generated OpenAPI/tool schema
is useful only when the running endpoint implements it.

Exercise real boundary requests with the supported client formats. Verify invalid data, wrong
owners, retries, conflicting revisions, pagination and response serialization. Report exact version
and rollout impact. Prefer a bounded typed contract over passing arbitrary dictionaries through
every layer.
