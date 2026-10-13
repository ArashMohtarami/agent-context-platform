---
id: typescript-contracts
name: TypeScript Runtime and API Contracts
version: 1.0.0
description: Design TypeScript client contracts with runtime validation, discriminated states, null semantics and
  compatible API changes. Use for typed frontend/data boundaries rather than cosmetic UI edits.
category: frontend
trust_level: trusted
tags:
- typescript
- types
- validation
- api
- frontend
---

# TypeScript Runtime and API Contracts

## Validate outside data

Read compiler settings, framework, schema tooling and API definitions. A TypeScript assertion does
not validate JSON. Treat incoming network/storage/message values as `unknown` and narrow or parse
at the boundary with the project's selected validator. Keep the validated domain type through
subsequent code instead of repeated casts.

Use discriminated unions for distinct states rather than several independent booleans. Narrow
through the discriminator and handle all domain outcomes. Avoid `any` escapes that hide a changed
contract. [Narrowing and exhaustiveness](https://www.typescriptlang.org/docs/handbook/2/narrowing.html).

```typescript
type LoadState<T> =
  | { status: "idle" }
  | { status: "loading"; requestId: string }
  | { status: "ready"; value: T }
  | { status: "error"; message: string };
```

## Preserve transport meaning

Distinguish absent, null, empty and defaulted fields where the server does. Model error envelopes
and conflict/retry behavior, not only success objects. Keep authorization on the server; hiding a
button is an interaction choice. Do not expose credentials or private data through browser logs,
error messages or stale cross-user caches.

Coordinate API compatibility and generated-client updates. When request identity changes, cancel
or ignore stale responses so a late result cannot replace the current screen. Scope cache keys to
the owner/project/filter identity and clear sensitive state when identity changes.

## Verify boundary behavior

Test invalid server data, each relevant union outcome, conflicting updates, loading/error/retry
transitions and outdated response arrival. Run the project's typecheck/build and meaningful UI
checks. If a schema is generated, verify runtime examples against it; generated types alone do
not establish compatibility with the deployed service.
