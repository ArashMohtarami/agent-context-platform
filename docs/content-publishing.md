# Manual publication receipts and publisher interfaces

The publishing service provides an explicit, approval-gated boundary for existing
content records. It does not generate prose, infer an audience, approve content,
or publish as a side effect of creation, editing, versioning, retrieval, export,
or MCP discovery.

## Default deployment

The default and only enabled provider is ManualPublisher. It has no
credentials, makes no network call, and records a human-confirmed publication
of an already-approved stored item. A human may export the item, complete the
platform action outside this service, and then explicitly mark that exact item
published.

github, linkedin, and medium are durable provider vocabulary only. They are
not adapters, are not configured, and return a safe "not configured" failure.
The platform therefore continues to work normally when all external adapters
are disabled.

## Safety and lifecycle

The only supported transition into published is:

~~~text
drafting -> review -> approved -> explicit publish
~~~

The publish service reloads the current item, checks its optimistic revision,
requires approved, and runs the deterministic safety review again.
Configured blocking findings stop the action. This remains a safety layer, not
a guarantee of confidentiality.

Generic content updates cannot set published, published_at, provider, external
ID, external URL, or the publication idempotency key. Creating a new version
returns the new current item to drafting and clears its current publication
receipt. Immutable content versions and audit records remain unchanged.

## API and MCP

POST /api/v2/content/{content_id}/publish requires content:publish, profile
access, every referenced project grant, and:

~~~json
{
  "profile_id": "personal-brand",
  "expected_revision": 4,
  "provider": "manual",
  "idempotency_key": "manual-publish-2026-09-24"
}
~~~

It deliberately accepts no body, title, destination path, provider credential,
or arbitrary text. The response contains the persisted current item and the
redacted provider receipt: provider, optional external ID/URL, timestamp, and
idempotency key.

The thin MCP tool mark_content_published forwards those same fields to the
API. It cannot query MongoDB directly and it cannot publish arbitrary text.

Every attempt is auditable as content.publish with allowed, denied, or failed
outcome. Audit reasons are fixed classifications, not provider error payloads
or content. Provider failures are returned as a generic error and logs never
include provider credentials or provider response bodies.

## Idempotency and retries

If no key is supplied, the service uses the deterministic key
publish:{content_id}:{current_version}. ManualPublisher supports idempotency.
Retrying a completed action with the same provider and key returns the stored
receipt without calling the provider again. A different key for the same
current published representation is refused.

A provider must declare idempotency support before this service will use it.
If a concurrent persistence update loses the optimistic race, the service
re-reads the item and returns the existing matching receipt; otherwise it asks
the caller to re-read and retry. This prevents a retry from silently delivering
a second publication.

## Persistence and migration

Migration 11 adds nullable fields to the existing V2 content_items collection:

- publication_provider
- external_id
- publication_idempotency_key

It first applies a temporary strict validator that permits the new properties,
backfills each missing field with null, then restores the final strict
validator. It also permits the additive content.publish action on the existing
audit_logs validator. The migration is idempotent and irreversible: deleting
an auditable publication receipt or decision would lose history.

No collection, index, database, vector store, embedding, or backup target is
added by publication receipts. Full-database backup and restore procedures include
content_items and audit_logs.

## Adding an external adapter later

An external adapter must implement Publisher in app/content/publishers/base.py:
publish, validate_connection, and get_publication_status. It must be explicitly
registered, use a scoped credential source, redact errors, honor the supplied
idempotency key, and remain disabled unless configured. A future GitHub adapter
must use a repository-scoped, least-privilege token; organization-wide or
unrestricted repository access is not acceptable.

## Deliberate exclusions

The release contains no GitHub, LinkedIn, or Medium network adapter, no credential
settings, no direct external publishing by default, no background publication,
no automatic approval, and no external analytics collection. Explicit manual
metric observations are documented in [content-metrics.md](content-metrics.md).

