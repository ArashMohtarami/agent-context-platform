# Observability

What the platform says about itself while it runs, and what it will never say.

Three things, answering three questions. **Logs** answer *what happened* — one
structured JSON line per request, per search, per tool call. **Metrics** answer
*how is it going* — counters and latency distributions at `/metrics`, in the
Prometheus text format. **Readiness** answers *can it work right now* — every
dependency probed and reported at `/health/ready`.

All three are self-hosted by construction. Nothing is pushed anywhere, no
collector is required, and no telemetry library is installed. Metrics are a
dictionary of numbers in the process that recorded them, and logs are lines on
standard output. A deployment that wants to collect either already has what it
needs.

## What is never recorded

The rule is the same for both, and it is enforced rather than remembered.

| Never | Where the rule lives |
| --- | --- |
| API keys, key secrets, stored hashes | the log field allow-list |
| Passwords and the MongoDB connection password | never in argv, never in a field |
| Bearer tokens and the `Authorization` header | the log field allow-list |
| What anybody searched for | the query is not among the retrieval log fields |
| What a search returned | results are counted, never quoted |
| The content of a memory or a context entry | no code path attaches one |
| A MongoDB command document or its error reply | the listener reads two fields |

**Logs.** `app/core/logging.py` serializes an explicit allow-list of fields and
nothing else. A handler that attaches an API key to a log record simply cannot
have it written out, whatever it called the attribute — the formatter never
looks. Adding a field to that list is the only way one reaches a log line,
which is what makes the rule checkable in review and in a test.

**Metrics.** Every value is a number and every label comes from a bounded
vocabulary this codebase writes: a route template, a retrieval method, a
collection, a tool name, an outcome. There is no project identifier, no query
identifier, and no document identifier in any series. Metrics are kept longer
than logs and read by more people, so a series may say how much and how long,
never about whom or about what.

The one field a caller influences is the request method, and an unrecognized
one is recorded as `other` rather than becoming a series named after whatever
was sent. A request matching no route is recorded as `unmatched` for the same
reason; the application's own built-in routes — the OpenAPI schema and the docs
pages — are named by the endpoint they matched, so they are not mistaken for
requests that matched nothing.

## Logs

Structured JSON, one object per line, on standard output. The MCP server writes
to standard error instead, because its standard output carries JSON-RPC and a
log line there would corrupt the conversation rather than describe it.

```json
{"timestamp":"2026-09-19T11:02:41.318+00:00","level":"INFO","logger":"agent_context.http",
 "message":"http_request_completed","request_id":"3f1c…","operation":"search_knowledge",
 "project_id":"payment-service","query_id":"9ab2…","result_count":6,
 "http_method":"POST","http_path":"/api/v1/search","http_route":"/api/v1/search",
 "status_code":200,"duration_ms":214.83}
```

### Correlation

| Field | What it says |
| --- | --- |
| `request_id` | which call this line belongs to |
| `operation` | what ran — the endpoint's name, or the MCP tool's |
| `project_id` | which project it concerned, when it concerned one |
| `query_id` | which search, matching the `trace.query_id` in the response |
| `result_count` | how much came back |
| `duration_ms` | how long the whole call took |
| `latency_ms` | how long the retrieval inside it took |

`request_id` is created at the edge and carried in a context variable, so every
line a call produces has it without any function being handed one.

**It crosses the process boundary.** The MCP server mints an identifier for
each tool call and sends it to the API as `X-Request-ID`; the API adopts it and
answers with it. One tool call and the API request it became therefore share an
identifier in two processes' logs:

```
mcp   {"message":"mcp_tool_completed","request_id":"7f3a…","mcp_tool":"search_knowledge","outcome":"ok","duration_ms":231.4}
api   {"message":"http_request_completed","request_id":"7f3a…","operation":"search_knowledge","result_count":6,"duration_ms":214.8}
```

A supplied identifier is adopted only if it is at most 64 characters of
`[A-Za-z0-9_.:-]`; anything else is replaced with a fresh one. The identifier
is repeated on every line of the call, so nothing that could be mistaken for
structure by whatever later reads those lines is carried.

Any client may send `X-Request-ID` and get the same behaviour.

### The events worth knowing

| Event | Logger | When |
| --- | --- | --- |
| `http_request_completed` | `agent_context.http` | one per answered request |
| `http_request_failed` | `agent_context.http` | a request that raised |
| `unhandled_request_error` | `agent_context.http` | with the stack, never sent to the caller |
| `retrieval_completed` | `agent_context.retrieval` | one per search, without the query |
| `mcp_tool_completed` | `agent_context.mcp` | one per tool call, with its outcome |
| `mcp_tool_failed` | `agent_context.mcp` | a defect in the MCP server, with the stack |
| `authorization_denied` | `agent_context.security` | a refusal, with `key_id` and the reason |
| `mongodb_command_failed` | `agent_context.mongodb` | a failed command, without its reply |
| `skill_import_completed` | `agent_context.skills` | one per `contextctl skills import` |

`LOG_LEVEL` sets the threshold; `INFO` is the default and is what produces the
access line.

### Reading them

```bash
docker compose logs -f context-api | jq -c 'select(.message == "http_request_completed")'
docker compose logs context-api | jq -c 'select(.request_id == "7f3a…")'      # one call
docker compose logs context-api | jq -c 'select(.duration_ms > 1000)'          # the slow ones
docker compose logs context-api | jq -c 'select(.level == "WARNING" or .level == "ERROR")'
```

## Metrics

`GET /metrics` renders process-local counters and last-observation latency
summaries in Prometheus format. It is installation-operator-only: mount a
separate random credential and set `OPERATOR_METRICS_TOKEN_FILE`. Tenant keys,
including `system:read`, do not authorize raw shared metrics. An unset token path
disables access. Configure the private installation scraper with that credential;
do not embed it in a dashboard, example URL or browser bundle.

These values are not historical organization chart series. The
[monitoring console](dashboard-monitoring.md) uses separate authorized events and
declared coverage, retention and population. Never infer employee presence,
productivity or model cost from last observations.

A scrape runs **no dependency checks**. It walks a dictionary and returns; the
readiness gauges are whatever `/health/ready` last wrote. Monitoring must not be
on the critical path of the thing it monitors.

`METRICS_ENABLED=false` removes the endpoint entirely — 404, not an empty body,
so a scraper pointed at it fails visibly instead of recording that all is quiet
— and stops the driver publishing a timing for every MongoDB command.

### The catalogue

| Metric | Type | Labels |
| --- | --- | --- |
| `acp_http_requests_total` | counter | `method`, `route`, `status` |
| `acp_http_request_duration_seconds` | summary | `route` |
| `acp_mongodb_command_duration_seconds` | summary | `command` |
| `acp_mongodb_command_failures_total` | counter | `command` |
| `acp_embedding_duration_seconds` | summary | `operation` |
| `acp_embedding_failures_total` | counter | `operation` |
| `acp_search_duration_seconds` | summary | `method`, `collection` |
| `acp_search_failures_total` | counter | `method`, `collection` |
| `acp_retrieval_duration_seconds` | summary | `operation` |
| `acp_retrieval_results` | summary | `operation` |
| `acp_mcp_calls_total` | counter | `tool`, `outcome` |
| `acp_mcp_call_duration_seconds` | summary | `tool` |
| `acp_memory_operations_total` | counter | `operation`, `outcome` |
| `acp_skill_imports_total` | counter | `outcome` |
| `acp_dependency_ready` | gauge | `dependency` |
| `acp_readiness_check_duration_seconds` | summary | — |
| `acp_uptime_seconds` | gauge | — |
| `acp_build_info` | gauge | `version` |

Label vocabularies: `method` is `vector` or `lexical`; `collection` is
`skill_chunks`, `project_context`, or `memories`; `operation` on embedding is
`query` or `documents`; `outcome` on MCP is `ok`, `refused`, `unavailable`, or
`failed`; `outcome` on memory is `ok`, `unchanged`, or `failed`; `outcome` on a
skill import is `new`, `updated`, `unchanged`, or `failed`.

### Percentiles

Every summary reports p50, p95, and p99:

```
acp_http_request_duration_seconds{route="/api/v1/search",quantile="0.5"} 0.184
acp_http_request_duration_seconds{route="/api/v1/search",quantile="0.95"} 0.501
acp_http_request_duration_seconds{route="/api/v1/search",quantile="0.99"} 0.912
acp_http_request_duration_seconds_count{route="/api/v1/search"} 1043
acp_http_request_duration_seconds_sum{route="/api/v1/search"} 217.4
```

The quantiles are computed over the **last 1024 observations** of that series;
`_count` and `_sum` are lifetime figures. That split is deliberate and is what
the format's summaries have always meant: a p99 should describe now, while a
rate should cover the whole process. The value reported is one that was
actually measured — nearest rank, not interpolation — so a p99 names a request
that really did take that long.

### Why vector and lexical are counted apart

They fail apart. A rebuilding vector index takes semantic search down while
lexical search keeps answering, and a single "search failures" number would go
red for that half-outage without saying which half. `acp_search_failures_total`
splits by method and by collection, so the failing index is the one the number
names.

### A missing index is not a failed query

An empty query result does not prove that a search index is ready. Search
failures and readiness are separate signals, so alert on both. A dropped or
rebuilding vector index is detected by
`acp_dependency_ready{dependency="vector_search"}` going to 0, because readiness
asks whether each index is queryable rather than inferring it from whether
queries failed.

The failure counter is for the other case: a query that could not be served at
all — the coordinator down, the pipeline refused, a score that came back
unusable. Both signals exist because they catch different outages, and an
alert on only one of them would miss the other silently.

### Cardinality

A registry cannot grow without bound. Metrics are declared up front, an
undeclared name or an unexpected label is a programming error rather than a new
series, each metric keeps at most 128 label combinations, and each summary
keeps at most 1024 recent observations. Nothing a caller sends can add a
series, so the scrape endpoint can never become the way a process runs out of
memory.

### The processes that have no port

`contextctl` and the local stdio MCP bridge do not expose scrape endpoints.
They record into the
same registry and also emit one structured event per operation —
`mcp_tool_completed`, `skill_import_completed` — carrying the same figures.
Whatever collects container logs therefore has those numbers, without either
process opening an additional port. The remote HTTP MCP gateway runs inside
the API process and uses its normal metrics endpoint.

### Scraping

```yaml
scrape_configs:
  - job_name: agent-context-platform
    scrape_interval: 15s
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/agent-context-key
    static_configs:
      - targets: ["127.0.0.1:8000"]
```

Run the scraper on the same host or the same private network. The API port is
published to loopback only by default, which is where it should stay.

## Readiness

`GET /health/live` says the process is answering. `GET /health/ready` says
whether it can actually work, and answers **503** when it cannot — checking
MongoDB, the replica set, the search coordinator, the embedding model, and all
six search indexes. It never reports healthy merely because the HTTP process is
alive.

```bash
curl -s http://127.0.0.1:8000/health/ready | jq '{status, api, mongot, embedding: .embedding.ready}'
contextctl health                       # the same answer, exit 3 when not ready
```

```json
{
  "status": "ready",
  "api": { "ready": true, "version": "0.1.0", "uptime_seconds": 4172.8 },
  "authentication": { "enabled": true, "audit": true },
  "mongodb": true, "database": true, "replica_set": true, "mongot": true,
  "embedding": { "enabled": true, "available": true, "model": "…", "dimensions": 384, "ready": true },
  "vector_search": { "…": "…", "ready": true },
  "lexical_search": { "…": "…", "ready": true },
  "context_search": { "…": "…", "ready": true },
  "memory_search": { "…": "…", "ready": true }
}
```

The `api` block names the build that answered and how long it has been up,
which turns "it started failing after the deploy" from a guess into a reading.

Each check also writes `acp_dependency_ready{dependency=…}` as 1 or 0, so an
alert can fire on a failing dependency without anything having to parse this
body. The container health check calls this endpoint every ten seconds, which
is what keeps those gauges as fresh as readiness itself.

| `dependency` | Ready when |
| --- | --- |
| `api` | the process answered |
| `mongodb` | the application database accepts commands |
| `replica_set` | this member is primary of the expected set |
| `mongot` | the coordinator path answers |
| `embedding` | the model is loaded, or embeddings are disabled |
| `vector_search` | the skill vector index is queryable at the right dimensions |
| `lexical_search` | the skill Search index is queryable |
| `context_search` | both project-context indexes are queryable |
| `memory_search` | both memory indexes are queryable |
| `platform` | every one of the above |

## Alerts worth having

```promql
acp_dependency_ready{dependency="platform"} == 0
rate(acp_search_failures_total[5m]) > 0
rate(acp_embedding_failures_total[5m]) > 0
acp_http_request_duration_seconds{route="/api/v1/search",quantile="0.95"} > 1
rate(acp_mcp_calls_total{outcome="unavailable"}[5m]) > 0
rate(acp_http_requests_total{status=~"5.."}[5m]) > 0
```

These are example alert rules. Set latency thresholds from representative
measurements and the service objectives of your own deployment.

## OpenTelemetry

OpenTelemetry is not included. Structured logs and `/metrics` provide the
shipped observability interface. A deployment that needs tracing can add a
compatible exporter and collector at the existing instrumentation boundary.

The extension point exists if a deployment wants it. Every call already has one
identifier that crosses the process boundary in a header, every operation is
already timed in one place, and `app/observability/` has no dependency on the
rest of the application — an exporter would be an additional consumer of the
same recordings, not a rewrite of them.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `LOG_LEVEL` | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, `DEBUG` | `INFO` |
| `METRICS_ENABLED` | publish `/metrics` at all | `true` |
| `RETRIEVAL_LOG_ENABLED` | additionally **store** each search, query text included | `false` |
| `AUDIT_LOG_ENABLED` | store every authorization decision | `true` |

`RETRIEVAL_LOG_ENABLED` is the one setting that changes what is retained rather
than what is measured. The structured retrieval event never carries the query;
enabling this stores one `retrieval_logs` document per search **with** the query
text, which is a deliberate retention decision — it is the one field that can
describe who asked what. See [retrieval.md](retrieval.md).

## Verification

```bash
python -m pytest tests/unit/test_metrics.py tests/unit/test_instruments.py \
  tests/unit/test_logging.py tests/unit/test_api_metrics.py \
  tests/unit/test_observability_context.py tests/unit/test_observability_mongo.py \
  tests/unit/test_search_observability.py tests/unit/test_knowledge_observability.py \
  tests/unit/test_mcp_observability.py
docker compose --profile test run --rm --build test
```

The tests assert the guarantees this page makes, not only the plumbing: that a
record carrying a credential cannot have it serialized, that no metric declares
a label naming a project or a query, that an unknown tool name and an unmatched
route cannot create series, and that a vector failure and a lexical failure are
countable apart.
