# Client profiles

Client profiles are reusable onboarding templates for the shared MCP gateway.
They describe scopes and small operational metadata; each connected client still
has its own identity, credential, project grants, and effective authorization.
Codex, Claude Code, Gemini CLI, and other MCP clients use the same backend.

## Durable model and assignment

`client_profiles` is a strict, revisioned MongoDB collection. A profile records
`profile_id`, name, description, scopes, optional `rate_limit_profile`, bounded
metadata, and active/disabled status. It stores no token, provider credential,
prompt, or arbitrary nested configuration.

`agent_clients.profile_id` is nullable. Assigning a profile snapshots its scopes
onto the client; it does not create policy inheritance. An administrator can
supply a strictly narrower scope override and a project override that narrows the
client's project grant. Later profile edits do not widen existing clients or
already-issued credentials.

Gateway authentication continues to intersect credential, client, scope, and
resource grants. `rate_limit_profile` is descriptive onboarding metadata; the
existing per-client gateway settings enforce rate limits.

## Recommended templates

The application offers five non-persistent templates. Operators choose whether
to save or assign one; startup does not automatically create profiles or clients.

| Profile | Intended use |
| --- | --- |
| `coding-agent-default` | Coding retrieval and context/memory writes |
| `coding-agent-readonly` | Read-only technical retrieval |
| `research-agent` | Research acquisition without approval/promotion authority |
| `documentation-agent` | Documentation retrieval and context/memory updates |
| `creator-agent` | Planning and drafting without publication authority |

`coding-agent-default` contains `skills:read`, `projects:read`, `context:read`,
`context:write`, `memory:read`, `memory:write`, `search:read`, `research:read`,
`research:write`, `agent:self`, `session:write`, and `audit:self:read`.

Recommended templates omit `agent:manage`, `research:approve`, `research:promote`,
and `content:publish`. The platform has no broad `admin` scope; client and
credential administration uses the explicit `agent:manage` capability.

## Administrative API

All endpoints require `agent:manage`; none is a normal agent MCP tool.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v5/client-profiles` | Create a profile |
| `GET /api/v5/client-profiles` | Cursor-page profiles |
| `GET /api/v5/client-profiles/{profile_id}` | Read a profile |
| `PATCH /api/v5/client-profiles/{profile_id}` | Revision-guarded update |
| `POST /api/v5/client-profiles/{profile_id}/assignments` | Assign to an existing client |

Assignment includes `client_id`, the client's `expected_revision`, and optional
`scope_overrides` and `project_overrides`. Scope overrides must be a subset of the
profile scopes. A supplied project list restricts an unrestricted client or
narrows an existing allowlist. Concurrent edits return a revision conflict.

## Credential and recovery boundaries

A client type such as `gemini_cli` is provenance/diagnostic metadata. It does not
select a different data store or permission implementation. Every installation
needs a dedicated Agent Context credential; provider credentials and another
client's token are separate.

Migrations 33 and 34 add nullable profile assignment metadata and backfill profile
description/status fields without changing existing client grants. Backups cover
all collections, including `client_profiles`. Migration rollback intentionally
refuses to remove durable assignment history.

Follow [client-onboarding.md](client-onboarding.md) for registration and transport
configuration, or the [Claude Code](clients/claude-code.md) and
[Gemini CLI](clients/gemini-cli.md) guides for their client settings.
