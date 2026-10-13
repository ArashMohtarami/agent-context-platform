---
id: react-accessibility
name: React State and Accessible Interaction
version: 1.0.0
description: Build React interactions with clear state ownership, accessible forms, focus/error behavior and correct
  effect cleanup. Use for functional UI work and interaction regressions.
category: frontend
trust_level: trusted
tags:
- react
- accessibility
- forms
- state
- effects
---

# React State and Accessible Interaction

## Model the user's task

Identify entry, loading, success, empty, invalid, failed and retry states. Choose one owner for each
piece of state; derive values when possible rather than keeping synchronized duplicates. Preserve
existing routing and component conventions. Keep implementation details out of product flows
unless they help the user make a decision.

Use effects to synchronize with an external system, not to calculate render-derived state or
respond to every user action indirectly. Clean up subscriptions and coordinate stale requests.
Development re-runs can expose missing cleanup; suppressing them is not a repair.
[Effect lifecycle](https://react.dev/learn/synchronizing-with-effects).

## Make interaction operable

Use semantic buttons, labels and form controls. Keep keyboard focus visible and define where focus
moves after navigation, modal open/close or validation failure. Associate errors with fields and
announce changes when they would otherwise be invisible to assistive technology. Avoid duplicate
submits while preserving a recoverable error state.

Dialog interactions need a name, focus containment, Escape/close behavior and focus restoration.
Respect reduced-motion preferences and give control over continuous animations. Do not use color
alone for state. Verify layout with narrow screens, zoom and long text rather than only one
perfectly sized mockup.

## Data, permission and tests

Render fetched text safely and validate network data. Filter client choices for usability while
letting the server enforce grants. Avoid showing another identity's cached data after login/project
switches. Define the meaning of a retry after an uncertain write.

Test the whole user task with keyboard input, invalid fields, dependency failure and successful
recovery. Prefer observable role/name/state assertions to component-internal snapshots. Report
which real interactions, viewports and accessibility conditions were checked.
