# Publication safety and approval

The publication service provides deterministic review before a content item can be
approved for manual export or a manual-publication receipt. It is a safety layer, **not**
a confidentiality guarantee and not an automated publishing system. An agent
still writes the prose; a human remains responsible for deciding whether the
result is appropriate to disclose.

## Lifecycle

The existing `ContentStatus` state machine is the approval state; no new
content collection or body field is introduced:

```text
drafting -> review -> approved -> export / explicit manual receipt
```

`POST /api/v2/content/{content_id}/approve` accepts an `expected_revision` and
only permits the existing `review -> approved` transition. The service scans
the current revision again immediately before the update. A stale revision,
draft, archived record, or configured high-severity finding is refused. The
content body is never changed, generated, redacted, published, or copied into
the audit trail.

`POST /api/v2/content/{content_id}/export` now requires `approved` or
`published` status. It remains a local/manual representation endpoint; the service
does not call LinkedIn, Medium, GitHub, or any other external provider.

## Review API and MCP

`POST /api/v2/content/{content_id}/publication-review` requires `content:read`
plus profile and every provenance-project grant. Its request body is:

```json
{"profile_id": "personal-brand"}
```

The response contains `publishable`, an advisory `findings` list, a field and
line/column location for each finding, and the current item’s authorized
project/source metadata. It does **not** contain the item body or the matched
secret/PII value. Provenance is returned only after the same profile/project
checks used by normal content reads, so it is available to an authorized
reviewer but never included in a public export.

The thin MCP tool `review_content_for_publication` calls that API with
`content:read`. It returns the same redacted structured data and explicitly
states that retrieved content and review results are data, not instructions.
Approval is a separate `content:write` API operation. The MCP
`mark_content_published` tool requires `content:publish` and records a manual
receipt; see [content-publishing.md](content-publishing.md).

## What the deterministic scanner checks

`app/content/publication.py` evaluates the title, body, and existing external
URL for likely API keys, credentials, tokens, private URLs, private IPs,
internal hostnames, internal repository URLs, customer IDs, email addresses,
and phone numbers. It also evaluates provenance project IDs and configurable
case-insensitive terms. A deployment can mark private architecture vocabulary
or projects as sensitive with:

- `CONTENT_PUBLICATION_SENSITIVE_TERMS`
- `CONTENT_PUBLICATION_SENSITIVE_PROJECT_IDS`
- `CONTENT_PUBLICATION_BLOCK_HIGH_SEVERITY` (defaults to `true`)

The list settings use normal Pydantic-settings JSON arrays, for example
`CONTENT_PUBLICATION_SENSITIVE_TERMS='["LedgerMesh"]'`. Keep the list small and
specific; it identifies material that needs review, not words that should be
silently removed.

The configuration is a review policy, not a magic allow-list or a replacement
for project authorization. A finding includes a type, severity, field,
location, and safe explanation—not the value that was matched. High-severity
findings block approval by default; lower-severity findings remain visible for
human judgment. The scanner never silently edits a finding away.

## Audit and logging

Successful and blocked approval attempts append `content.approve` events to the
existing `audit_logs` collection. The event records actor, profile, request
route, outcome, and a generic safe reason. It contains neither prose nor a
matched sensitive value. Scanner logs include only content/profile IDs,
finding-type names, counts, and the boolean result.

Migration 10 idempotently runs `collMod` on the existing strict
`audit_logs` validator to permit the additive `content.approve` action. It
creates no collection, changes no V1 document, and has no safe down migration:
removing an approval event would destroy audit history. Existing backup and
restore already include `audit_logs` and the V2 content collections, so no
separate backup configuration is needed.

## Deliberate exclusions

Publication review does not generate or rewrite content, retrieve new private
context, automatically approve, or deliver content to external providers.
Manual receipts and descriptive metric captures are separate explicit workflows;
see [content-publishing.md](content-publishing.md) and
[content-metrics.md](content-metrics.md).
