# Contributing

Bug reports, focused fixes, documentation improvements, and reviewed skills are
welcome. Use the public repository's issue tracker for ordinary bugs and feature
requests. Follow [SECURITY.md](SECURITY.md) for vulnerabilities or leaked secrets.

## Local development

Use Python 3.12 or newer and install the pinned development environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock \
  --extra-index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install --no-deps -e .
```

Follow the [README](README.md) to run the Compose stack when working on database,
embedding, search, or remote MCP behavior. The standard unit suite runs without
vendor credentials or a paid model call.

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy app
```

Run meaningful focused tests while editing, then these checks before submitting.
For a database or search change, also run the relevant integration tests in a
disposable environment. Compose uses explicitly named volumes and networks, so a
different Compose project name alone does not isolate the default deployment.
Use a separate host or an override that changes database, volume, network, and
port names; never run destructive fixture tests against your real knowledge
database. Follow [deployment](docs/deployment.md) for configuration details.

## Changes to the platform

Keep routes, MCP tools, and API-backed CLI commands thin. Put lifecycle,
authorization, and provenance rules in services; persistence and query filters
belong in repositories. The [architecture](docs/architecture.md) and
[onboarding guide](docs/onboarding.md) describe the boundaries.

Schema changes need an idempotent migration that preserves existing records and
strict validators. Test permissions, project/profile/client isolation, stale
revisions, retries, and safe dependency failures when those behaviors are
affected. Keep transport tools capability-based so equivalent clients share the
same backend and knowledge.

Canonical skills live in `skills/`. Follow the
[frontmatter and version policy](docs/skills.md): changed skill content needs a
new semantic version. Validate the catalog before submitting skill edits:

```bash
.venv/bin/python scripts/skill_catalog.py validate
```

Document user-visible changes and update examples alongside interface changes.
For guide or website changes, follow [website.md](docs/website.md) and rebuild
the generated HTML. Submit a focused pull request describing the problem, the
resulting behavior, relevant verification, and any remaining limitation.

## Privacy and licensing

Use fictional sample organizations and reserved example domains. Keep tokens,
`.env`, private project facts, database exports, model caches, and private working
notes out of commits. Audit selected release files before creating an archive:

```bash
.venv/bin/python scripts/prepare_open_source_release.py --check
```

Contributions to this repository are submitted under its [MIT License](LICENSE).
Only contribute material you are authorized to share. Preserve third-party
licenses and attribution; linking a source does not grant permission to copy it.
