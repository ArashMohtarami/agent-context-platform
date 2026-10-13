---
id: content-editing
name: Technical Content Editing
version: 1.1.0
description: Edit technical writing for evidence, clarity, structure, accessible language, and platform fit while preserving factual limits.
category: content
trust_level: trusted
tags:
  - content
  - editing
  - technical-writing
  - review
---

# Technical Content Editing

## Edit in passes

Edit in separate passes so style improvements do not hide factual problems. First check evidence:
remove unsupported claims, fabricated details, accidental certainty, confidential project context,
and claims that imply an unverified result. Then check structure: one primary idea, a clear sequence,
and a conclusion that follows from the reasoning.

Next edit for clarity. Prefer active, concrete sentences; define necessary terms; replace abstraction
without evidence with an example or a more precise statement. Keep audience knowledge in mind without
removing the technical mechanism that makes advice useful. Shorten paragraphs where the destination
benefits from scanning, but do not turn meaningful reasoning into fragments.

## Preserve intent and provenance

Editing may improve language and organization, but it must not change the author's known experience
or upgrade inference into fact. Keep source attribution, qualifications, and uncertainty when they
matter to a reader's decision. When material is incomplete, flag the missing evidence for review
instead of filling it with plausible prose.

## Final pass

Check terminology, headings, examples, links, code, commands, and formatting for the destination.
For GitHub, test or clearly label commands. For LinkedIn, verify the opening and short paragraph
flow. For Medium, verify that the technical depth and tradeoffs were not lost during shortening.

## Review checklist

- Is the content factual, attributable, and safe to share?
- Is one idea visible from opening through conclusion?
- Does every paragraph earn its place for the intended audience?
- Are technical terms, examples, and commands accurate and usable?

## Preserve a checkable claim

Track changes that affect technical meaning separately from cosmetic edits. If "may serialize"
becomes "always runs in parallel", restore the qualification unless evidence supports the new
claim. Match examples to the stated framework version and ensure setup commands produce the input
needed by later commands.

Example edit: replace "async is faster" with "this request overlaps two independent network waits;
we measured it under the stated workload" when that is the actual evidence. If no measurement exists,
explain the mechanism and omit the performance claim.

For a substantial edit, return the polished artifact and the few unresolved evidence questions
that prevent completion. Do not require arbitrary sentence lengths, heading counts or a universal
voice; follow the user's audience, destination and established profile.
