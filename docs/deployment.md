# Deployment

## Authentication

Knowledge APIs and MCP require a scoped credential by default; health checks
are public. Issue the first operator key through the database-backed CLI:

```bash
docker compose exec -T context-api python -m app.cli.contextctl --json keys create \
  --name codex --scope search:read --scope skills:read | jq -r .key
```

Inside the platform, because `contextctl keys` reaches MongoDB directly and MongoDB is on the
`internal` network with no host port. `python -m app.cli.contextctl` because the image installs
the application's dependencies but not the project, so there is no `contextctl` console script in
it.

The operator key is shown once; the platform stores only a hash. Subsequent
client credentials can be issued through authenticated gateway administration.
 `API_AUTH_ENABLED=false` turns authentication
off for a single-user deployment, which logs a warning at startup and is reported by
`/health/ready`. An application whose authentication decision was never made fails closed with a
500 rather than serving. Health endpoints stay open because Docker's healthcheck has no credential
and readiness carries no knowledge.

`AUDIT_LOG_ENABLED` (default `true`) records who was allowed or refused what, and who wrote
something — never what was read or written. `API_MAX_REQUEST_BYTES` (default 1 MiB) bounds a
request body, refused from `Content-Length` before anything is read.

The containers built here run as a non-root user with no capabilities and a read-only root
filesystem; the official MongoDB images keep their capabilities because their entrypoints need
`CHOWN`, `SETUID`, and `SETGID` to start. Every service runs with `no-new-privileges`.

See [security.md](security.md) for scopes, project authorization, what a refusal says, what reaches
the logs, and how to check a deployment.

## Secrets

Development may use `scripts/generate-dev-secrets.sh`. Production deployment must provision
unique root/application/synchronization passwords and a MongoDB keyfile through its secret
manager, then point the corresponding `*_SECRET` Compose variables at those files. The `mongot`
synchronization password file must be non-empty and must not end in a newline. Do not commit
`.env`, secret files, or generated credentials, and do not pass administrative or synchronization
credentials to `context-api`. For local Compose, the generator protects the containing
`secrets/` directory with mode `0700` and makes its bind-mounted files container-readable; every
container mount remains read-only.

Changing the root username after the data volume has been initialized does not rename the
existing root user. Credential changes should be performed as an explicit administrative
rotation. The idempotent bootstrap job updates the application and synchronization users'
passwords and roles to match the supplied secrets and least-privilege definitions.

## Network and persistence

MongoDB, `mongot`, and bootstrap are attached only to the Docker network `agent_context`, which
is marked `internal`. MongoDB and `mongot` declare their container ports for service discovery
but publish no host ports. The API is dual-homed so it can reach MongoDB privately while
publishing port 8000 to `127.0.0.1` by default. Put a separately managed TLS reverse proxy in
front of the API if it must cross a host boundary.

Database and Community Search files live in the named volumes `agent_context_mongodb_data` and
`agent_context_mongot_data`. `docker compose down` preserves them. Removing either volume is
destructive and is intentionally not part of any Make target.

Local embedding model files live in `agent_context_embedding_models`, mounted at
`/var/cache/embeddings` and owned by the non-root container user. The model is downloaded once
into that volume; removing it only forces another download. On an air-gapped host, populate the
volume out of band and set `EMBEDDING_ALLOW_DOWNLOADS=false`. Measure both disk
and resident memory for the configured model, its dependencies, and corpus,
in addition to MongoDB and search. Each API worker loads its own model instance.

## Startup and readiness

The startup sequence is:

1. The official MongoDB 8.3.9 image creates the root user on an empty data volume.
2. `mongodb-init` authenticates as root, initializes `rs0` when necessary, waits for primary
   election, creates the monitoring privilege, and creates/updates the application and dedicated
   `mongot` synchronization users.
3. MongoDB's container health check requires authenticated primary status.
4. `mongot` starts only after bootstrap succeeds and MongoDB is healthy. Its health endpoint
   becomes serving after replication/configuration initialization.
5. FastAPI starts only after `mongot` is healthy. Its lifespan creates a timezone-aware PyMongo
   Async client, validates the database, replica set, and indirect coordinator path, applies
   pending database migrations, and idempotently reconciles standard indexes.
6. The lifespan reconciles every Search index, then loads the configured local embedding model
   once and reconciles every vector index for the dimensions that model reports. An unusable
   model or an unreachable search coordinator is logged and reported by readiness rather than
   crashing the process, so the container is never reported healthy while it cannot embed or
   retrieve.
7. Startup fails if database initialization or required index creation fails. The MongoDB client
   and the embedding model are released during shutdown.

`GET /health/live` reports process liveness. `GET /health/ready` returns HTTP 503 unless the
application database is reachable, the configured replica set is primary, a non-mutating
`$listSearchIndexes` coordinator request succeeds through `mongod`, the configured embedding model
is loaded, and all six search indexes — two each over skill chunks, project context, and
memories — are queryable, with each vector index matching the model's dimensions. The response
reports the model, its dimensions, and its device, plus each index's name, status, and
queryability. A newly
created index reports `BUILDING` until `mongot` finishes it. Setting `EMBEDDING_ENABLED=false`
removes the embedding and vector-search requirements from readiness; the Search indexes stay
required because lexical retrieval does not depend on the model. The application never opens a
direct connection to `mongot`.

The readiness body also carries an `api` block naming the running build and its uptime, and each
check publishes `acp_dependency_ready{dependency=…}` as 1 or 0 so an alert can fire without
anything parsing JSON. The container health check calls this endpoint every ten seconds, which
is what keeps those gauges as fresh as readiness itself.

Application and Uvicorn logs are emitted as one JSON object per line on stdout. Set `LOG_LEVEL`
to `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG` (case-insensitive). HTTP access events
carry a `request_id`, the method, the path, the matched route template, the response status, the
duration, and whatever the call turned out to be about — the operation, the project, the query
identifier, and the result count. The request ID is returned in the `X-Request-ID` response header,
and an acceptable one supplied by a caller in that header is adopted rather than replaced, which is
how an MCP tool call and the API request it became share an identifier across two processes' logs.
Query strings are not logged. Embedding events log only the model name, dimensions, device,
duration, and error type.

`METRICS_ENABLED` (default `true`) publishes `GET /metrics` in the Prometheus text exposition
format to a caller holding `system:read`: request, MongoDB, embedding, retrieval, and per-index
latency at p50/p95/p99, plus counters for API requests, MCP calls, memory writes, skill imports,
and failures. Nothing is pushed anywhere, no exporter runs, and no second port is opened — a
self-hosted scraper reads the API's own port. A scrape performs no dependency checks, so
monitoring never lands on the critical path of what it monitors. Setting it to `false` removes the
endpoint rather than serving an empty one, and stops the driver publishing a timing per MongoDB
command. See [observability.md](observability.md) for the full catalogue and the scrape
configuration.

Hybrid ranking is tuned through `RETRIEVAL_WEIGHT_*`, `RETRIEVAL_RECENCY_HALF_LIFE_DAYS`,
`RETRIEVAL_DUPLICATE_OVERLAP`, `RETRIEVAL_TOKEN_BUDGET`, and `RETRIEVAL_MAX_TOKEN_BUDGET`. Each is
validated at startup, so an incoherent weighting or a default budget above its maximum stops the
process from starting rather than surfacing on the first query. Changing them changes ranking
only; no reindexing or re-embedding is required. Research packets use their own
`RESEARCH_RETRIEVAL_TOKEN_BUDGET` (default 8000) and
`RESEARCH_RETRIEVAL_MAX_TOKEN_BUDGET` (default 16000) so V3 research retrieval cannot retune
V1 engineering or V2 content budgets.

Every search reports what it did to the structured log as `retrieval_completed`, carrying a
generated `query_id`, the project, the retrieval method, the requested sources, the result count,
and the latency. The query text is deliberately excluded, so shipping these logs never ships what
somebody asked. `RETRIEVAL_LOG_ENABLED` (default `false`) additionally stores one `retrieval_logs`
document per search, query text included; that is a retention decision, which is why it is opt-in
rather than a default. Retrieved content is never logged or stored either way — a stored record
references results by key.

The MCP server runs as its own process and is started by the client that talks to it:
`docker compose --profile mcp run --rm -T mcp`, or `python -m app.run_mcp` locally. It speaks
its protocol over stdin and stdout, so it publishes no port, and it writes its logs to stderr
because a log line on stdout would corrupt the protocol stream. It reaches the platform through
the Context API on the private network and is configured with no database credentials at all —
the component an agent talks to directly is the one that can do the least. `MCP_SCOPES`,
`MCP_PROJECT_IDS`, and `MCP_PROFILE_IDS` narrow what one server may do, which V1 projects it may
reach, and which V2 creator profiles it may reach. Leaving a local allowlist
empty removes that additional restriction; it never overrides the API credential
or linked client's grants. When the API
requires a key, point `MCP_API_TOKEN_FILE` at a mounted secret holding one — the server presents it
as a bearer token and the API's own refusals reach the agent as tool errors that name a missing
scope but never a refused project or profile. See [mcp.md](mcp.md).

See [search.md](search.md) for the unified `POST /api/v1/search` interface, its retrieval
metadata, and its logging hooks. See [mongot.md](mongot.md) for search-service operating requirements
and [database.md](database.md) for persistence details. Canonical skill deployment and
import operations are described in [skills.md](skills.md), the local embedding model in
[embeddings.md](embeddings.md), vector, lexical, and hybrid retrieval in
[retrieval.md](retrieval.md), project boundaries and their
durable context in [projects.md](projects.md), and intentional memory in [memory.md](memory.md).
Codex configuration, the startup order it depends on, and its troubleshooting are documented in
[codex.md](codex.md), and the `contextctl` operator commands — importing skills, rebuilding
search indexes, issuing keys, backup, and restore — in [cli.md](cli.md). API-key authentication,
authorization, and container hardening are in [security.md](security.md); the backup strategy,
retention, verification, and the disaster-recovery procedure are in [backup.md](backup.md).
Backups cover MongoDB alone: search indexes are derived state that `contextctl skills reindex`
rebuilds, so a restore is followed by a reindex and a readiness check rather than by restoring an
index. The vectors are inside the documents and travel with the backup;
re-embedding is needed only when the model or its stored-vector compatibility changes.
