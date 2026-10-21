# Generic MCP client onboarding

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


Agent Context has one capability-based MCP gateway and one shared knowledge
store. Adding a compatible client is an administrative identity-and-configuration
task, not a request for a client-specific backend, database, search tool, or
authorization implementation.

## Supported integration metadata

List the built-in adapters:

```bash
contextctl integration list
contextctl integration show codex
contextctl integration show claude-code
contextctl integration show gemini-cli
contextctl integration show custom
```

`show` emits only token-free configuration references, compatibility facts, and
verification steps. It never creates a client, issues a credential, edits a
client configuration file, or makes a network request.

The built-in adapters cover Codex's existing stdio launcher, Claude Code's
remote HTTP configuration, Gemini CLI's Streamable HTTP settings, and a
vendor-neutral `custom` Streamable HTTP template. All use the existing tools
and gateway controls.

## Administrative flow

An operator creates the client identity and grants only the necessary scopes
and projects. For example, a custom Streamable HTTP client can start with a
read-only capability set:

```bash
contextctl agent create \
  --client-id review-agent \
  --type custom \
  --name "Review agent" \
  --owner-id YOUR_OWNER_ID \
  --scope skills:read \
  --scope projects:read \
  --scope context:read \
  --scope memory:read \
  --scope search:read \
  --scope agent:self \
  --project payment-service

contextctl agent token create review-agent
```

Credential issuance is administrative. The gateway never exposes a normal MCP
tool that can create or reveal credentials. The token is displayed once by the
administrative command; store it in an approved secret store and supply it only
to that one client installation.

Profiles, when the operator elects to use them, remain scope snapshots on the
existing V4 `agent_clients` record. They do not create policy inheritance or
allow a later profile edit to widen existing credentials. Project overrides may
only narrow a client grant.

## Remote MCP configuration contract

Remote clients use the full `/mcp` endpoint and these runtime references:

```text
AGENT_CONTEXT_MCP_URL=https://agents.example.com/mcp
AGENT_CONTEXT_TOKEN=<dedicated Agent Context credential>
```

Use bearer authentication:

```json
{
  "Authorization": "Bearer ${AGENT_CONTEXT_TOKEN}"
}
```

Do not use provider credentials such as `GEMINI_API_KEY`, `GOOGLE_API_KEY`, or
an Anthropic/OpenAI provider key as Agent Context credentials. Use TLS for all
remote deployments. A client-side trust setting or tool filter is never server
authorization; the gateway still checks authentication, credential scopes,
projects, approval boundaries, revision checks, idempotency, and rate limits.

## Local preflight

Run a local, redacting check before configuring a client:

```bash
contextctl integration validate gemini-cli
```

This checks only the local environment-variable shape and TLS/`/mcp` policy. It
does not print a token, connect to the endpoint, invoke an MCP tool, or make a
paid model call. Use the verification steps printed by `contextctl integration
show <client>` for a deliberate connection test.

## Compatibility contract

Before supporting a new client, run the generic compatibility suite. It proves
the existing gateway's initialize/connect, tool discovery, bearer
authentication, scope/project authorization, reads, writes, project resolution,
stable errors, revision conflicts, idempotency, per-client rate limits, and
reconnection behavior. The suite uses Codex-like, Claude-like, Gemini-like, and
custom fixtures only; it does not require a vendor account or external model
call.
