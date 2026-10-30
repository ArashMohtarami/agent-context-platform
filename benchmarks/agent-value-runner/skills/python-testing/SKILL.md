---
name: python-testing
description: Choose bounded behavioral checks and explain what they prove.
---

# Python Behavioral Testing

## Start with the observable failure

Describe the trigger, result and invariant before selecting mocks. A regression test should fail
because a user-visible contract is broken, not because one internal helper was renamed. Pure
transformations need unit tests; adapters need boundary tests; database locking/query behavior needs
the real engine. Do not add a test for every reversible documentation or styling edit.

Use the project's existing test framework and commands. Keep fixtures narrow, with cleanup owned
by the fixture that creates a resource. Function-scoped mutable fixtures limit state leakage.
[Fixture lifetimes and teardown](https://docs.pytest.org/en/stable/how-to/fixtures.html).

## Useful cases and doubles

Cover accepted input, rejected input, missing resources, denied ownership, a meaningful dependency
failure and uncertain retries when relevant. Distinguish omitted values, explicit null and empty
collections if the contract does. Use parametrization for genuinely parallel cases rather than
combining unrelated assertions in one large scenario.

Mock the adapter the application consumes, not deep library internals. A recording HTTP/database
fake verifies calls but does not prove server/driver semantics. Use a local server or real database
for the behavior that depends on serialization, transactions, indexes or network lifecycle. Never
turn off authorization to simplify a test of an authorized endpoint.

## Determinism and delivery

Inject clocks/randomness where the domain needs a stable decision. Coordinate concurrent tests
with events/barriers and independent connections. Restore environment variables, overrides and
shared state. Keep test order irrelevant; assert no resource/task leakage for lifecycle changes.

Run checks proportional to the change, then broaden when a failure or an unresolved interaction
justifies it. Explain what each integration test proves and what was unavailable. Treat line
coverage as a gap finder, not evidence that authorization, rollback or retry behavior is correct.
