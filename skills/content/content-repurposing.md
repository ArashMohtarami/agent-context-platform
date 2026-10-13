---
id: content-repurposing
name: Technical Content Repurposing
version: 1.1.0
description: Adapt one verified technical idea for LinkedIn, Medium, and GitHub while preserving provenance, factual limits, and platform-specific purpose.
category: content
trust_level: trusted
tags:
  - content
  - repurposing
  - linkedin
  - medium
  - github
---

# Technical Content Repurposing

## Preserve the source of truth

Repurposing changes communication shape, not factual provenance. Start with a verified source idea,
its evidence, intended audience, known limits, and information that must remain private. Keep those
facts stable across every adaptation. Do not introduce a stronger result, invented anecdote, or more
certain conclusion merely because another platform rewards a different style.

## Reframe for each platform

For LinkedIn, isolate one practical lesson and give it a strong, honest opening with short readable
paragraphs. For Medium, expand the same idea into problem/context, first-principles explanation,
implementation detail, tradeoffs, alternatives, and a practical conclusion. For GitHub, turn the
idea into an operational artifact: README guidance, architecture documentation, ADR context,
reproducible examples, configuration, troubleshooting, or API usage.

Each version should have a distinct reader task. Do not merely truncate an article into a post or
paste a social explanation into repository documentation. Link or reference the original source when
that improves verification, but do not disclose private project context without explicit review.

## Review checklist

- Does every adaptation preserve the original evidence and uncertainty?
- Does each platform version serve a distinct reader task?
- Has private context been removed or explicitly approved for review?
- Is the adaptation useful on its own rather than a promotional fragment?

## Use an evidence matrix

List each material claim, its source, permitted disclosure, version/date and essential qualification.
Check each adaptation against that matrix. The platform/format may change emphasis, but it may not
upgrade an inference into an observed result or remove the condition that makes a claim valid.

For an async Django lesson, a short post may explain one hidden lazy-query trap; an article can
trace ASGI, middleware and transaction boundaries; GitHub documentation can give a tested example
and version requirements. Each artifact should keep the same transaction limitation and synthetic
sample label where relevant.

Review code and claims after adaptation, not only the original. Record links/provenance without
exposing private paths or identifiers. Avoid duplicate drafts whose only difference is length;
choose a useful reader task for each target.
