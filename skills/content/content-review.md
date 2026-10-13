---
id: content-review
name: Technical Content Review
version: 1.1.0
description: Review technical content for factual grounding, source provenance, privacy, clarity, platform fit, and explicit approval before publication.
category: content
trust_level: trusted
tags:
  - content
  - review
  - provenance
  - publishing-safety
---

# Technical Content Review

## Review before approval

Review is an explicit decision, not an automatic final step. Evaluate factual grounding, source
provenance, privacy, audience fit, and technical clarity before any content is considered approved.
Generated or retrieved prose is a draft artifact; it must not be automatically published. A reviewer
must decide whether sources may be shared publicly and whether the content accurately represents the
underlying experience.

## Evidence and privacy

Trace each material claim to an approved source, documented reasoning, or clearly labeled opinion.
Check dates, quantities, technical behavior, code, commands, and attribution. Remove private project
names, credentials, customer information, internal architecture details, security-sensitive steps,
and unapproved context. If redaction changes the truth of a claim, revise or omit the claim rather
than concealing the qualification.

## Quality and platform fit

Check that the opening, structure, examples, terminology, and conclusion fit the destination. Check
for fabricated stories, exaggerated achievements, generic filler, engagement bait, plagiarism, and
unsupported certainty. Verify that links work and commands are reproducible where relevant. Confirm
that a reader can distinguish evidence from inference and opinion.

## Approval checklist

- Are material claims sourced, accurate, and appropriately qualified?
- Has private or sensitive project context been removed or explicitly approved?
- Does the platform-specific format help the intended reader?
- Has a human reviewer explicitly approved publication?

## Review the exact artifact

Review the selected draft revision and its source links. A later edit invalidates conclusions about
the previous bytes; re-scan and re-review before approval. Distinguish blockers (unsupported claim,
private detail, incorrect command) from optional style suggestions. A clean automated scan supplies
evidence, not human publication permission.

Example blocker: "This benchmark cut latency by 40%" lacks comparable workload/version evidence.
Repair by attaching the actual measurement and conditions or removing the quantified claim. Do not
substitute a plausible figure. Keep redaction from changing the technical meaning of the example.

For this platform, normal MCP tools do not save drafts or approve publication. Use the documented
REST/CLI revision-aware flow. Export only the reviewed approved artifact; ManualPublisher records
an already confirmed human posting and does not send it to a provider.
