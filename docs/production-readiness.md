# Production readiness

Validate your own deployment and dataset before relying on the service. The
shipped Compose configuration is a small self-hosted topology: one API process,
one MongoDB replica-set member, and one `mongot` process. It has persistent storage
and backup tooling, with no automatic host failover or published availability SLA.

## Resources and capacity

MongoDB, search indexes, and the local embedding model all consume memory. Compose
applies per-service memory, CPU, and process limits. Size the host above the combined
limits plus operating-system overhead; increase limits based on measurements from
your corpus, ingestion workload, and concurrency. A small test dataset is not a
capacity guarantee for a larger deployment.

The API loads one embedding model per process. Increasing worker count multiplies
that cost and changes the gateway's process-local control behavior. Keep the
default single-process gateway unless you have evaluated shared rate/concurrency
controls and model memory. Search memory and disk usage grow with indexed content.
Monitor free disk and index build status as well as request latency.

Use the benchmark harness against a representative isolated dataset:

```bash
.venv/bin/python scripts/benchmark.py --base-url http://127.0.0.1:8000 \
  --token-file ./secrets/benchmark-api-key --project-id payment-service --check
```

Issue only the scopes needed by the harness. Record the commit, hardware, dependency
versions, corpus size, index readiness, concurrency, and cache state with results.
The harness's targets are configurable deployment checks, not product guarantees.

## Deployment controls

- Keep `API_AUTH_ENABLED=true`; issue independent credentials with explicit scope
  and project/profile grants. Separate coding, review, promotion, and publication
  responsibilities where your workflow requires it.
- Use HTTPS for remote MCP/API clients and configure trusted reverse-proxy headers
  deliberately. Database, bootstrap, and search ports remain private.
- Provision unique secrets through your deployment's secret-management process.
  Keep the source checkout, secrets, backups, and private notes outside any public
  web root. Deploy the introduction website independently.
- Run the application containers as their configured non-root user with read-only
  filesystems and resource limits. Upstream MongoDB entrypoints retain capabilities
  needed to initialize storage and drop privileges.
- Apply outbound network restrictions when enabling research ingestion from
  untrusted submitters. DNS preflight checks are not connection pinning; see
  [research.md](research.md) for the remaining egress boundary.

See [deployment.md](deployment.md) and [security.md](security.md) for the settings
and authorization model.

## Readiness and failure behavior

`/health/live` reports the process. `/health/ready` checks the authenticated
MongoDB path, primary status, the search coordinator, the configured embedding
model, and core knowledge search indexes. It does not independently prove every
creator-content or research index can answer a query. Verify those workflows with
representative authorized requests before putting them into service.

Monitor readiness alongside error rate, request latency, index status, resource
usage, disk space, and restore-test results. Database operations and readiness
probes have bounded timeouts; dependency failures should produce safe service
errors. An embedding outage can leave lexical retrieval available, but model
requirements still affect readiness according to configuration.

In an isolated staging deployment, exercise database/search/model unavailability,
credential expiration and revocation, denied project access, stale revisions,
retry idempotency, and rate limits. Confirm recovery rather than assuming a
container restart restored the complete workflow.

## Backup and recovery

MongoDB holds durable records, vectors, identity/grants, provenance, and history.
Search indexes are derived and must be rebuilt after a restore. Canonical skill
sources remain in Git. Store backups away from the service host, restrict access,
and test restoration on an isolated deployment.

The [backup guide](backup.md) documents retention, integrity checks, restore
verification, and recovery. Schedule both backups and actual restore verification.
A single-member replica set does not protect against losing its only host.

## Release verification

Run the unit/static checks and the relevant live integration checks described in
[CONTRIBUTING.md](../CONTRIBUTING.md). Use a dedicated fixture database; integration
tests must not mutate your real knowledge store. Rebuild and inspect the public
website/guide. To audit the public source tree, run:

```bash
python3 scripts/prepare_open_source_release.py --check
```

Dependency locks pin versions rather than wheel hashes. Container image digests
pin their selected upstream images. Review security updates and rerun build,
integration, and license-notice checks when changing those pins.
