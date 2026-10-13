---
id: django-rest-framework
name: Django REST Framework Contracts
version: 1.0.0
description: Build Django REST Framework APIs with explicit serializers, authorized querysets, object permissions,
  bounded pagination, schema contracts, and error behavior. Use for DRF views and viewsets.
category: django
trust_level: trusted
tags:
- django
- drf
- api
- authorization
- serializers
---

# Django REST Framework Contracts

## Trace the request contract

Read the installed DRF version, router, authentication classes, permission classes, serializers,
querysets and exception handler. Choose a generic view/viewset only where its behavior matches the
resource. Define allowed actions, status codes, writable fields, pagination, filters, ordering,
and stable errors before implementing. Keep transport validation separate from multi-row business
operations. [APIView lifecycle](https://www.django-rest-framework.org/api-guide/views/).

## Authorize every path

Scope `get_queryset()` to the authenticated owner/tenant before lookup and list filtering.
Object permission checks do not automatically filter a list, and creation requires its own
ownership decision. Custom object lookup must call `check_object_permissions()` when appropriate.
Choose authentication and CSRF rules for the actual client: session cookies and bearer credentials
have different threat models. CORS is not authorization.
[Permission boundaries](https://www.django-rest-framework.org/api-guide/permissions/).

Never accept ownership, staff flags, approval state or totals merely because the client submits
them. Derive those values in the server/service. Test list, retrieve, update, destroy, custom actions,
bulk endpoints and nested resources with a second user. An identifier that exists elsewhere should
not leak private fields through a validation message.

## Serializer and performance decisions

Use explicit `fields`, read-only computed fields and bounded input lengths. Distinguish omitted
fields from explicit null for PATCH. Validate cross-field rules with clear field/non-field errors.
Persist through a service when one request changes multiple domain records. A serializer instance
is not a durable command record or a concurrency guard.
[Serializer behavior](https://www.django-rest-framework.org/api-guide/serializers/).

Check serializer relation access against query counts. Preload the exact relations needed, avoid
an N+1 in `SerializerMethodField`, and retain authorized query predicates while optimizing. Restrict
filter/order fields deliberately, and cap page size. Generate schema from the chosen tooling and
verify examples against the actual response serializer.

## Retries, async and evidence

Define what a repeated POST means. If clients can retry an uncertain write, use a domain-appropriate
unique key and compare payload identity. Optimistic concurrency needs a checked version/ETag;
a last-write-wins serializer save does not detect lost updates. Specify 409/412 behavior explicitly.

Do not assume `async def` is supported merely because the underlying Django view system supports it.
Inspect DRF's installed dispatch/authentication stack and the chosen extension before an async
migration; otherwise retain synchronous DRF and use the async Django skill for native Django views.
Verify invalid inputs, denied resources, pagination, error envelopes, retries and query-count growth.
Return tested behavior and documented compatibility limits, not an unverified "async API" claim.
