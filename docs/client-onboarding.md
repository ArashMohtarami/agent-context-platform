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

## Historical unmanaged administration

The following V4 commands apply only to an explicitly unmanaged rehearsal. They
are refused by managed policy and are not the current evaluation setup path.
For the evaluation use [staged commissioning](evaluation.md), independent client
approval and recipient-only delivery above.

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

## Preview and diagnose an evaluation connection

Use the exact origin and project printed/approved during evaluation. Preview is
always the default, including when a token path is supplied:

```sh
contextctl --json integration doctor codex \
  --origin https://127.0.0.1:YOUR_PORT --project-id evaluation-notes
```

Replace `YOUR_PORT` with the actual numeric port. Select `claude-code`,
`gemini-cli` or `custom` for their HTTP recipes. The preview returns the existing
registry metadata, an actual TOML/JSON fragment and a recipient-terminal command.
It opens no active client configuration, token, CA or deployment `.env`, sends
no requests and changes no grants. There is no apply mode. Manually review and
merge only the `agent-context` server into your selected destination, preserving
other servers. Replace the stdio launcher/token/CA path placeholders locally.

An explicit live check reads only the selected recipient-owned regular 0600 file
and uses this exact origin. A private CA is trusted only for this process:

```sh
contextctl --json integration doctor codex \
  --origin https://127.0.0.1:YOUR_PORT --project-id evaluation-notes \
  --token-file /recipient/private/platform-token \
  --ca-file /evaluation/state/tls/ca.crt --connect
```

Inspect the evaluation CA fingerprint first. Normal public CA trust is used
when `--ca-file` is omitted. Codex's stdio bridge accepts `MCP_API_CA_FILE` in its
process environment for the same scoped trust. For Claude Code/Gemini CLI using
a private CA, configure that Node process's reviewed `NODE_EXTRA_CA_CERTS` before
startup; the doctor does not change vendor client trust. See the official
[Claude Code CA guidance](https://code.claude.com/docs/en/network-config#custom-ca-certificates)
and [Node CA contract](https://nodejs.org/api/cli.html#node_extra_ca_certsfile). Never bypass certificate
validation or silently import an evaluation CA into system trust.

The doctor returns one status/code/next step for each stage:

| Stage | What it actually proves |
| --- | --- |
| Configuration | Explicit selection and private credential source are valid; not an audit of installed vendor settings |
| Transport | The selected listener responds; HTTPS certificate/hostname verification passed (loopback HTTP is labeled separately) |
| Authentication | The actual server accepts this credential under current policy |
| Capabilities | Actual MCP initialization, tool discovery and `get_platform_capabilities` succeed; needs approved `agent:self` |
| Project | `resolve_project` resolves the exact selected authorized project; needs `projects:read` on its directory |
| Retrieval | Project-context `search_knowledge` succeeds with bounded results; needs coupled `search:read` and `context:read` for that project |
| Policy (requested) | `--require-read-only` checks the server credential's current scopes and coupled tuples contain only reads/`agent:self` |
| Write (opt-in) | A harmless context probe is created, or an expected read-only refusal is observed, only on the explicit disposable project |

A failed stage returns nonzero; later dependent checks remain `not_run`.
Explicit CA inputs must be regular files no larger than 1 MiB; symlinks and
devices are refused.
Reports contain fixed codes and a result count, never token/header/environment
values, private local paths, record IDs or retrieved text. Requests, responses,
stdio lifetime and total time are bounded. There is no model-provider call.
`EMPTY` is a successful empty retrieval, not proof of useful knowledge or search
relevance. Protocol success is not a claim that an installed vendor CLI was run.

For read-only evaluation, the manager first approves the necessary ceiling and
the verified recipient issues a narrower credential without write, approval,
promotion, publication, management or session-write scopes. Run doctor with
`--require-read-only`. This verifies server policy; Gemini's existing setup
`--read-only` option remains only a local discovery filter and is labeled as
unverified server access. Preview never narrows/replaces an existing credential.
Sponsor/team/parent expiry and action/resource grants remain authoritative on
every real request. A credential with broader server rights fails the read-only
check even if its client hides write tools.

A write check is a separate opt-in:

```sh
contextctl --json integration doctor claude-code \
  --origin https://127.0.0.1:YOUR_PORT --project-id evaluation-notes \
  --token-file /recipient/private/platform-token \
  --ca-file /evaluation/state/tls/ca.crt --connect \
  --write-probe --disposable-project-id evaluation-notes
```

It leaves one clearly labeled context record if allowed. With
`--require-read-only`, only a real gateway write refusal passes this optional
check. Do not select a real project as disposable. There is no automatic retry,
record deletion, policy update or credential minting. Lost-token replacement
continues to require explicit revoke/replacement and new one-time delivery.

The console's **Machine clients → View client → Connect this client** provides
the same offline recipe and staged checklist through its current session-only
inspection permission. It never accepts a bearer token or runs a live bearer
probe in the browser. The displayed client ceiling and actual credential may
differ; terminal doctor verifies the credential separately. Human sessions and
machine credentials remain separate.

Client syntax was checked against current official documentation on 27 October
2026: [Codex MCP](https://developers.openai.com/codex/mcp/),
[Claude Code MCP](https://code.claude.com/docs/en/mcp), and
[Gemini CLI MCP](https://geminicli.com/docs/tools/mcp-server/).
The existing stdio and Streamable HTTP shapes are preserved. Full release
reconciliation remains open.
