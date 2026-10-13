# Creator profiles and content pillars

Creator profiles store communication preferences, while content pillars group
themes within a profile. The API supports profile/pillar CRUD, profile-scoped
authorization, thin MCP tools, and API-backed `contextctl` commands.
See [content-skills.md](content-skills.md) for writing guidance,
[content-storage.md](content-storage.md) for drafts and versions, and
[content-discovery.md](content-discovery.md) for planning.

## Preference data, never hidden instructions

A creator profile is structured data about how its owner prefers to communicate.
It may contain audiences, domains, structured voice tone/avoidance values,
writing, formatting, terminology, language, and tone preferences, topics of
interest or avoidance, and content objectives. It has no prompt or executable
instruction field. MCP labels every returned profile as retrieved data, not
instructions.

Profiles have `active`, `inactive`, or `archived` status. Inactive and archived
profiles remain readable to an authorized caller. Profiles do not generate
prose; the calling agent interprets them as preference data.

Pillars are profile-owned, revisioned themes. A pillar has an identifier, name,
description, tags, 0..1 priority, and active/archived status. A pillar cannot
be created or updated outside its explicit `profile_id` boundary.

## API

All endpoints are under `/api/v2` and use the existing bearer-key
authentication.

| Method | Path | Scope | Notes |
| --- | --- | --- | --- |
| `GET` | `/creator-profiles` | `creator:read` | Cursor-paginated; only granted profiles appear. |
| `POST` | `/creator-profiles` | `creator:write` | Creates a deterministic `profile_id`. |
| `GET` | `/creator-profiles/{profile_id}` | `creator:read` | Requires an explicit profile grant. |
| `PATCH` | `/creator-profiles/{profile_id}` | `creator:write` | Requires `expected_revision`. |
| `GET` | `/content-pillars?profile_id=…` | `creator:read` | Cursor-paginated inside exactly one profile. |
| `POST` | `/content-pillars` | `creator:write` | Profile must already exist. |
| `PATCH` | `/content-pillars/{pillar_id}` | `creator:write` | Body contains `profile_id` and `expected_revision`. |

`PATCH` operations are optimistic: a stale `expected_revision` returns `409`
rather than overwriting a concurrent change. The API never accepts a
`project_id` for these resources, so V1 project context cannot be implicitly
attached or disclosed through these endpoints.

## Profile grants

`creator:read` and `creator:write` authorize the resource type. An API key can
also be narrowed to creator profiles with repeatable `--profile`:

```bash
contextctl keys create --name personal-brand-agent \
  --scope creator:read --scope creator:write \
  --profile personal-brand
```

Omitting `--profile` retains the established V1 convention of an unrestricted
grant. A restricted key receives neither a profile listing entry nor a
successful lookup for an ungranted profile. The same scope names also include
separate `content:read` and `content:write` grants for briefs, drafts, and
content items.

MCP has an independent, optional `MCP_PROFILE_IDS` allow-list. It is checked
before an MCP request reaches the API, then the API key is checked again. The
narrower of the two grants wins.

## MCP tools

| Tool | Scope | Operation |
| --- | --- | --- |
| `get_creator_profile` | `creator:read` | Read one profile as data. |
| `update_creator_profile` | `creator:write` | Revision-guarded partial update. |
| `list_content_pillars` | `creator:read` | Page pillars in one explicit profile. |

The MCP server remains a thin HTTP client. It does not connect to MongoDB,
make lifecycle decisions, or turn profile preferences into instructions.

## CLI

The commands call the V2 API; they do not duplicate service or MongoDB logic.

```bash
contextctl creator show --profile-id personal-brand
contextctl creator update --profile-id personal-brand --expected-revision 3 \
  --status inactive --audience "software architects"
contextctl creator pillars --profile-id personal-brand --limit 20
```

`creator update` sends only named changes. Supplying the structured voice
fields requires both `--voice-tone` and `--voice-avoid`, preventing an
accidental replacement of half the voice object.

## Migration and verification

Migration 7 backfills empty `tone_preferences` on existing creator profiles and
empty `profiles` allow-lists on existing API keys, then reconciles strict
validators for `creator_profiles`, `api_keys`, and `audit_logs`. It is
idempotent and never renames or drops a V1 collection. Existing keys retain
their unrestricted behavior because an empty profile list means all profiles.

Focused verification:

```bash
.venv/bin/python -m pytest tests/unit/test_creator_service.py \
  tests/unit/test_creator_api.py tests/unit/test_creator_mcp.py \
  tests/unit/test_cli_creator.py tests/integration/test_creator_phase1.py -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
```
