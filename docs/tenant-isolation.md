V6 Phase 2 adds the scoped runtime at migration **37**, following the adopted schema at revision `4dc1d6b` (migration 36). It adds no human dashboard, membership system, self-registration, owner claim, or tenant restore API.

Every domain collection is tenant-owned. The authoritative catalog is `app/db/tenant_schema.py:TENANT_COLLECTIONS`; the reference/identity catalog is `app/db/tenant_inventory.py`. All 37 collections pass through `TenantCollection`, including auxiliary skill chunks and content versions. Installation exceptions are the schema ledger, mongot health probe, organization registry, tenant control, preservation manifests, and quarantine. Backups, restored-check collections, canonical Git files, embedding model files, and raw shared process metrics belong to the installation operator. Runtime skill imports, memory (including `global`), embeddings attached to domain records, jobs, and replay results belong to one organization.

| Path | Trusted context and policy gate |
| --- | --- |
| Ordinary V1–V5 APIs, including `agent:manage` | Verified bearer → globally unique public key lookup → enabled organization → principal → action/resource policy. Organization selectors never establish authority. |
| Services and repositories | Public async service guard; immutable organization on all domain driver operations; same-org resource/reference validation on writes; independent ownership checks on reads and joins. Export/review methods also check their input ownership. |
| Counts, distinct, pagination, joins | Tenant/resource query predicate before selection; cursors are signed and bind organization, action, subject/grants, collection, and filters. Old unsigned cursors must be restarted. |
| Lexical/vector retrieval | All eleven production definitions index organization and candidate IDs. Filters are inside `$search.compound.filter` / `$vectorSearch.filter`, before limits. Complex research predicates and live content provenance compile to bounded authorized candidate IDs. Results are checked again. |
| Content provenance and history | Same-org reference closure; live project/private-client authority inherited through source links before candidate selection, counts, exports, or history reads. Cached project metadata is descriptive, not authority. |
| stdio and HTTP MCP | Fresh authenticated identity preflight per call; one organization per gateway runtime; HTTP sessions bind both organization and key. Existing tool names and API paths remain. |
| Ingestion | Existing synchronous provider/job path. Server-issued initiating authority is checked before work, persistence steps, replay, and status/result access. Revoked/expired keys, disabled clients, inaccessible sources, and foreign organizations are refused. There is no new queue. Legacy jobs with no established initiator cannot be downloaded/replayed through bearer traffic. |
| Quotas and replay cache | Organization + client (or verified legacy key for quota purposes); replay additionally binds effective initiating grants. A changed grant refuses a cached response. |
| Temporary checkouts and downloads | Private temporary directories include the validated organization; provider cleanup remains scoped to that directory. Export reads the authorized tenant item before formatting. No tenant database download/restore route exists. |
| Provisioning/import/admin CLI | Explicit `contextctl --org-id …`; direct database credentials are the installation operator boundary. Tenant bearer callers cannot activate organizations or operate whole-database backups. |
| `/metrics` | Separate installation-operator token file, compared in constant time. Tenant `system:read` grants no access to raw shared metrics. |

Legacy identities remain only on adopted records. Lookups use tenant natural keys so updates retain adopted ObjectIds, revisions, actors, and identity version. New identities include the organization. Migrated empty project/profile lists become explicit `all_in_org` using the adoption manifest; `none` is distinct. An administrative action narrowed to resources cannot mint broader `all_in_org` authority. No key grants all organizations.

Content/reference candidate compilation is bounded at 10,000 records/IDs and refuses larger envelopes. This is a safety bound, not a performance result. Large installations must measure representative workloads before raising it. MongoDB documents the candidate-filter semantics in [compound filter](https://www.mongodb.com/docs/search/query/operators-collectors/compound/) and [Vector Search filtering](https://www.mongodb.com/docs/vector-search/query/aggregation-stages/vector-search-stage/). This phase adds no third-party dependency.

Run the repeatable disposable gate with Docker and the existing development virtual environment:

```bash
scripts/test-v6-isolation.sh
```

The runner uses the repository's pinned MongoDB/mongot images, generated temporary credentials, tmpfs database storage, an isolated network, and a loopback-only random port. It never reads the deployment `.env`. It runs the full Python unit suite, then the real three-module selector, including actual stdio and Streamable HTTP MCP. Both organizations reuse logical identifiers and carry distinct private sentinel content. Reports include a fingerprint of the public runtime/test sources; credentials and `.private` planning are excluded. Only the runner's own containers/network are removed. Set `V6_REPORT_DIR` to retain reports at a chosen location.

A deployment remains sealed until an explicit operator cutover. Complete Phase 1 adoption first. Stop all existing writers and MCP/API workers, fence/rotate their database credentials, and deploy only schema-37 binaries. The worker version declaration is an operator attestation; it does not discover running processes. Older binaries fail the migration/version gate, and the Phase 1 middleware continues to seal its domain routes. Database credentials themselves remain trusted installation authority.

```bash
contextctl --org-id ADOPTED_ORG organizations prepare-isolation \
  --writers-stopped --worker-schema-version 37

# Enable the scoped runtime for subsequent startup/operator operations:
# V6_TENANT_FOUNDATION=true
# V6_TENANT_ISOLATION=true
# API_AUTH_ENABLED=true

contextctl --org-id ADOPTED_ORG organizations activate-isolation \
  --writers-stopped --worker-schema-version 37 \
  --evidence /path/to/unit.xml --evidence /path/to/real.xml
```

Activation refuses failed/skipped/stale-source evidence, mixed worker declarations, unresolved quarantine, unclassified collections/owners, invalid records, and unresolved/foreign references. It activates only the explicitly named organization; additional organizations stay sealed until separately activated. Reports are local operator evidence and must come from the actual disposable run, not fabricated XML. There is no HTTP activation endpoint.

For metrics, mount a separate operator credential file with at least 32 random characters, set `OPERATOR_METRICS_TOKEN_FILE` to its container path, and have the installation scraper present it as a bearer token. An unset path disables access. Keep this file outside the repository and separate from all tenant API keys.
