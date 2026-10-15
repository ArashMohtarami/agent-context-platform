# Agent Context Platform

Self-hosted knowledge for coding agents. Store reviewed skills, project decisions,
intentional memories, research evidence, and content history once, then retrieve
them through a shared MCP gateway or REST API.

Codex, Claude Code, Gemini CLI, and compatible MCP clients use the same knowledge
and authorization rules. Each client can have its own credential and project
grants. Embeddings run locally; MongoDB and MongoDB Community Search provide
lexical, vector, and hybrid retrieval.

[Interactive usage guide](docs/usage-guide.html) · [Architecture](docs/architecture.md) ·
[Self-host the introduction website](docs/website.md) · [Contributing](CONTRIBUTING.md)

## What you can do

- **Retrieve project knowledge:** search Git-versioned skills, one project's
  recorded context, and allowed memories together within a token budget.
- **Share decisions across agents:** keep actor provenance, explicit project
  bindings, client scopes, and revision checks for concurrent edits.
- **Save memories intentionally:** use global, project, agent, or project-agent
  scopes; archive or supersede records while preserving history.
- **Research with evidence:** ingest bounded text, Markdown, PDF, public web
  pages, and public repositories; retain snapshots, claims, citations, and
  contradictions. Review and promote approved findings explicitly.
- **Prepare technical content:** manage creator preferences, ideas, briefs,
  drafts, immutable versions, review, and manual export. The calling agent writes
  prose. The included publisher records a human-confirmed publication receipt.
- **Operate your own service:** use authenticated APIs, structured logs, metrics,
  backups, verified restores, and local embedding models.

The repository includes a [skill catalog](docs/skill-catalog.md), a fictional
[payment service knowledge example](examples/payment-service/README.md), a
[small Notes API](examples/starter-notes-api/README.md), and an isolated
[async Django example](examples/django-async/README.md).

V6 setup requires explicit [legacy adoption](docs/tenant-adoption.md) and
[tenant isolation cutover](docs/tenant-isolation.md) before domain traffic is enabled.
The workflows below assume completed setup and organization-bound credentials.
Provisioning, import, and administrative commands require `--org-id`; human
management dashboards remain disabled.

## Quick start

Run from this checkout. You need Docker with Compose v2, Python 3.12+, OpenSSL,
and `make`. The credential example below also uses `jq`. A small deployment
should have several GB of available RAM; MongoDB, search indexes, and the local
model each consume memory. See [deployment](docs/deployment.md) and
[production readiness](docs/production-readiness.md) before sizing a host.

```bash
python3.12 -m venv .venv
./scripts/generate-dev-secrets.sh
./scripts/prepare-docker-wheels.sh
docker compose config --quiet
docker compose up -d --build --wait
curl --fail http://127.0.0.1:8000/health/ready
make import-skills
```

The secrets generator creates an ignored `.env` and database secret files without
printing their values. You can instead configure `.env.example` and provision
the referenced files yourself. The wheel script downloads the pinned Python
dependencies into an ignored build cache; rerun it when lock files change.
The first startup may download the default local embedding model. Subsequent
starts reuse the model volume. Offline deployments need preloaded wheels,
container images, and model files.

Only the API is bound to the host, on `127.0.0.1:8000`. MongoDB and `mongot` stay
on the private Docker network. The health endpoints need no credential;
knowledge APIs and metrics require scoped keys.

Create a read-only key inside the service, where the private database is reachable:

```bash
umask 077
docker compose exec -T context-api python -m app.cli.contextctl --json keys create \
  --name local-reader --scope skills:read --scope search:read \
  | jq -er .key > ./secrets/local-reader-key
```

Install the host CLI and query the imported skills:

```bash
.venv/bin/python -m pip install -r requirements-dev.lock \
  --extra-index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install --no-deps -e .
export CONTEXTCTL_API_BASE_URL=http://127.0.0.1:8000
export CONTEXTCTL_API_TOKEN_FILE="$PWD/secrets/local-reader-key"
.venv/bin/contextctl search "safe async database transactions"
```

The key grants reading and searching skills. To create projects or store context,
issue the corresponding write scopes and explicit project grants, following the
[walkthrough](docs/usage-guide.html#16-from-zero-to-a-project-and-reviewed-content).
Avoid granting publication, research approval, promotion, or client administration
to an agent that only needs coding assistance.

Stop the stack while retaining its volumes with `docker compose down`.

## Connect an agent

Start the platform and import skills first, then follow the relevant guide:

| Client | Connection | Setup |
| --- | --- | --- |
| Codex | Local stdio launcher | [Codex integration](docs/codex.md) |
| Claude Code | Streamable HTTP `/mcp` | [Claude Code integration](docs/clients/claude-code.md) |
| Gemini CLI | Streamable HTTP `/mcp` | [Gemini CLI integration](docs/clients/gemini-cli.md) |
| Other MCP clients | Shared capability contract | [Client onboarding](docs/client-onboarding.md) |

Use a separate platform credential for each client. Remote access requires HTTPS;
configure a reverse proxy rather than exposing database or search ports. Provider
API keys are separate from Agent Context credentials. Local embeddings keep this
platform's embedding work on your host; your chosen agent can still send retrieved
context to its own model provider.

A useful agent workflow is to resolve an explicit project, call `search_knowledge`
for the current task, inspect relevant sources, and save only verified decisions
or requested memories. Retrieved text is labeled data. The server does not
automatically store entire conversations or treat research results as approved
knowledge.

## Documentation

The [HTML usage guide](docs/usage-guide.html) opens directly in a browser and works
offline. Its editable sources and runnable walkthroughs live in
[docs/guide](docs/guide). The [introduction website](website/) can be built and
served independently on a public IP; see [website deployment](docs/website.md).

| Topic | Guide |
| --- | --- |
| Architecture and code navigation | [Architecture](docs/architecture.md), [codebase onboarding](docs/onboarding.md) |
| Storage and retrieval | [Database](docs/database.md), [skills](docs/skills.md), [embeddings](docs/embeddings.md), [retrieval](docs/retrieval.md) |
| Projects and memories | [Projects](docs/projects.md), [memory](docs/memory.md), [unified search](docs/search.md) |
| Interfaces | [MCP](docs/mcp.md), [CLI](docs/cli.md), [client profiles](docs/client-profiles.md) |
| Evidence-backed research | [Research workflow](docs/research.md) |
| Technical content | [Creator profiles](docs/creator.md), [storage](docs/content-storage.md), [publication review](docs/content-publication-safety.md), [manual publishing](docs/content-publishing.md) |
| Operations | [Deployment](docs/deployment.md), [security](docs/security.md), [backup and recovery](docs/backup.md), [observability](docs/observability.md) |
| V6 maintenance foundation (domain traffic stays sealed after adoption) | [Tenant adoption](docs/tenant-adoption.md) |
| Licenses and dependency notices | [MIT License](LICENSE), [third-party notices](docs/third-party-notices.md) |

## Development and verification

After installing the development dependencies above:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy app
```

The default suite requires no vendor account or paid model call. Tests that need
a real embedding model are opt-in. To test MongoDB and search integration, use a
disposable Compose deployment with its own database and volumes:

```bash
make verify-mongodb
make verify-mongot
docker compose --profile test run --rm --build test
```

The integration suite writes fixtures; use an isolated environment as described
in [Contributing](CONTRIBUTING.md). The default Compose deployment is one API
process, one database replica-set member, and one search process. It provides
durability through backups, with no automatic host failover. Load-test your own
corpus before choosing resource limits.

## License

The project's original code, documentation, skills, and examples are available
under the [MIT License](LICENSE). MongoDB, `mongot`, Python dependencies, model
weights, and material you ingest retain their own licenses. See
[third-party notices](docs/third-party-notices.md) for these boundaries.
