# Agent Context Platform

Self-hosted knowledge for coding agents and an organization console for people.
Store reviewed skills, project decisions, intentional memories, research evidence
and content history, then retrieve them through MCP, versioned bearer APIs or
session-authenticated human workflows.

Codex, Claude Code, Gemini CLI and compatible clients share the same services.
Organizations have explicit ownership and action/resource grants. Embeddings run
locally; MongoDB and MongoDB Community Search provide lexical, vector and hybrid
retrieval. Installation database and backup operators remain trusted across
organizations. **Release readiness is not yet approved:** see
[verification and known limits](docs/verification.md).

[Usage guide](docs/usage-guide.html) · [Console](docs/dashboard.md) ·
[Architecture](docs/architecture.md) · [Contributing](CONTRIBUTING.md)

## What you can do

- Retrieve permitted project context, organization-imported skills and memories
  within explicit source filters and token budgets.
- Share verified decisions across agents with actor provenance, bindings,
  immutable history and revision-aware updates.
- Save memories intentionally in organization-global, project, agent or
  project-agent scope; private client ownership remains enforced.
- Manage invited people, teams, bounded delegation, access requests and sponsored
  clients through the [organization console](docs/organization-management.md).
- Research bounded sources with snapshots, claims, citations, contradictions,
  independent review and separately authorized promotion.
- Prepare creator briefs/drafts and immutable versions. The caller writes prose;
  publication records a manual receipt and sends nothing to external platforms.
- Inspect authorized content-free monitoring and governance history, with declared
  coverage, event-time attribution and bounded expiring reports.

Roles do not bypass corpus policy. Read-A/write-B never becomes write-A; new
managed assignments default to NONE. Human cookies do not authenticate V1–V5/MCP,
and machine bearer credentials do not authenticate session-only `/api/v6`.

## Quick start

For a separate fictional organization, follow the
[disposable evaluation guide](docs/evaluation.md). It covers reviewed root intent,
private HTTPS trust, verified enrollment/MFA and recipient-owned credentials.
The current commissioning mode is model-free; it makes no retrieval-quality claim.

Start with the [HTTPS console deployment guide](docs/dashboard-deployment.md) and
[maintenance migration/recovery procedure](docs/migration-recovery.md). A fresh
checkout does not anonymously create an owner, adopt data or activate tenants.
The V6 flags are disabled by default. An operator must complete the staged
cutovers and verify their gates before admitting domain traffic.

Build inputs require Docker Compose 2.24.4+, Python 3.12+, OpenSSL and the pinned
Node toolchain for frontend development. From a reviewed checkout:

```sh
python3.12 -m venv .venv
./scripts/generate-dev-secrets.sh
./scripts/prepare-docker-wheels.sh
docker compose -f docker-compose.yml -f deploy/console/compose.yml build context-api console
```

The secrets helper creates ignored development files; it does not provide a
publicly trusted TLS certificate, local owner or production IdP configuration.
Follow the deployment guide for exact-origin HTTPS, mounted keys, local mail
handoff and optional external OIDC. Offline installations must preload pinned
images, Python wheels, npm packages and model files. Never expose database/search
ports or password/MCP administration over public HTTP.

After explicit adoption and activation, the operator imports reviewed Git skills
into the selected organization:

```sh
python -m app.cli.contextctl --org-id YOUR_ORG skills import --directory ./skills
```

Run operator commands inside the configured private API/operator container.
For managed agent access, approve bounded client policy, then have the verified
recipient generate their own token once in the console. Use a separate private
token file for each client; no other worker or lead can recover that plaintext.
Legacy `keys create` is not the managed issuance path.

## Connect an agent

| Client | Connection | Setup |
| --- | --- | --- |
| Codex | Local stdio launcher | [Codex](docs/codex.md) |
| Claude Code | Streamable HTTP `/mcp` | [Claude Code](docs/clients/claude-code.md) |
| Gemini CLI | Streamable HTTP `/mcp` | [Gemini CLI](docs/clients/gemini-cli.md) |
| Other clients | Shared capability contract | [Onboarding](docs/client-onboarding.md) |

Remote access uses the exact HTTPS console/API origin. Provider API keys are
separate from platform credentials. Current key, client, sponsor, team and
parent policy intersect per action/resource on ordinary REST and MCP preflight,
including already connected sessions. Retrieved text remains data; your chosen
agent may send retrieved context to its model provider.

## Documentation

The [HTML guide](docs/usage-guide.html) opens offline. Its source registry includes
maintained implemented docs; legacy bearer examples are labelled separately from
managed human workflows. The [marketing site](docs/website.md) is independently
hostable and has no console session, runtime data or secret mounts.

| Topic | Guide |
| --- | --- |
| Console and people | [Dashboard](docs/dashboard.md), [organization management](docs/organization-management.md) |
| Identity and grants | [Local/MFA](docs/local-identity.md), [SSO](docs/sso-identity.md), [policy](docs/management-policy.md), [managed clients](docs/managed-agents.md) |
| Architecture/storage | [Architecture](docs/architecture.md), [database](docs/database.md), [code navigation](docs/onboarding.md) |
| Knowledge | [Projects](docs/projects.md), [memory](docs/memory.md), [skills](docs/skills.md), [retrieval](docs/retrieval.md) |
| Research/creator | [Research console](docs/dashboard-research.md), [creator studio](docs/dashboard-creator.md), [publication safety](docs/content-publication-safety.md) |
| Monitoring | [Dashboard policy](docs/dashboard-monitoring.md), [authorized summaries](docs/authorized-summaries.md) |
| Interfaces | [MCP](docs/mcp.md), [CLI](docs/cli.md), [permission templates](docs/client-profiles.md) |
| Operations | [Console deployment](docs/dashboard-deployment.md), [migration/recovery](docs/migration-recovery.md), [backup](docs/backup.md), [security](docs/security.md) |
| Readiness and licenses | [Verification](docs/verification.md), [MIT License](LICENSE), [dependency notices](docs/third-party-notices.md) |

The repository includes a [skill catalog](docs/skill-catalog.md), fictional
[payment-service](examples/payment-service/README.md), [Notes API](examples/starter-notes-api/README.md)
and isolated [async Django](examples/django-async/README.md) examples.

## Development and verification

Install the exact development lock with the documented CPU wheel index before
running Python checks. Frontend source, Node pin, lockfile, scripts and notices
live in [`dashboard/`](dashboard/README.md).

```sh
python -m pytest tests/unit -q
python -m ruff check .
python -m mypy app
npm --prefix dashboard ci
npm --prefix dashboard run lint
npm --prefix dashboard run typecheck
npm --prefix dashboard run test:unit
npm --prefix dashboard run build
```

The [complete release gate](docs/verification.md) also needs real disposable
MongoDB/mongot, supported OIDC and all browser/MCP/client journeys. Required skips
and exceeded budgets remain unresolved. Test fixtures write data: never use live
volumes or real recipients. The supplied single-node topology has no automatic
host failover; recovery depends on independently stored backups and rehearsal.

## License

Original code, documentation, skills and examples use the [MIT License](LICENSE).
Dependencies, MongoDB/search images, model weights and ingested material retain
their own licenses. Frontend distribution includes source, lockfile, license
inventory and notices; generated builds, browser artifacts and private plans are
excluded from clean source exports.
