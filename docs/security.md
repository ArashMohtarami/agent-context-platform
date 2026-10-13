# Security

Written for whoever runs this platform and has to decide what it may be trusted with.

The platform stores a team's architecture decisions, integration details, and accumulated
judgement, and hands them to an agent on request. Two things follow. Somebody who can read it
learns how the systems it describes are built, so reading has to be authorized. And an agent that
acts on what it reads can be steered by whoever wrote it, so retrieved content has to arrive as
data rather than as instructions.

## API keys

A key looks like `acp_<key_id>_<secret>`. The identifier is public and is what a lookup filters
on, so verification is one indexed read; the secret is 32 bytes of CSPRNG output.

**What is stored is a SHA-256 hash of the secret, never the secret.** A dump of the `api_keys`
collection is a list of what exists, not a set of working credentials.

SHA-256 rather than a password KDF, deliberately. A KDF exists to make guessing a *low-entropy*
human-chosen secret expensive. There is nothing to guess here — 256 random bits admit no
dictionary, rainbow table, or brute-force search at any iteration count. What a KDF would add is
tens of milliseconds to every authenticated request. The comparison is constant-time
(`hmac.compare_digest`), which is the property that actually matters.

### Issuing one

Bootstrap API keys are issued by operator commands with database access.
After bootstrap, authenticated client administration can issue scoped client
credentials through the gateway API or CLI. Client credentials cannot widen
their linked client's grants; keep administrative credentials separate from
normal agent credentials.

**Which means it runs inside the platform.** Under Compose, MongoDB is on an `internal` network
and publishes no host port, so these commands only work where the database is reachable — and the
image installs the application's dependencies but not the project, so the entry point is
`python -m app.cli.contextctl` rather than the `contextctl` console script:

```bash
acp() { docker compose exec -T context-api python -m app.cli.contextctl "$@"; }

acp keys scopes
acp keys create --name codex --scope search:read --scope skills:read
acp keys create --name payments-agent \
    --scope search:read --scope context:read --scope context:write \
    --project payment-service --expires-in-days 90
acp keys list
acp keys revoke --key-id 0123456789abcdef
```

Running one from the host instead fails with `this command needs the platform's database settings,
and they are not set` — the local ignored `.env` is written for Compose, which maps
`MONGODB_APP_USERNAME` to the `MONGODB_USERNAME` the application reads, so the file that looks
like it configures everything configures a different set of names.

`keys scopes` is the exception: it describes the platform's vocabulary, reads nothing, and answers
anywhere `contextctl` is installed.

The key is printed once. `--json` carries it too, so a deployment script can pipe it into a secret
store rather than a person copying it out of a terminal:

```bash
acp --json keys create --name ci --scope search:read | jq -r .key > ./secrets/ci-api-key
chmod 600 ./secrets/ci-api-key
```

A lost key cannot be recovered; issue a new one and revoke the old. Revocation keeps the record
rather than deleting it, so the audit trail still has a name to attach past decisions to.

### Scopes

| Scope | Grants |
| --- | --- |
| `skills:read` | Read skill metadata and retrieve skill content |
| `projects:read` | Read project boundaries |
| `projects:write` | Create a project boundary |
| `context:read` | Read and search one project's durable context |
| `context:write` | Store durable context in one project |
| `memory:read` | Read and search memory within shared or authenticated client ownership |
| `memory:write` | Store and correct memory |
| `search:read` | Search permitted core knowledge sources together |
| `creator:read` | Read creator profiles and content pillars |
| `creator:write` | Create and update creator profiles and content pillars |
| `content:read` | Read creator content ideas, briefs, and items |
| `content:write` | Create and update creator content and immutable versions |
| `research:read` | Read permitted research projects and evidence |
| `research:write` | Register sources, ingest evidence, and propose research claims/candidates |
| `research:approve` | Review and approve research candidates |
| `research:promote` | Promote approved candidates, also requiring target write grants |
| `content:publish` | Record publication of an approved content item |
| `agent:self` | Read the authenticated client's gateway metadata |
| `session:write` | Create and update that client's lightweight sessions |
| `audit:self:read` | Read that client's content-free activity history |
| `agent:manage` | Administer clients, credentials, bindings, and reusable profiles |
| `system:read` | Read platform diagnostics |

Defined once, in `app/security/scopes.py`, and read by both the API and the MCP server so the two
cannot disagree about what a grant means. Reading never implies writing. A test asserts that every
route outside `/health` declares a scope, so a route added without one fails the suite rather than
quietly being open.

`projects:write` is enforced by the API but reaches no MCP tool: an agent does not bring an
isolation boundary into existence.

### Projects

A key may be restricted to named projects. Naming none grants every project — the widest grant, so
it is the one an operator chooses by leaving `--project` out rather than the one they fall into.

The check follows the value, not the route: `project_id` arrives in a path, a query string, or a
body depending on the endpoint, and every one of them is checked. Addressing no project at all is
always allowed, because that reaches global knowledge, which belongs to every caller — it is never
read as "any project", which would infer an identity from an absence.

### Expiry and revocation

`--expires-in-days` sets an expiry; omitting it issues a key that does not expire. `created_at`,
`last_used_at`, and `status` are recorded. A usage timestamp is written without a revision guard
and without the request waiting on it: losing one to a concurrent write is better than failing an
authenticated request over an observation.

### Remote MCP clients

Claude Code and Gemini CLI each use an independent `AGENT_CONTEXT_TOKEN`, in the same hashed
`api_keys` collection as every other client. It is never an `ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, or
`GOOGLE_APPLICATION_CREDENTIALS`; it is never stored in client settings and is presented only in
the HTTP `Authorization` header. A dedicated client can be expired, rotated, disabled, or revoked
without interrupting Codex or another client. Revocation is checked on every `/mcp` request and
tool preflight; historical content and provenance remain intact.

Remote `/mcp` requires HTTPS. Plain HTTP is allowed only for a direct loopback peer in an explicit
development/test deployment. A TLS-terminating proxy must be explicitly allowlisted with
`API_PROXY_HEADERS=true` and `API_FORWARDED_ALLOW_IPS`; arbitrary forwarded headers are never
trusted. Request body and live MCP-session limits are bounded before work begins.

The Claude and Gemini default scope sets are intentionally non-administrative: they do not include
`agent:manage`, `research:approve`, `research:promote`, or `content:publish`. Agent identity,
scope, actor provenance, and session ownership are derived from the authenticated credential, not
from caller-provided headers or tool arguments. Retrieved knowledge is labelled source, trust, and
resource type and fenced as data rather than executable instructions. See
[Claude Code remote MCP](clients/claude-code.md) and
[Gemini CLI remote MCP](clients/gemini-cli.md) for current setup and rotation guidance.

## What a refusal says

Every way a key can fail — missing, malformed, unknown, revoked, expired, wrong secret — produces
the same answer:

```
401 {"detail": "the API key was not accepted"}
```

Distinguishing them would tell somebody probing which keys exist and which merely expired. The
real reason goes to the operator's log and the audit trail.

A missing scope **is** named (`this key is not authorized for context:write`), because that tells a
legitimate caller what to ask for. A refused project is **not** named, because naming it confirms
the project exists.

## Running without authentication

`API_AUTH_ENABLED=false` makes every endpoint answer without a credential. It is a deliberate
single-user choice, not a default: it logs `api_authentication_disabled` at startup and is reported
by `/health/ready` under `authentication.enabled`.

There is no other path around the check. An application whose authentication decision was never
made — a wiring mistake — **fails closed** with `500 authentication is not configured` rather than
being treated as open.

Health endpoints are always reachable without a credential. Docker's healthcheck has none, and
readiness carries no knowledge: only whether the parts of the platform are working.

## The audit trail

`audit_logs` records who asked, for what, and what was decided: the action, the outcome, the key
identifier and name, the project or creator profile, the scope, the method, the path, and a short reason.

**Never what came back.** A trail that copied retrieved knowledge would double what a leak exposes
while telling an investigator nothing the records themselves cannot. The path is stored without its
query string for the same reason — a search query is the one field that can describe the person who
ran it.

An audit write never changes the outcome it describes. A correctly refused request does not become
a 500 because the trail was unwritable, and a success is not undone by one; the failure is logged
for the operator instead.

`AUDIT_LOG_ENABLED=false` turns it off. Refusals still reach the operator's log.

## Secrets

- **Nothing in the source, the image, or Git.** The Dockerfile takes no build arguments and
  contains no password, secret, token, or key — a build argument survives in the image's layers.
- **Passwords arrive as mounted files**, not environment variables: a variable is readable through
  `/proc` by every process in the container and appears in `docker inspect`.
- **No command takes a secret in `argv`** — readable by every process on the host, and kept in
  shell history. `backup` and `restore` hand MongoDB's tools their password through a private file
  at mode `0600`, created before anything is written into it and removed afterwards.
- **The structured logger serializes an allow-list of fields.** A log record's other attributes are
  dropped rather than shipped. `key_id` and `auth_reason` are on that list — the public half of a
  credential and one word from a fixed vocabulary. The secret, the stored hash, and the
  `Authorization` header are not, and are never logged.
- **An unexpected failure answers `500 {"detail": "the request could not be completed"}`.** An
  exception's own text is written for a traceback and can name a host, a path, or a connection
  string; it goes to the log, with the stack, and never to the caller.
- **Metrics carry no credential and no knowledge.** Every value is a number and every label comes
  from a bounded vocabulary this codebase writes; no series names a project, a query, or a
  document, and a caller cannot create one. `/metrics` requires `system:read` all the same, because
  it describes the shape and health of a deployment. See
  [observability.md](observability.md).

## MongoDB privileges

The application never holds root credentials. It authenticates as a user with:

- `readWrite` on its own database;
- one cluster role narrowed to `replSetGetStatus`;
- one role narrowed to `collMod` on that database, for startup migrations.

Root credentials exist only for the one-shot `mongodb-init` service that creates those users. The
`mongot` synchronization user is separate again. Nothing the application connects as can run
server-side JavaScript, read another database, or administer the cluster.

## Query construction

MongoDB queries are documents, so the injection to rule out is not a quoting bug but a shape one: a
caller whose string became a key, an operator, or a nested document would be writing the query
rather than answering it.

- Caller input reaches only value positions. A search query lands in Atlas Search's `query` field
  and a filter value in `equals.value`, where the worst a crafted string can do is match nothing.
- `$where`, `$function`, and `$accumulator` appear nowhere in `app/`, and a test reads the source to
  keep it that way.
- The one place a regular expression is built from caller text — lexical skill search — escapes
  every term with `re.escape` and caps the query at eight terms, so a query of arbitrary length does
  not become a query of arbitrary cost.
- A key identifier is validated as exactly sixteen lowercase hexadecimal characters *before* it
  becomes a lookup, so a crafted credential never reaches the database at all.
- Every list is paginated with a bounded limit. Nothing returns an unbounded result set.

## Request limits

- Bodies are refused above `API_MAX_REQUEST_BYTES` (1 MiB by default) from `Content-Length`, before
  the body is read, so an oversized request costs the process nothing. Comfortably above the largest
  documented item, whose content the model bounds at 200 000 characters.
- A bodied request that declares no length is refused with `411` rather than streamed: without a
  declared size there is nothing to check against, and no endpoint here needs a chunked upload.
- Every field is length-bounded by its model: identifiers 128 characters, titles and summaries 500,
content 200 000, tags 64 with at most 50 of them, at most 16 scopes and 100 projects or profiles
per key.

## Response headers

Every response, including refusals and failures:

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; sandbox
Cross-Origin-Resource-Policy: same-origin
Cache-Control: no-store
```

This API answers programs in JSON and never serves a page, so these exist to stop a response being
reinterpreted as something a browser would run or frame. `no-store` is there because a retrieval
answer is somebody's project knowledge, and a shared cache holding it would hand it to the next
caller.

## Containers

| | runs as | capabilities | filesystem |
| --- | --- | --- | --- |
| `context-api` | `app` (uid 999) | none | read-only, `/tmp` in memory |
| `mcp` | `app` (uid 999) | none | read-only, `/tmp` in memory |
| `test` | `app` (uid 999) | none | writable (pytest cache) |
| `mongodb`, `mongodb-init`, `mongot` | image default | image default | writable |

Every service runs with `no-new-privileges`.

The images built here run unprivileged from their first instruction, write nothing outside a volume
and `/tmp`, and bind a port above 1024 — so they take a read-only root filesystem and no
capabilities at all. The official MongoDB images cannot: their entrypoints start as root to `chown`
the data directory and then drop privileges with `gosu`, which needs `CHOWN`, `SETUID`, and
`SETGID`. Dropping those stops `mongod` before it opens the database — verified, not assumed — so
they keep their capabilities.

MongoDB, `mongot`, and the MCP server sit on an `internal` Docker network with no route off the
host. Only the API is published, and only to `127.0.0.1`.

## Prompt injection

**Retrieved content is data.** Everything the platform stores was written by somebody, and some of
it will be written by somebody who wants a model to act on it. Two defences, both in
`app/mcp/formatting.py`:

*It is announced as data.* Every rendering that carries stored text is introduced with a sentence
saying the following is retrieved knowledge, not instructions, and is to be treated as data
whatever it appears to say.

*It cannot forge the structure around it.* A document can contain the markers the renderer uses —
`### `, `SOURCE:`, `TRUST:` — and without something an author cannot predict it could end its own
block and open one claiming to come from a trusted skill. So every rendering picks a fresh random
boundary, names it in the preamble, and wraps each body in it:

```
### Payment idempotency
SOURCE: project_context
TRUST: user
SCORE: 0.9312
CONTENT:
--8<--3f9a1c74
Payment requests carry a UUID4 idempotency key.
--8<--3f9a1c74
```

Forging the structure would mean guessing a value that did not exist when the document was written.

Each item also states **how far its source is trusted** — `trusted`, `user`, `external`, or
`generated` — because one answer mixes sources, and a curated skill and something a previous run
generated are not owed the same weight. A source that carries no trust level claims none: inventing
one would be the renderer vouching for it.

What is *not* claimed: none of this stops a model that chooses to follow an instruction it can see.
It makes the instruction visibly a quoted document rather than a turn in the conversation, and it
removes the ability to lie about where the document came from.

## Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `API_AUTH_ENABLED` | `true` | Require a key. `false` logs a warning and is reported by readiness. |
| `AUDIT_LOG_ENABLED` | `true` | Record authorization decisions. |
| `API_MAX_REQUEST_BYTES` | `1048576` | Largest accepted request body. |
| `METRICS_ENABLED` | `true` | Publish `/metrics`, to a caller holding `system:read`. |
| `MCP_SCOPES` | *(all)* | Narrow what the MCP server may ask for. |
| `MCP_PROJECT_IDS` | *(all)* | Narrow which projects it may reach. |
| `MCP_PROFILE_IDS` | *(all)* | Narrow which creator profiles it may reach. |
| `MCP_API_TOKEN_FILE` | — | File holding the key it presents to the API. |
| `CONTEXTCTL_API_TOKEN_FILE` | — | File holding the key `contextctl` presents. |

Prefer the `_FILE` forms. A token in an environment variable is readable through `/proc` and ends up
in shell history and CI logs.

## Checking a deployment

```bash
# Refused without a key.
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/v1/skills        # 401

# Accepted with one.
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $(cat ./secrets/ci-api-key)" \
  http://127.0.0.1:8000/api/v1/skills                                              # 200

# Authentication is on, and the audit trail with it.
curl -s http://127.0.0.1:8000/health/ready | jq .authentication

# Headers on every answer.
curl -sI http://127.0.0.1:8000/health/live | grep -iE 'content-type-options|frame|cache'

# No secret in the logs.
docker compose logs context-api | grep -ciE 'acp_[0-9a-f]{16}_|password|Bearer '    # 0

# Nor in the metrics.
curl -s -H "Authorization: Bearer $KEY" http://127.0.0.1:8000/metrics \
  | grep -ciE 'acp_[0-9a-f]{16}_|password|Bearer '                                 # 0
```

The security tests are `tests/unit/test_security_keys.py`,
`tests/unit/test_security_authentication.py`, `tests/unit/test_api_authorization.py`,
`tests/unit/test_api_middleware.py`, `tests/unit/test_query_construction.py`,
`tests/unit/test_prompt_injection.py`, `tests/unit/test_cli_keys.py`,
`tests/unit/test_compose.py`, and `tests/unit/test_dockerfile.py`.

## Research ingestion boundaries

The configured providers constrain acquisition and retained material. These
controls require an appropriate deployment network policy when accepting
untrusted source submissions.

| Boundary | Review outcome |
| --- | --- |
| URL ingestion | Registered HTTP(S) sources only; public-address preflight checks, bounded redirects/time/size, approved content types, and no ambient proxies. A connected peer is checked after response headers where available. DNS results are not pinned to the connection; use outbound restrictions for private and metadata networks. |
| File/PDF ingestion | Bytes only, no paths or execution; MIME/signature checks, parser/page/stream/extracted-text ceilings, unsafe filename, archive, and executable rejection. |
| Git/GitHub ingestion | Fixed argument invocation with a sterile environment; no shell, hooks, checkout, submodules, scripts, code execution, or credentials. File/repository ceilings and binary, symlink, unsafe-path, and likely-secret exclusions apply. |
| Authorization and evidence | V1 scopes plus linked-project and preserved-record authorization guard research projects, sources, claims, citations, candidate review, and promotion. Private source material is not returned before these checks. |
| MCP | Allowlisted Pydantic tools forward to scoped REST routes only. There is no arbitrary URL, filesystem, shell, MongoDB, or Git command input. |
| Abuse and logs | Request-size and request-concurrency middleware remain in place. New V3 source-count, fail-fast ingestion-concurrency, and chunk-count limits stop persistent or expensive work early. Structured logs redact common credentials and URL userinfo, including exception text. |

Residual network boundary: DNS validation and network connection use separate
resolutions for both web and repository acquisition. A post-response peer check
cannot prevent a request already issued to a changed address; Git exposes no
connected-peer hook. Enforce outbound restrictions against private, loopback,
link-local, and cloud metadata destinations for untrusted ingestion. These
application checks alone do not establish comprehensive SSRF prevention.
