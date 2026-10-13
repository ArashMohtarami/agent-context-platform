---
id: content-ideation
name: Technical Content Ideation
version: 1.1.0
description: Frame evidence-backed technical content ideas from engineering lessons, architecture decisions, implementation tradeoffs, and useful questions.
category: content
trust_level: trusted
tags:
  - content
  - ideation
  - engineering-lessons
  - architecture
---

# Technical Content Ideation

## Start from evidence

Frame content ideas from verified engineering knowledge: a design decision, implementation lesson,
failure mode, tradeoff, debugging method, operational practice, or question a reader can apply. An
idea is not evidence by itself. Preserve the source, project boundary, privacy constraints, and
uncertainty that determine whether the subject can be discussed publicly.

Useful starting questions include: What decision was difficult? Which assumption changed? What
failure mode did the team design around? What tradeoff would another engineer otherwise miss? What
small implementation detail made a larger architecture reliable? Avoid treating novelty as a reason
to publish if the idea cannot be grounded or would expose private context.

## Shape a candidate idea

State one audience, one primary takeaway, the supporting evidence, and the appropriate platform
shape. A LinkedIn candidate may focus on a concise engineering lesson; a Medium candidate can
support a first-principles architecture article; a GitHub candidate may become a README section,
ADR, example, troubleshooting page, or API usage guide.

List the limits alongside the opportunity: missing proof, sensitive details, stale information,
similar material already published, or an audience mismatch. These are review inputs, not obstacles
to hide with generic prose.

## Candidate checklist

- Is there a verified source or clearly marked research requirement?
- Does the idea have one useful takeaway for a named audience?
- Is private project context protected pending explicit review?
- Is the proposed platform shape appropriate for the reader's task?

## Advanced selection and example

Separate candidate generation from persistence. Compare audience need, usable evidence, specificity,
missing proof, privacy and overlap with existing content. A novelty score is advisory; an unfamiliar
claim may simply be wrong. Preserve rejected candidates' reasons when they guide the next research
step rather than generating endless minor rewrites.

Example candidate: "Why our retry operation has a stable key" for service engineers. Evidence:
a reviewed API contract and duplicate-request test. Limit: no measured latency improvement.
LinkedIn angle: one failure mode and invariant. GitHub angle: reproduce the duplicate request and
explain the response. Do not turn this into "we eliminated all payment failures".

Deliver a concise candidate/brief with source IDs, a clear reader takeaway and required review.
Use the actual creator profile for voice/platform preferences instead of inventing a brand.
