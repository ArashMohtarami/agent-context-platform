# Gemini CLI remote MCP

Gemini CLI connects to the same client-neutral Streamable HTTP endpoint used by
Codex and Claude Code:

```text
Gemini CLI → https://agents.example.com/mcp → Agent Gateway → Context API → MongoDB
```

There is no `/mcp/gemini`, Gemini-specific server, database, tool, or
credential type. Gemini uses the existing `/mcp` endpoint and its bearer
authorization middleware.

## Prerequisites

- A current Gemini CLI installation (`gemini --version`).
- Agent Context deployed at a public or private HTTPS URL ending in `/mcp`.
- A certificate trusted by the Gemini CLI host. Plain HTTP is only for direct,
  same-machine `127.0.0.1` development.
- An Agent Context operator able to create clients and issue credentials.

Use the dedicated Agent Context variables below. Do not substitute
`GEMINI_API_KEY`, `GOOGLE_API_KEY`, or `GOOGLE_APPLICATION_CREDENTIALS`; those
are provider credentials, not Agent Context gateway credentials.

```bash
export AGENT_CONTEXT_MCP_URL='https://agents.example.com/mcp'
export AGENT_CONTEXT_TOKEN='<dedicated-agent-context-credential>'
```

Never commit either export, a dotenv file containing the credential, or a
settings file with an expanded credential.

## Create `gemini-cli-main` and a dedicated credential

Run these as an Agent Context operator. Substitute your owner ID and only the
projects this client should reach. The client and its credential have separate
independent lifecycles from Codex and Claude Code.

```bash
contextctl agent create \
  --client-id gemini-cli-main \
  --type gemini_cli \
  --name "Gemini CLI" \
  --owner-id YOUR_OWNER_ID \
  --scope skills:read \
  --scope projects:read \
  --scope context:read \
  --scope context:write \
  --scope memory:read \
  --scope memory:write \
  --scope search:read \
  --scope research:read \
  --scope research:write \
  --scope agent:self \
  --scope session:write \
  --scope audit:self:read

contextctl agent token create gemini-cli-main \
  --scope skills:read \
  --scope projects:read \
  --scope context:read \
  --scope context:write \
  --scope memory:read \
  --scope memory:write \
  --scope search:read \
  --scope research:read \
  --scope research:write \
  --scope agent:self \
  --scope session:write \
  --scope audit:self:read
```

The credential is shown exactly once. Store it in an approved secret store and
make it available only to the Gemini CLI process. The default excludes
administrative or high-impact access: `admin`, `agent:manage`,
`credential:manage`, `research:approve`, `research:promote`, and
`content:publish` are not granted.

## Credential lifecycle and log safety

An Agent Context credential is an independently issued `acp_…` bearer value.
The platform persists only its SHA-256 hash, never the plaintext value; the
plaintext is returned once at issuance and is never included in gateway
responses, activity, audit records, or structured logs. Use an expiry for a
workstation credential when operationally appropriate:

```bash
contextctl agent token create gemini-cli-main --expires-in-days 90
```

Rotate by minting a new credential for the same Gemini client, updating only
the secret-store injection for that process, reconnecting and verifying it,
then revoking the old key by public key ID. Do not copy a Codex or Claude
credential into Gemini during rotation. Revocation and expiry are checked on
every request, including later calls on an already-open MCP session.

## Server alias and transport

Use the stable alias `agent-context`. Do not use an underscore in an MCP server
alias (for example, do not use `agent_context`): Gemini CLI qualifies tools as
`mcp_<server>_<tool>`, and its policy parser can misidentify aliases that
contain underscores. Hyphens are safe.

The examples use `httpUrl`, which Gemini CLI maps to Streamable HTTP. It must
be the complete `/mcp` endpoint. Do not add `url` or `type` alongside it in the
same server entry. The current `gemini mcp add --transport http` command is
also supported for managing an HTTP server; its command-managed representation
is distinct from this explicit `httpUrl` settings form.

## User-level configuration

Use `~/.gemini/settings.json` for personal, cross-project access. Merge this
object with other settings already in the file rather than replacing them.

```json
{
  "mcpServers": {
    "agent-context": {
      "httpUrl": "${AGENT_CONTEXT_MCP_URL}",
      "headers": {
        "Authorization": "Bearer ${AGENT_CONTEXT_TOKEN}"
      },
      "timeout": 30000,
      "description": "Agent Context shared MCP gateway",
      "trust": false
    }
  }
}
```

`httpUrl` selects the remote Streamable HTTP transport. `headers` places the
dedicated credential in the request's bearer header at runtime, rather than in
the file. `timeout` is milliseconds and `description` helps identify the
connection in Gemini. Keep `trust` false unless an operator has deliberately
reviewed a controlled workflow.

To print the exact token-free fragment without editing a file:

```bash
contextctl integration gemini-cli setup --scope user --show-json
```

The helper only prints configuration and instructions. It does not create a
client, issue a credential, edit Gemini settings, set shell variables, or set
`trust: true`.

## Project-level configuration

For a repository-specific definition, merge the same token-free fragment into
`<repository>/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "agent-context": {
      "httpUrl": "${AGENT_CONTEXT_MCP_URL}",
      "headers": {
        "Authorization": "Bearer ${AGENT_CONTEXT_TOKEN}"
      },
      "timeout": 30000,
      "description": "Agent Context shared MCP gateway",
      "trust": false
    }
  }
}
```

Every developer supplies an independently issued `AGENT_CONTEXT_TOKEN`; a
repository settings file never carries a shared credential. Print a
project-oriented example with:

```bash
contextctl integration gemini-cli setup --scope project --show-json
```

Gemini CLI loads configuration in increasing precedence: built-in defaults,
system defaults, user settings, project settings, system settings, environment,
then command-line arguments. In particular, a project `.gemini/settings.json`
overrides the user entry for the same setting. Review a repository's settings
before running Gemini there, especially if it defines or replaces
`agent-context`.

Before committing a repository, scan the effective `.gemini/settings.json` and
related examples for literal `acp_…` values. `${AGENT_CONTEXT_TOKEN}` is the
only credential form appropriate for a tracked settings file.

## Tool filtering and read-only discovery

Gemini's `includeTools` is an allowlist of discovered tools; `excludeTools`
removes tools and takes precedence if a name appears in both lists. They are
useful for reducing accidental tool exposure in a particular workspace:

```json
{
  "mcpServers": {
    "agent-context": {
      "httpUrl": "${AGENT_CONTEXT_MCP_URL}",
      "headers": {
        "Authorization": "Bearer ${AGENT_CONTEXT_TOKEN}"
      },
      "timeout": 30000,
      "description": "Agent Context shared MCP gateway",
      "includeTools": [
        "get_platform_capabilities",
        "search_knowledge",
        "search_context",
        "search_memory"
      ],
      "excludeTools": ["save_context", "save_memory"],
      "trust": false
    }
  }
}
```

Use the helper's convenience read-only discovery list when appropriate:

```bash
contextctl integration gemini-cli setup --scope project --read-only --show-json
```

This is **not authorization**. An `includeTools` or `excludeTools` setting
changes only what Gemini exposes to its model; removing or editing it cannot
grant a gateway action. Issue a credential with read scopes when a client must
be read-only. The gateway independently enforces authentication, credential
scopes, project access, content-publication approval, research-promotion
approval, and expected-revision checks.

## Trust guidance

Gemini defaults to `trust: false`, which retains its normal confirmation
behavior. Do not blindly set `trust: true`: Gemini documents that it bypasses
all tool-call confirmations for that server. Even in a deliberately trusted,
controlled environment, it does not bypass the Agent Context gateway's
authentication, scopes, projects, publication approval, research-promotion
approval, or optimistic-concurrency revision checks.

## Gateway authorization validation

Gemini's permission decision is made by the existing V4 gateway from the
dedicated bearer credential, its linked client, the credential/client scope
intersection, and project grants. Gemini's `clientInfo`, arbitrary HTTP
headers, `includeTools`, `excludeTools`, and `trust` setting are not inputs to
that decision.

The authorization validation matrix covers three independent credentials: `gemini-cli-main`,
`gemini-cli-readonly`, and `gemini-cli-research`. It verifies read-only search
and denied writes; permitted context, memory, and research-candidate proposals
for the normal/research grants; denied research approval/promotion and content
publication without elevated scopes; project denial; and client/scope spoofing
attempts. This is shared V4 authorization behavior, not a Gemini exception.

## Project resolution, sessions, and provenance

Gemini uses the same V4 `resolve_project` tool and explicit
`project_bindings` as every other MCP client. Supply one of `project_id`,
`git_remote`, `repository_url`, `repository_name`, or `workspace_alias`
(`workspace_hint` remains supported for V4 compatibility). Resolution uses
only configured exact bindings. A result with `status: "ambiguous"` contains
candidates and must be resolved by an explicit operator/client choice; the
gateway never guesses a project.

Use `begin_agent_session` before a related workflow, optionally attaching an
authorized project, repository, and small operational metadata such as a
workspace label. Use `update_agent_session` with the returned revision and
`end_agent_session` when work finishes. Session records are operational
metadata only; they never persist Gemini conversations, prompts, messages, or
transcripts.

Writes made through the gateway are attributed from the dedicated credential,
not Gemini settings, `clientInfo`, caller-provided headers, scopes, or actor
claims. For `gemini-cli-main`, actor provenance is:

```json
{
  "actor_type": "agent",
  "client_id": "gemini-cli-main",
  "client_type": "gemini_cli"
}
```

The same credential-derived client identity and active session ID correlate
gateway activity and retrieval logs for `search_knowledge`, `save_context`,
`save_memory`, and authorized research writes. This metadata is diagnostic and
auditable; it is not a conversation store. A Gemini credential cannot use
another client's session: the gateway checks session ownership against the
authenticated client on every session-bound call.

## Shared three-agent knowledge workflows

Gemini CLI, Codex, and Claude Code are peers of the same shared platform. An
authorized project context or `project` memory saved by one is discoverable by
the others through the same capability-based tools and project grant. There is
no per-client context or memory collection.

Operational memory remains isolated: an `agent` memory belongs only to the
credential-linked client, while a `project_agent` memory belongs only to that
client within its explicitly named project. These scopes are derived at the
gateway from the bearer identity; no MCP argument can select another client's
operational memory.

The shared workflow also retains V4 safety controls. Every durable write has
credential-derived actor provenance, guarded updates require the revision a
client read, retries with the same Gemini idempotency key create one durable
object, and Gemini rate-limit exhaustion does not consume Codex or Claude
budgets.

## Current `gemini mcp` management command

The documented Streamable HTTP command syntax is:

```bash
gemini mcp add --scope user --transport http \
  --timeout 30000 \
  --description 'Agent Context shared MCP gateway' \
  --header 'Authorization: Bearer ${AGENT_CONTEXT_TOKEN}' \
  agent-context '${AGENT_CONTEXT_MCP_URL}'
```

Change `--scope user` to `--scope project` for project configuration. The
current command also supports comma-separated `--include-tools` and
`--exclude-tools`; use them only as client discovery controls. Omit `--trust`
to retain Gemini's default confirmation behavior. The non-mutating helper
prints the matching command for either scope and the read-only option:

```bash
contextctl integration gemini-cli setup --scope user
contextctl integration gemini-cli setup --scope project --read-only
```

## Verify the connection

First inspect registration and connection state:

```bash
gemini mcp list
```

Start Gemini CLI with both Agent Context environment variables, then run
`/mcp list`. `agent-context` should be connected and list discovered tools.
Make these bounded checks in order:

1. Call `get_platform_capabilities` and confirm `gemini-cli-main` has the
   expected non-administrative scopes.
2. Call `search_knowledge` for a known term.
3. Call `save_context` once with an authorized `project_id` and harmless test
   record; review the proposed write.
4. Confirm the record with `search_context` or the standard API.

The tools are exactly the same tools Codex and Claude Code use. Project
isolation, optimistic concurrency, idempotency, rate limits, provenance, and
audit behavior remain unchanged.

## TLS, revocation, and troubleshooting

Production remote MCP requires HTTPS. Publish `/mcp` through a trusted reverse
proxy, keep the API/container network private, and never place a bearer token
in a URL. Configure `API_PROXY_HEADERS=true` only together with an explicit
`API_FORWARDED_ALLOW_IPS` proxy IP/CIDR allowlist—never `*`; an untrusted
client-supplied `X-Forwarded-Proto` cannot upgrade plaintext HTTP. Direct
loopback HTTP is development-only. The existing request-size middleware rejects
oversized MCP requests before they are read, and the shared gateway applies its
bounded fast, standard, and long-running tool timeouts to Gemini exactly as it
does to Codex and Claude. If a credential is lost, revoke only its Gemini
client:

```bash
contextctl agent revoke gemini-cli-main
```

Codex and Claude Code remain unaffected. Rotate by issuing a new credential,
updating Gemini's secret injection, reconnecting and verifying, then revoking
the old credential.

- **`UNAUTHENTICATED` / HTTP 401:** verify the Gemini process received both
  Agent Context variables, the URL ends in `/mcp`, and the credential is an
  Agent Context credential.
- **Tools are absent or disconnected:** run `gemini mcp list`, then `/mcp
  list`; check whether a higher-precedence project or system setting replaced
  `agent-context`.
- **`FORBIDDEN` or `PROJECT_FORBIDDEN`:** grant only required scopes and
  projects to both client and credential; do not weaken tool filters or trust.
- **TLS failure:** repair the certificate chain or trusted corporate CA; never
  disable certificate verification.

## Official reference verified

The settings paths, precedence, environment interpolation, `httpUrl` transport,
headers, timeout, description, tool filters, alias warning, `--scope`, and
`--trust` behavior above were checked against Gemini CLI's current [MCP server
documentation](https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html)
and [configuration reference](https://google-gemini.github.io/gemini-cli/docs/get-started/configuration.html)
on 2026-10-09.
