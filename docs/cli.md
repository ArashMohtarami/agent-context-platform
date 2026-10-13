# contextctl

The operator's interface to the platform. Codex reaches it through MCP and an
application reaches it through the API; `contextctl` is how a person does.

```bash
contextctl health
contextctl search "how do we prevent duplicate payments?" --project-id payment-service
contextctl --json memory search "typing conventions" | jq .results
```

Every command is a thin translation: parse arguments, call one service or one endpoint, print the
result. Nothing here ranks, embeds, or decides what a project may see.

## Two kinds of command

The difference is visible in what each one needs, and it is not arbitrary.

| Command | Reaches | Because |
| --- | --- | --- |
| `skills list` | the API | it is a read the API already serves |
| `projects create`, `projects list` | the API | same |
| `creator show`, `creator update`, `creator pillars` | the API | profile authorization and optimistic updates live there |
| `content ideas`, `content idea create`, `content brief`, `content novelty`, `content repurpose`, `content export` | the API | discovery, provenance, authorization, and platform formatting live there |
| `search`, `memory search` | the API | retrieval lives behind it; a second copy would drift |
| `health` | the API | readiness is the API's own answer |
| `skills import` | MongoDB | no endpoint exposes it, and only an operator should do it |
| `skills reindex` | MongoDB + mongot | same |
| `keys create`, `keys list`, `keys revoke` | MongoDB | operator bootstrap and key administration require private database access |
| `keys scopes` | nothing | it describes the platform's own vocabulary |
| `backup`, `restore`, `backups` | MongoDB | same, and the tools run beside it |

So a `search` never fails for want of MongoDB credentials, and an `import` never fails for want of
an API address. Each command reads only the configuration it actually uses.

It also decides **where you run it**. The API publishes a port, so the commands that ask it run
from anywhere. MongoDB does not — under Compose it is on an `internal` network — so the commands
that reach it run inside the platform:

```bash
docker compose exec -T context-api python -m app.cli.contextctl keys list
docker compose run --rm context-api python -m app.cli.contextctl skills import
```

`python -m app.cli.contextctl` rather than `contextctl`: the image installs the application's
dependencies but not the project, so the console script exists only where you have run
`pip install -e .`. The syntax below is written bare for readability; prefix it accordingly.

## Commands

### skills

```bash
contextctl skills list [--status active] [--category backend] [--tag python] [--limit 20]
contextctl skills import [--directory ./skills] [--no-embeddings]
contextctl skills reindex [--no-embeddings]
```

`import` is idempotent: unchanged content is a no-op, changed content under the same version is
refused, and a new version supersedes the previous one while keeping its history.

`reindex` reconciles all six search indexes. They are derived state — not backed up, always
rebuildable — so this is what you run after a restore, or after changing the embedding model. It is
idempotent; a healthy deployment reports `unchanged`. With `--no-embeddings` it rebuilds the three
lexical indexes alone, which is what you want when the model is unavailable but search should still
work.

### skill proposals

```bash
contextctl skill proposal list --research-project-id retry-evidence [--skill-id backend-guidance]
contextctl skill proposal show --research-project-id retry-evidence --proposal-id idempotency-guidance
contextctl skill proposal export --research-project-id retry-evidence --proposal-id idempotency-guidance
```

These are read/export-only clients for generated skill-update proposals. `export` prints the stored Markdown patch for a separate human review process; it does not write canonical Markdown, call Git, or invoke the skill importer.

### projects

```bash
contextctl projects create --project-id payment-service --name "Payment Service" \
  --description "Card and wallet payments" \
  --repository git@example.invalid:sampleco/payment-service.git \
  --environment production --tag payments
contextctl projects list [--limit 20] [--cursor …]
```

The identifier is permanent: it derives the document key, so it cannot be taken twice and nothing
moves between projects.

### project bindings

```bash
contextctl project binding add --project-id payment-service --type git_remote \
  --value git@github.com:example/payment-service.git
contextctl project binding list [--project-id payment-service] [--limit 20] [--cursor …]
contextctl project binding delete payment-remote --revision 1
```

Bindings are the explicit deterministic mapping from a local repository or
workspace hint to a V1 project. Repository remotes are normalized without any
network or filesystem access, so standard SSH and HTTPS forms identify the
same repository. Creating a duplicate is refused; a mapping already owned by
another project is a conflict. Deletion requires the revision shown by `list`,
so an operator cannot silently remove a binding another operator changed.

### search

```bash
contextctl search "Implement payment refund support." --project-id payment-service
contextctl search "wallet:read" --source active_skill --limit 3
contextctl search "refund rules" --project-id payment-service --content
```

Skills, the project's own recorded context, and memory searched together and ranked against each
other. `--content` prints each result in full; without it you get the ranking and what it cost.

Naming no project excludes project context and project memory. Global knowledge
remains eligible; a client-linked credential can also search its own operational
agent memory. Projects are requested explicitly and never inferred.

### memory

```bash
contextctl memory search "typing conventions"
contextctl memory search "refund review" --project-id payment-service
contextctl memory search "deploys" --status superseded
```

Naming a project searches its memory and global memory together. Archived and superseded memory is
excluded unless asked for.

### health

```bash
contextctl health          # is the platform ready?
contextctl health --live   # is the process alive?
```

Exits `3` when the platform is not ready, so a deployment script can gate on it:

```bash
contextctl health || exit 1
```

### creator

```bash
contextctl creator show --profile-id personal-brand
contextctl creator update --profile-id personal-brand --expected-revision 2 \
  --status inactive --audience "software architects"
contextctl creator pillars --profile-id personal-brand --limit 20
```

Creator commands are API clients. They never read MongoDB directly and creator
preference data is never treated as hidden system instructions. `update` sends only named fields
and requires the revision read from `show`, so a concurrent update is refused rather than replaced.

### content

```bash
contextctl content ideas --profile-id personal-brand --project-id payment-service \
  --pillar-id payments --platform linkedin --since 2026-09-01T00:00:00Z
contextctl content idea create --profile-id personal-brand --idea-id payment-idempotency \
  --title "Idempotent payment retries" --description "Explain the stored retry rule." \
  --platform linkedin --origin-type project_context --origin-source-id 0123456789abcdef01234567 \
  --project-id payment-service --priority 0.9
contextctl content brief --profile-id personal-brand --idea-id payment-idempotency --platform medium
contextctl content novelty --profile-id personal-brand --title "Monitoring Outbox lag" \
  --description "Explain alerting and exhausted retries." --tag outbox --project-id payment-service
contextctl content repurpose --profile-id personal-brand --content-id outbox-medium \
  --target linkedin --target github
contextctl content export --profile-id personal-brand --content-id outbox-medium --mode markdown
contextctl content export --profile-id personal-brand --content-id architecture-readme \
  --mode file --output ./README.md
```

`content ideas` prints source-backed candidate topics and saves nothing. `content idea create` is
the explicit reviewed write, including all cited origin IDs. `content brief` prepares an evidence
package and saves nothing; it does not generate a draft or publish a result. These commands call the
V2 API only, so profile/project authorization and provenance checks remain centralized.

`content novelty` prints advisory, explainable overlap with only history that
the caller may access. `content repurpose` prints platform-specific source and
skill planning fields, never a generated body or saved draft. Both also call
only the V2 API.

`content export` calls the API to authorize, validate, and format one existing
content item. `stdout` and `markdown` print the returned representation. `file`
requires an explicit `--output PATH` and writes UTF-8 only on the local CLI
host; an existing file is refused unless `--force` is also passed. This is a
manual export, never an external publish. See
[content-export.md](content-export.md) for platform representations and safety
rules.

### research

```bash
contextctl research create --research-project-id retry-evidence --name "Retry evidence" \
  --question "What constrains safe payment retries?" --type technical_research \
  --related-project-id payment-service
contextctl research search --research-project-id retry-evidence --query "idempotency retry"
contextctl research search --research-project-id repository-evidence --query "authenticate" \
  --retrieval-profile research_code --method lexical
contextctl source ingest --source-id payments-repository --fetch-repository
contextctl research brief --research-project-id retry-evidence --brief-id retry-summary \
  --scope "Provider documentation and incident reports" \
  --next-question "Which SDK versions enforce the retry requirement?"
```

`contextctl source ingest --fetch-repository` sends only the registered source identifier; it does not take a repository URL, path, ref, credentials, or command. The server’s Git provider applies the public-HTTPS, size, and read-only execution controls.

`contextctl research brief` asks the API to persist structured claim, citation, source,
contradiction, uncertainty, and next-question references. It does not generate prose, select a
side of conflicting evidence, claim completeness, fetch a source, or promote any result into
memory, project context, or a skill.

### candidate

```bash
contextctl candidate list --research-project-id retry-evidence --status pending_review
contextctl candidate approve --research-project-id retry-evidence --candidate-id retry-context \
  --expected-revision 1 --reason "Reviewed retained evidence"
contextctl candidate reject --research-project-id retry-evidence --candidate-id retry-context \
  --expected-revision 1 --needs-changes --reason "Narrow the target statement"
```

Candidates retain the exact claim and citation IDs that motivated a proposal. `approve` and `reject` are explicit review decisions only: they create an immutable decision event, but never write V1 memory, project context, skills, or content. `--needs-changes` is a rejection-path review state, not a promotion.

Promote an explicitly approved candidate through the authorized V1 target:

```bash
contextctl candidate promote \
  --research-project-id retry-evidence \
  --candidate-id retry-context \
  --expected-revision 2 \
  --supersedes-context-id 0123456789abcdef01234567
```

The optional `--supersedes-context-id` is only for project-context promotion. The server requires the appropriate V1 target write scope, creates the replacement first, marks that explicit old context `superseded`, retains evidence provenance on the new target, and refuses skill changes.

### keys

```bash
contextctl keys scopes
contextctl keys create --name codex --scope search:read --scope skills:read
contextctl keys create --name payments-agent \
    --scope search:read --scope context:read --scope context:write \
    --project payment-service --expires-in-days 90
contextctl keys list [--status active] [--limit 20]
contextctl keys revoke --key-id 0123456789abcdef
```

This is the only way a key comes into existence. There is deliberately no endpoint for it: one
would need a key to call it, and a key that could mint keys could widen itself without limit.

**The key is printed once.** The platform stores a hash, so a lost key cannot be recovered — issue
a new one and revoke the old. `--json` carries it too, so a deployment script can put it straight
into a secret store rather than a person copying it out of a terminal:

```bash
contextctl --json keys create --name ci --scope search:read | jq -r .key > ./secrets/ci-api-key
```

Naming no `--project` grants every project, which is the widest grant and so the one you choose by
leaving the option out. Omitting `--expires-in-days` issues a key that does not expire.

The same convention applies to `--profile`: include it one or more times to narrow a key to V2
creator profiles; omit it to grant every creator profile. See [creator.md](creator.md) for the
V2 resource scopes and examples.

`revoke` keeps the record rather than deleting it, so the audit trail still has a name to attach
past decisions to. A revoked key authenticates nothing from that point on, immediately.

`keys create` does not require search to be working — only the database. Needing a healthy search
coordinator to issue a credential would mean authentication could not be bootstrapped on a platform
whose search is degraded, which is when you are most likely to need to. `keys scopes` needs nothing
at all and answers wherever `contextctl` is installed.

### backup, restore, and backups

```bash
contextctl backup                        # into the configured directory, with retention
contextctl backup --no-prune             # keep this one regardless of retention
contextctl backup --output ./one-off.archive.gz   # a path you named; unmanaged

contextctl backups list
contextctl backups verify [--all] [--restore]
contextctl backups prune [--dry-run]

contextctl restore --latest --drop --yes
contextctl restore --name context-20260916-131532.archive.gz --yes
contextctl restore --input ./one-off.archive.gz --yes
```

A bare `backup` writes into `BACKUP_DIRECTORY`, records a manifest beside the archive, and applies
the retention policy — 7 daily, 4 weekly, 3 monthly by default. `--output` is the unmanaged form: a
one-off copy before a risky migration, which should not join the retained set and push a real backup
out of it.

`backups verify` checks the archive against the checksum written with it and that `mongorestore` can
read it. `--restore` goes further and actually restores it into throwaway collections, comparing the
document counts with the manifest — the only check that proves a backup restores. It exits non-zero
if any backup fails, so a scheduler can alert on it.

See [backup.md](backup.md) for the strategy, the scheduling, and the disaster-recovery procedure.

**Search indexes are not backed up.** They are derived state that `contextctl skills reindex`
rebuilds from the stored documents, and treating them as a backup would be treating a cache as a
record. The restore output says so.

`restore` requires `--yes`. It is not a prompt — it stays scriptable — but nothing overwrites a
database because a command was typed with one word missing. `--drop` replaces each collection
rather than merging into it.

The full procedure:

1. Stop the API.
2. `contextctl restore --input … --drop --yes`
3. `contextctl skills reindex`
4. Start the API.
5. `contextctl health`

The production and test images include the matching MongoDB Database Tools, so the CLI and
`mongodump`/`mongorestore` coexist without publishing MongoDB. Take scheduled backups in the
running API container. Run a disaster restore through `scripts/restore.sh`, which uses a one-off
container while the serving API remains stopped. A managed restore verifies the manifest,
checksum, archive readability, target database, and restored collection counts.

## Output

Human-readable by default, `--json` for anything that will be parsed:

```bash
contextctl --json health | jq '.failing'
contextctl --json search "refunds" --project-id payment-service | jq '.results[].title'
```

`--json` is a global flag and goes before the command. Results go to **stdout** and failures to
**stderr**, both as JSON when `--json` is set, so a failure never lands in the pipe:

```json
{ "error": "the platform is unreachable", "exit_code": 3, "hint": "… contextctl health" }
```

Nothing is interactive. Every command runs unattended.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | the command succeeded |
| `1` | the command ran and did not succeed — a record was absent, a write conflicted |
| `2` | the arguments were wrong |
| `3` | the platform could not be reached, or is not ready |

`1` and `3` are deliberately different: one means the command was wrong, the other means nothing is
listening. A retry loop should retry `3` and give up on `1`.

## Configuration

Through the environment, like everything else.

| Variable | Purpose | Default |
| --- | --- | --- |
| `CONTEXTCTL_API_BASE_URL` | where the Context API listens | `http://127.0.0.1:8000` |
| `CONTEXTCTL_API_TOKEN_FILE` | file holding a bearer token | unset |
| `CONTEXTCTL_API_TOKEN` | the token inline; prefer the file | unset |
| `CONTEXTCTL_REQUEST_TIMEOUT_SECONDS` | per-request timeout | `30` |
| `MONGODB_*` | only for `import`, `reindex`, `keys`, `backup`, `restore` | see deployment.md |

`--api-base-url` and `--timeout` override the first and last for one invocation.

The CLI settings are separate from the MCP server's on purpose: an operator's terminal and an
agent's tool server are different callers, and pointing one at another deployment should not move
the other.

### Secrets

No command accepts a password or a token as an argument, and a test asserts that none ever grows
one. Both are read from the environment or from a file, because argv is readable by every process
on the host through `/proc` and ends up in shell history and CI logs. `keys create` prints a key
rather than taking one, which is the same rule from the other side.

Prefer `CONTEXTCTL_API_TOKEN_FILE` over `CONTEXTCTL_API_TOKEN` for the same reason.

`backup` and `restore` pass the MongoDB password to the database tools through a private
configuration file, created at mode `0600` before anything is written to it and removed when the
command finishes. It is never in argv, never in their environment, and never printed.

## Troubleshooting

**`this command needs the platform's database settings, and they are not set`** — you ran a
MongoDB-reaching command (`skills import`, `skills reindex`, `keys`, `backup`, `restore`) from the
host. The local ignored `.env` is written for **Compose**, which maps `MONGODB_APP_USERNAME` to the
`MONGODB_USERNAME` the application reads — so the file that looks like it configures everything
configures a different set of names. And under Compose the database is on an `internal` network
with no host port, so it is unreachable from the host anyway. Run these inside the platform:

```bash
docker compose exec -T context-api python -m app.cli.contextctl keys list
```

`python -m app.cli.contextctl` rather than `contextctl`: the image installs the application's
dependencies but not the project, so the console script is not in it.

**`the platform is unreachable`** — nothing is listening at `CONTEXTCTL_API_BASE_URL`. Check the
address and that the API is up. The CLI deliberately ignores inherited `HTTP_PROXY`, `HTTPS_PROXY`,
and `ALL_PROXY` values because the self-hosted API address is explicit.

**`the platform is not ready`** — the API answered but a capability is failing. The message names
which; `contextctl --json health | jq .failing` lists them.

**`mongodump was not found`** — the MongoDB Database Tools are not on `PATH`. Run the command in
the mongodb container, as above.

**`skills list` is empty** — skills are imported, not seeded: `contextctl skills import`.

**A search returns nothing right after a write** — `mongot` indexes asynchronously, usually within
a second. If it persists, `contextctl health` will say whether the index is queryable.

**`the arguments were rejected as invalid`** — the API refused the request, and its own message
follows. `--scope project` without `--project-id` is the common one.

## Installing

The console script is declared in `pyproject.toml`, so an editable install puts `contextctl` on
`PATH`:

```bash
pip install -e .
contextctl --help
```

Without installing, it runs as a module:

```bash
python -m app.cli.contextctl --help
```

## Verification

```bash
python -m pytest                                     # parsing, execution, exit codes, JSON
docker compose --profile test run --rm --build test  # live mongod + mongot checks
```

Parsing is tested against the full command tree — every required command, every refusal, and that
no command has grown a secret-bearing flag. Execution is tested by running whole invocations
in-process against a recording HTTP double, which is what makes the exit codes themselves
assertable. Backup and restore run a real subprocess so the claim about the process table is proved
rather than asserted.
