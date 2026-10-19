# Claude Code remote MCP

## Managed installation setup

Complete the [organization console](../dashboard.md) cutovers first. A lead
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
change assigned clients or keys. See [managed agents](../managed-agents.md)
for rotation, expiry, custody, revocation and normal-machine review refusal.


Claude Code and Codex use the same Agent Context MCP server and the same
client-neutral tools. Remote clients connect to the Streamable HTTP endpoint:

```text
Claude Code → https://agents.example.com/mcp → Agent Gateway → Context API → MongoDB
```

There is no Claude-specific endpoint, database, tool, or credential type.

## Prerequisites

- A current Claude Code installation (`claude --version`).
- Agent Context deployed at a public or private HTTPS URL ending in `/mcp`.
- TLS terminated with a certificate trusted by the Claude Code host. Plain HTTP
  is appropriate only for a same-machine `127.0.0.1` development deployment.
- An operator able to administer Agent Context clients and credentials.

Claude Code's current MCP documentation recommends remote HTTP for remote
servers. Its documented command is `claude mcp add --transport http …`; it
also supports `type: "http"` plus environment-variable expansion in the
`url` and `headers` fields of `.mcp.json` and user configuration. This guide
uses `add-json` so the shell does not expand or persist a token value.

## Create the Claude Code identity and dedicated credential

Run these as an Agent Context operator, substituting the owner and the least
privileges required by this client:

```bash
contextctl agent create \
  --client-id claude-code-main \
  --type claude_code \
  --name "Claude Code" \
  --owner-id YOUR_OWNER_ID \
  --scope search:read \
  --scope context:read \
  --scope context:write \
  --scope projects:read \
  --scope skills:read \
  --scope memory:read \
  --scope memory:write \
  --scope research:read \
  --scope research:write \
  --scope agent:self \
  --scope session:write \
  --scope audit:self:read

contextctl agent token create claude-code-main \
  --scope search:read \
  --scope context:read \
  --scope context:write \
  --scope projects:read \
  --scope skills:read \
  --scope memory:read \
  --scope memory:write \
  --scope research:read \
  --scope research:write \
  --scope agent:self \
  --scope session:write \
  --scope audit:self:read
```

The second command displays the generated Agent Context token exactly once.
Store it in the operator-approved secret store, then make it available only to
the Claude Code process. Do not use, copy, or rename `ANTHROPIC_API_KEY` or
`ANTHROPIC_AUTH_TOKEN`; those authenticate Claude Code to a model provider,
not Claude Code to Agent Context.

This is the Claude Code default capability set. It deliberately excludes
`agent:manage`, `research:approve`, `research:promote`, and `content:publish`.
Grant any of those only to a separately reviewed client and credential; normal
MCP tools never administer credentials.

Set the two dedicated variables in the terminal/session that starts Claude
Code. `AGENT_CONTEXT_MCP_URL` is the full MCP endpoint, not just the origin:

```bash
export AGENT_CONTEXT_MCP_URL='https://agents.example.com/mcp'
export AGENT_CONTEXT_TOKEN='paste-the-dedicated-agent-context-token-here'
```

Never commit either export, a dotenv file containing the token, or an expanded
configuration containing the token.

## User-scope setup (personal, cross-project)

For personal access across projects, configure the server at Claude Code's
`user` scope. This is the current HTTP remote configuration form, with literal
environment references retained by the single quotes:

```bash
claude mcp add-json --scope user agent-context \
  '{"type":"http","url":"${AGENT_CONTEXT_MCP_URL}","headers":{"Authorization":"Bearer ${AGENT_CONTEXT_TOKEN}"}}'
```

This writes a user-scope server definition, not a shell profile. Run it only
after exporting the variables for a session where you intend to use it. To
inspect the definition and connection status:

```bash
claude mcp get agent-context
claude mcp list
```

## Project-scope setup

For a repository-shared server definition, commit the following
`.mcp.json` at the repository root. It intentionally contains variable
references, never a credential:

```json
{
  "mcpServers": {
    "agent-context": {
      "type": "http",
      "url": "${AGENT_CONTEXT_MCP_URL}",
      "headers": {
        "Authorization": "Bearer ${AGENT_CONTEXT_TOKEN}"
      }
    }
  }
}
```

Each developer supplies their own `AGENT_CONTEXT_TOKEN`, issued for their own
`claude_code` client identity. Claude Code asks for approval before using a
project MCP server; review the configuration and approve it interactively.
Do not bypass that approval. A missing required variable makes Claude Code
warn about the configuration; some credential variables may resolve to an
empty value. Treat that as a failed setup and fix the process environment —
never put a fallback token in `.mcp.json`.

## Verify connection and permissions

Start Claude Code in a shell that has both variables:

```bash
claude
```

Then run `/mcp` inside Claude Code. `agent-context` should report as connected.
Ask Claude to list the available Agent Context tools, then make these bounded
checks:

1. Ask it to use `search_knowledge` for a known term.
2. Ask it to use `save_context` with an authorized `project_id` and a harmless
   test record.
3. Confirm the saved record through the normal API or `search_context`.

For a long-running workspace task, Claude Code may call
`begin_agent_session` with the resolved project and a repository label. The
shared gateway then correlates subsequent calls such as `search_knowledge`
and `save_context` in activity and retrieval logs. This lightweight session
stores only status, timestamps, project/repository association, and bounded
scalar operational metadata; it never stores the Claude conversation, prompt,
context window, or private reasoning. End it with `end_agent_session` using
the returned revision when the task finishes.

The available tools are controlled by the credential's scopes and project
allowlist. A `search:read`-only credential can discover and call read tools,
but `save_context` requires `context:write`; a named project outside the
allowlist is refused. The server's configured MCP grant can narrow these
permissions further, never widen them.

## TLS and deployment

Publish `/mcp` behind HTTPS in production, normally through the same reverse
proxy that exposes the Context API. Keep the API/container network private;
the remote edge accepts a bearer credential on every request and forwards it
only in process to the existing API authorization path. Do not terminate TLS
on an untrusted proxy, disable certificate validation, or put a token in the
URL.

Set `APP_ENV=production` and `MCP_REMOTE_ALLOW_LOOPBACK_HTTP=false` in a
production deployment. If TLS terminates at a reverse proxy, set
`API_PROXY_HEADERS=true` and `API_FORWARDED_ALLOW_IPS` to that proxy's explicit
IP/CIDR allowlist — never `*`. This lets the server accept the proxy-derived
scheme while refusing a public client that merely sends `X-Forwarded-Proto`.
Requests are capped at `API_MAX_REQUEST_BYTES`; live remote MCP sessions are
bounded by `MCP_REMOTE_MAX_SESSIONS`.

For a local-only development service, `http://127.0.0.1:8000/mcp` is allowed.
It is not a production remote endpoint.

## Revocation

Immediately disable the specific Claude Code client if its credential is
lost, or revoke the client during offboarding:

```bash
contextctl agent revoke claude-code-main
```

The next MCP tool call is denied, including inside an already-open remote MCP
session. Revoking or disabling this client does not affect Codex or any other
client's credential.

## Rotation

Rotate a Claude credential without interrupting another client: mint a new
credential for the same client, update only the Claude Code process's secret
store/environment, reconnect and verify `search_knowledge`, then revoke the
old key by its public key ID:

```bash
contextctl keys list
contextctl agent token create claude-code-main --scope search:read --scope context:read
contextctl keys revoke --key-id OLD_KEY_ID
```

Never rotate by reusing a Codex credential or either Anthropic credential.
Revocation preserves historical knowledge, content, and actor provenance; it
only removes future access.

## Troubleshooting

- **`UNAUTHENTICATED` / HTTP 401:** verify that both variables are exported to
  the process that starts `claude`, that the URL ends in `/mcp`, and that the
  token is the dedicated Agent Context credential.
- **`CLIENT_DISABLED` / HTTP 403:** ask an operator to reactivate or replace
  the Claude Code client; do not substitute another agent's token.
- **`FORBIDDEN` or `PROJECT_FORBIDDEN`:** issue only the required scope or add
  the authorized project to the client's and credential's allowlists.
- **MCP is pending approval:** open Claude Code interactively in the project
  and approve the `.mcp.json` definition. Inspect it first.
- **TLS failure:** fix the server certificate chain or trusted corporate CA;
  do not disable TLS verification.
- **Configuration parse failure:** use `"type": "http"` with the full
  endpoint URL. An entry with `url` but no `type` is treated as stdio by
  Claude Code and fails.

## Integration-test strategy

CI exercises the MCP SDK's Streamable HTTP client against `/mcp`, using a
dedicated Claude Code client credential. It verifies bearer rejection,
revocation, tool discovery, `search_knowledge`, and `save_context`, alongside
the existing Codex stdio regression surface. It does not invoke a hosted model
or real Claude CLI account.

For a manual Claude Code E2E check, configure a user- or project-scope server
as above and run [`scripts/verify-claude-code-mcp.sh`](../../scripts/verify-claude-code-mcp.sh)
with `RUN_CLAUDE_CODE_E2E=1` and an authorized test project. The script makes
no configuration or shell-profile changes; it only invokes the already
configured Claude Code client after explicit opt-in.

## Official reference verified

The setup syntax and JSON shape above were verified against Anthropic's
current [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp),
which documents remote HTTP as the recommended transport, `--scope user` /
`--scope project`, `type: "http"`, bearer headers, `${VAR}` expansion, and
project MCP approval behavior.
