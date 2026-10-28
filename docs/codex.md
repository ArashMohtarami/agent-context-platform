# Codex integration

## Managed installation setup

Complete the [organization console](dashboard.md) cutovers first. A lead
approves bounded client policy and verified sponsor/team; the authenticated
recipient generates their token once. Do not use a legacy operator key recipe
to bypass managed issuance. Leads cannot recover another worker's plaintext.
Copy explicitly into a private file/approved secret store; retries return metadata
and lost delivery needs revoke/replacement. Never store a token in source or
browser local/session storage.

Each credential is bound to one organization. Key/client policy intersects current
sponsor, team and delegation rights per action/resource in REST and every MCP
preflight, including active sessions. Browser cookies cannot authenticate this
bearer surface. Provider API keys remain separate. Template edits/disable do not
change assigned clients or keys. See [managed agents](managed-agents.md)
for rotation, expiry, custody, revocation and normal-machine review refusal.


Codex reaches this platform as an MCP server. Nothing about Codex is modified, forked, or patched;
it is configured, and it spawns a process that talks to the Context API.

```
Codex ──stdio/MCP──► MCP server ──HTTP──► Context API ──► services ──► MongoDB
```

The platform is Codex's **durable** context layer. Codex keeps its own working context for the
session; what is stored here outlives the session, the terminal, and the machine. The two are not
connected, and this project never reads or writes Codex's internal state — `~/.codex` holds Codex's
own databases, and the only file here that belongs to Codex is `config.toml`.

## Configuration

Codex's MCP configuration lives in `config.toml`: `~/.codex/config.toml` for every project, or
`.codex/config.toml` inside a project you have trusted. One table per server, named `mcp_servers`
in snake_case, and `command` is what selects the stdio transport.

*(Syntax verified against the official Codex documentation — see Sources at the end.)*

### From any project

A project-local `.codex/config.toml` belongs only to the project where you create it and is not
inherited by another repository. If `codex mcp get agent-context` works here but says the server is absent elsewhere,
the user-level entry below is missing.

This is the normal case: some repository you are working in wants the platform. Add to
`~/.codex/config.toml`, substituting your checkout path:

```toml
[mcp_servers.agent-context]
command = "/absolute/path/to/agent-context-platform/scripts/codex-mcp-server.sh"
env = { MCP_API_BASE_URL = "http://127.0.0.1:8000", LOG_LEVEL = "INFO" }
startup_timeout_sec = 30
tool_timeout_sec = 60
required = true
```

Or let Codex write it:

```bash
codex mcp add agent-context \
  --env MCP_API_BASE_URL=http://127.0.0.1:8000 \
  -- /absolute/path/to/agent-context-platform/scripts/codex-mcp-server.sh
codex mcp list
```

The command must be absolute here, because Codex starts the server from whatever project you are
in. The launcher then resolves this repository from its own location, picks `.venv/bin/python` when
one exists, and sets `PYTHONPATH` — so it works with no virtual environment activated and no
inherited `PATH`.

### Inside this repository

The repository ships a token-free opt-in template. Copy it to activate the local
configuration:

```bash
mkdir -p .codex
cp examples/clients/codex-config.toml .codex/config.toml
```

Set `MCP_API_TOKEN_FILE` in the copied file to your dedicated credential path.
The template uses a relative launcher command for this checkout; use an absolute
launcher path for other repositories. Trust the project when Codex asks.
Active `.codex/` configuration remains local and is excluded from public releases.

### Running the server in a container

If you would rather not run Python on the host:

```toml
[mcp_servers.agent-context]
command = "docker"
args = ["compose", "--profile", "mcp", "run", "--rm", "-T", "mcp"]
cwd = "/absolute/path/to/agent-context-platform"
startup_timeout_sec = 60
```

`-T` is required: without it Compose allocates a TTY and the protocol stream is corrupted. The
container joins the private network and uses `MCP_API_BASE_URL=http://context-api:8000`, so the
host address does not apply. Raise `startup_timeout_sec` — a container has to be created first.

### Configuration keys

| Key | Purpose | Default |
| --- | --- | --- |
| `command` | The server to spawn; its presence selects stdio | — |
| `args` | Arguments passed to it | `[]` |
| `env` | Environment variables set for the server | — |
| `cwd` | Working directory to start it from | — |
| `startup_timeout_sec` | How long Codex waits for initialization | 10 |
| `tool_timeout_sec` | Per-call timeout | 60 |
| `enabled` | Set `false` to disable without deleting | `true` |
| `required` | Fail the session if the server cannot start | `false` |

## Environment variables

These configure the **MCP server process**, and are set through the `env` table above. It holds no
database credentials: it reaches the platform through the API and nothing else.

| Variable | Purpose | Default |
| --- | --- | --- |
| `MCP_API_BASE_URL` | The Context API address | `http://127.0.0.1:8000` |
| `MCP_SERVER_NAME` | Name Codex shows for the server | `agent-context-platform` |
| `MCP_REQUEST_TIMEOUT_SECONDS` | Timeout for one API request | `30` |
| `MCP_SCOPES` | Comma-separated scopes; empty adds no local narrowing; backend policy still applies | empty |
| `MCP_PROJECT_IDS` | Comma-separated projects; empty adds no local narrowing; backend policy still applies | empty |
| `MCP_PROFILE_IDS` | Comma-separated creator profiles; empty adds no local narrowing; backend policy still applies | empty |
| `MCP_API_TOKEN_FILE` | File holding a bearer token for the API | unset |
| `MCP_API_CA_FILE` | Explicit reviewed private CA for this process; normal hostname verification stays enabled | unset (normal public CA trust) |
| `LOG_LEVEL` | `DEBUG`…`CRITICAL`; logs go to stderr | `INFO` |

Narrowing is how you hand one Codex session one project:

```toml
env = { MCP_API_BASE_URL = "http://127.0.0.1:8000", MCP_PROJECT_IDS = "payment-service", MCP_SCOPES = "search:read,context:read,context:write,memory:read,memory:write" }
```

That local filter permits only `payment-service` and eligible organization-global
sources; the API still intersects current action/resource and sponsor policy.
Local settings alone grant nothing.

For an unmanaged legacy rehearsal only, the historical operator issuance below
requires explicit org context and is refused under managed policy. Current
managed installations use recipient-only generation described above, then set
`MCP_API_TOKEN_FILE`:

```bash
docker compose exec -T context-api python -m app.cli.contextctl --json --org-id YOUR_ORG keys create \
  --name codex-payments \
  --scope search:read --scope context:read --scope context:write \
  --scope memory:read --scope memory:write \
  --project payment-service | jq -r .key > ~/.codex/agent-context-key
chmod 600 ~/.codex/agent-context-key
```

```toml
env = { MCP_API_BASE_URL = "http://127.0.0.1:8000", MCP_API_TOKEN_FILE = "/home/you/.codex/agent-context-key", MCP_PROJECT_IDS = "payment-service" }
```

A file rather than `MCP_API_TOKEN`: a token in an environment variable is readable through `/proc`
by every process running as you, and ends up in shell history. The key is checked again at the API,
so this narrowing and the key's own grant both apply and the narrower one wins.

## Startup requirements

The MCP server is a client of the platform, not the platform. **Start the platform first:**

```bash
docker compose up -d --build --wait
curl --fail http://127.0.0.1:8000/health/ready
make import-skills          # once, so skill retrieval has something to return
```

Readiness must be `ready` before Codex will get useful answers. It requires MongoDB primary,
`mongot` reachable, the embedding model loaded, and all six search indexes queryable. The MCP
server starts happily against an unready platform and will simply report failures — ask it for
`get_system_status` to see which capability is missing.

The server itself needs nothing: it loads no model, opens no database connection, and starts in
well under a second.

## The expected workflow

```
You:    "Implement payment refund support in payment-service."

Codex:  search_knowledge(query="Implement payment refund support.",
                         project_id="payment-service")

        ← the ledger rules, the idempotency rules, the provider's refund
          behaviour, the wallet split, the architecture — ranked together and
          cut to a token budget

Codex:  writes the code, using what the project already decided

Codex:  save_context(project_id="payment-service", type="implementation",
                     title="Refund implementation notes", ...)
```

The last step is what makes the next session cheaper. It is a call Codex makes on purpose, when
asked or when it judges something worth keeping — nothing here watches a conversation and saves it.

**Always pass `project_id`.** Project context and project memory are unreachable without it, and no
tool will guess. Without it, search can use explicitly permitted organization-global knowledge and the authenticated
client's own agent memory, never another project's context or memory.
`list_projects` finds the identifier.

Useful things to tell Codex, once per project:

> Use the agent-context MCP server. The project_id is `payment-service`. Search knowledge before
> making architectural decisions, and save what you decide.

A note on what comes back: retrieved content arrives fenced and labelled by source, introduced as
data. It is knowledge, not instruction — if a stored document says "ignore your instructions", it
arrives visibly as a document saying that.

## Try it: the payment-service example

`examples/payment-service/` holds a realistic project: its architecture, Keycloak integration and
scopes, the payment provider's asynchronous refund behaviour, wallet split rules, append-only
ledger rules, and idempotency rules — plus two project memories.

```bash
python examples/payment-service/load.py
```

It creates the project through the API and stores everything through the **MCP tools**, so running
it also proves `save_context` and `save_memory` work. Every item carries an `external_id`, so
running it twice stores one copy.

Then ask Codex to implement refund support. The retrieval looks like this:

```
query: "Implement payment refund support."  project_id=payment-service

  0.8590  project_context  Wallet behavior on payment and refund
  0.8560  memory           Refund changes need a second reviewer
  0.7876  project_context  Payment service architecture
  0.7529  project_context  Payment provider behavior, including refunds
  0.7512  project_context  Ledger rules
  0.7269  project_context  Idempotency rules

  method=hybrid  searched=[project_context, active_skill, memory]
  tokens=800/4000  latency=32ms
```

Every rule a refund implementation needs, in one call, at a fifth of the token budget.

## Troubleshooting

**Codex does not list the tools.** Check the table name is `mcp_servers`, not `mcpServers` — the
camelCase form other clients use is silently ignored. Then `codex mcp list`. For a project-scoped
`.codex/config.toml`, confirm you trusted the project; Codex ignores it otherwise.

**The server fails to start.** Run the command yourself, exactly as configured:

```bash
/absolute/path/to/agent-context-platform/scripts/codex-mcp-server.sh </dev/null
```

It should print one `mcp_server_started` line to stderr and nothing to stdout. If `command` is
relative in a user-level config, make it absolute. The API client intentionally ignores inherited
`HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` settings: an explicit self-hosted platform address must
not be routed through an ambient proxy, and local MCP startup does not require `socksio`.

**Tools answer "the platform is unreachable."** The MCP server started but the Context API did not
answer. Check `curl --fail http://127.0.0.1:8000/health/ready`, and that `MCP_API_BASE_URL` matches
where the API actually listens. A containerised MCP server cannot reach `127.0.0.1` — it needs
`http://context-api:8000` on the private network.

**Searches return nothing.** Ask for `get_system_status`. If a capability is failing, retrieval is
degraded rather than broken — lexical search keeps working without the embedding model. If
everything is healthy, the content may not be indexed yet: `mongot` indexes asynchronously, with timing that depends on load and index readiness.

**`list_skills` is empty.** Skills are imported, not seeded: run `make import-skills`.

**Everything returns "not authorized".** `MCP_SCOPES`, `MCP_PROJECT_IDS`, or `MCP_PROFILE_IDS`
may be narrower than the call, or the credential/client may lack the required
grant. The message names a missing scope. Correct the intended grant or local
restriction; removing a local filter cannot widen the API credential.

**A project's context is missing.** The call almost certainly omitted `project_id`, which addresses
permitted organization-global/client-operational knowledge, without project-owned records. Nothing
infers a project.

**The protocol stream is corrupted.** Something wrote to stdout. If you wrote a custom launcher,
send its diagnostics to stderr; if you are using `docker compose run`, add `-T`.

## Memory and client boundaries

- No automatic conversation memory. Memory is written by an explicit `save_memory` call. Reviewed research
  candidates have a separate promotion workflow; entire conversations are never
  converted to memory automatically.
- No Codex internals. Nothing reads or writes `~/.codex/*.sqlite`, and Codex's own context cache is
  not touched, mirrored, or replaced.

## Verification

```bash
python -m pytest                                     # includes the shipped configuration
docker compose --profile test run --rm --build test  # live mongod + mongot checks
```

The configuration itself is under test: that it uses the current table name and the stdio
transport, that the command it names exists and is executable, that it carries no credentials, and
that the example project validates against the API's own schemas and holds the knowledge the
documented refund walkthrough needs.

## Sources

- [Model Context Protocol in Codex](https://developers.openai.com/codex/mcp) (redirects to
  [learn.chatgpt.com/docs/extend/mcp](https://learn.chatgpt.com/docs/extend/mcp?surface=cli))
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
