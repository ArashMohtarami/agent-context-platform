---
id: github-writing
name: GitHub Technical Documentation Writing
version: 1.1.0
description: Write GitHub architecture documentation, READMEs, ADRs, examples, reproducible installation commands, troubleshooting, and API usage guides.
category: content
trust_level: trusted
tags:
  - content
  - github
  - documentation
  - architecture
---

# GitHub Technical Documentation Writing

## Documentation purpose

Use this skill to write GitHub architecture documentation, a README, an architecture decision
record (ADR), an example, installation guidance, configuration reference, troubleshooting guide, or
API usage documentation. First identify the reader's task: evaluate the repository, run it locally,
configure it safely, understand an architectural decision, or integrate with an API.

Documentation should reflect the repository and verified environment. Do not claim commands,
configuration values, endpoints, compatibility, or operational guarantees that have not been
confirmed. Mark placeholders and environment-specific assumptions explicitly.

## README and task paths

A README should make the first successful path obvious: purpose, prerequisites, installation,
configuration, a reproducible command, expected result, and a route to deeper documentation. Keep
commands copyable. State the working directory, required variables, ports, files, and cleanup impact
when relevant. Avoid commands that silently mutate or destroy data without a warning.

Examples should be minimal and executable. Pair each command or code block with the input it expects
and the outcome it produces. API usage documentation should show method, path, authentication,
request shape, successful response, errors the caller can act on, and pagination or concurrency
semantics where applicable.

## Architecture and ADRs

Architecture documents should describe components, responsibilities, boundaries, data flow,
dependencies, and operational constraints. Explain why the boundaries exist, not only their names.
For an ADR, record context, decision, alternatives considered, consequences, and follow-up work.
Preserve uncertainty rather than rewriting an unresolved choice as settled history.

Troubleshooting sections should map a recognizable symptom to diagnostic steps, likely causes, and
safe remediation. Prefer observable evidence such as log fields, health endpoints, exit codes, or
configuration checks over guesses.

## Review checklist

- Can a new contributor complete the documented task with reproducible commands?
- Are installation and configuration prerequisites explicit and safe?
- Do architecture documents and ADRs explain rationale and tradeoffs?
- Are API examples complete enough to integrate without guessing?
- Does troubleshooting lead from symptom to verifiable remediation?

## Make examples reproducible and navigable

State prerequisites, working directory, environment/credential injection and expected output before
commands that depend on them. Use placeholders that are clearly replaceable and never embed a real
token. Keep safe read/check commands distinguishable from data-changing or recovery steps. Link
canonical contracts rather than duplicating configuration that will drift.

For an integration guide, show one complete path from setup to verification, then focused variants.
Explain persistence versus eventual search visibility and the actual error/retry behavior. Test
copyable JSON/TOML/shell syntax and observable workflow outcomes where possible.

Documentation should state what was verified and the practical limits. A local fake or schema
validation cannot establish a live provider connection. Update examples and generated references
alongside changed interfaces.
