# Backup and recovery

Written for whoever will restore this platform, probably in a hurry, possibly
at night, possibly without having read this before.

**The short version.** MongoDB is the only thing that must survive. Take a
daily backup, keep 7 daily / 4 weekly / 3 monthly, and prove once a week that
one actually restores. The disaster-recovery procedure is at the end.

## What has to be backed up, and what does not

| | Backed up | Why |
| --- | --- | --- |
| **MongoDB** — skills, chunks, project context, memories, creator/content records, research projects, sources, snapshots, documents, chunks, notes, claims, citations, briefs, candidates, promotion events, ingestion jobs, projects, API keys, audit log | **Yes** | The platform's only primary state. Nothing else can reconstruct it. |
| **Vector embeddings** | **Yes** | They live inside the documents, so they come along — and that is deliberate. Regenerating them means running the embedding model over every chunk again: minutes to hours of CPU for something already computed. |
| **Search indexes** (all configured mongot indexes) | **No** | Derived state. `contextctl skills reindex` rebuilds them from the documents in seconds to minutes. |
| **The embedding model files** | No | Re-downloaded, or placed on the volume out of band for an air-gapped host. |
| **Secrets** (`secrets/`, `.env`) | Separately, by your secret manager | Not in a database backup, and never in the same place as one. |

The two rules worth stating plainly, because they pull in opposite directions
and it is easy to get one of them backwards:

- **Search indexes are not a source of truth.** Never restore *from* them, and
  never treat a healthy index as evidence the data is intact. They are a
  rebuildable projection of MongoDB.
- **Vectors are not an index.** They are data, they are expensive, and they are
  in the backup. A restore does not re-embed anything.

So a restore is: bring MongoDB back, then rebuild the indexes. Never the other
way around, and never only the first half.

## Where backups live

`BACKUP_DIRECTORY`, `./backups` by default. The directory is created `0700` and
every file in it `0600` — an archive is every document the deployment holds, in
one file, so the protection it gets is the protection the whole platform gets.

Under Compose it is a **host directory** bind-mounted at `/backups`, not a named
volume, so you can copy backups off the machine with `cp` or `rsync` without
going through Docker. The container writes as uid 999, so create it once:

```bash
mkdir -p backups
sudo chown 999:999 backups
chmod 700 backups
```

Point `BACKUP_HOST_DIRECTORY` somewhere else — another disk, a mounted share —
if the backups should not live beside the data they protect.

**Do not put it anywhere the platform serves.** Not under a web root, not in a
volume mounted into a container that answers requests, not in the repository.
It is not in the image, and `.gitignore` covers the default path. If you ship
backups off the host — and you should, a backup on the same disk survives a
deleted collection but not a dead disk — send them somewhere with the same
protection: private, encrypted at rest, and not the same failure domain.

Each backup is two files:

```
backups/
  context-20260916-131532.archive.gz     # mongodump archive, gzipped
  context-20260916-131532.manifest.json  # what it contains, and its checksum
```

The timestamp is UTC and fixed-width, so sorting by name is sorting by time,
and the instant a backup was taken travels with it — a file copied to another
host still expires on the right day, which a modification time would not.

The manifest records the database, the byte count, the SHA-256 of the archive,
and the document count of every collection. That last one is what makes a
restore checkable rather than assumed: a restore that reports success and
leaves `skills` empty has failed, and only the counts would say so.

## Taking a backup

```bash
docker compose exec -T context-api python -m app.cli.contextctl backup
```

Inside the platform, because a backup reaches MongoDB directly and under
Compose MongoDB is on an `internal` network with no host port. And
`python -m app.cli.contextctl` rather than `contextctl`, because the image
installs the application's dependencies but not the project itself.

That one command dumps the database, writes the manifest, verifies the checksum
and dry-run restore plan, and only then applies retention. A failed new archive
is removed and cannot evict a known older backup. The archive is written under
a temporary name and moved into place only when it is complete, so an
interrupted backup never becomes one a restore might later choose.

```bash
# Keep this one regardless of retention.
… contextctl backup --no-prune

# A one-off copy before a risky migration: exactly where you said, no manifest,
# not part of the retained set, so it cannot push a real backup out of it.
… contextctl backup --output ./before-migration.archive.gz
```

## Retention

The default keeps the newest backup of each of:

- the last **7 days**
- the last **4 ISO weeks**
- the last **3 months**

Tiers overlap rather than add up — today's backup is the newest of its day, its
week and its month, and is kept once — so the default holds at most fourteen
files and reaches back about three months.

Counted in **periods, not files**. "Seven daily" means the seven most recent
days that have a backup. A rule counting files would, after one afternoon of
six manual backups, hold six from that afternoon and one from yesterday; and a
week of downtime would not silently expire everything that survived it.

```bash
BACKUP_RETAIN_DAILY=7
BACKUP_RETAIN_WEEKLY=4
BACKUP_RETAIN_MONTHLY=3
```

Setting all three to zero is refused at startup rather than on the night it
would have deleted everything. A backup dated in the future — a clock that went
backwards, a file copied from another host — is never deleted.

```bash
… contextctl backups list              # what you have, and what keeps each one
… contextctl backups prune --dry-run   # what retention would remove, and why
… contextctl backups prune
```

## Verifying

Three levels, each stronger and more expensive:

```bash
# 1. The file is intact: it matches the checksum written with it.
# 2. mongorestore can open and plan it (--dryRun: writes nothing).
… contextctl backups verify
… contextctl backups verify --all

# 3. It actually restores. Into throwaway collections, counts compared with
#    the manifest, copies removed afterwards — win or lose.
… contextctl backups verify --restore
```

`verify` exits non-zero if any backup fails, so a scheduler can alert on it.

**Only the third proves anything about recovery.** SHA-256 detects byte-level
truncation or corruption, and the dry run validates the restore plan; neither
can say every document materializes. A backup that has
never been restored is not yet known to be a backup — which is why the shipped
schedule runs `--restore` weekly.

It restores into **throwaway collections inside the application's own
database**, named `restorecheck_<timestamp>_<nonce>__<collection>`, and removes them
afterwards whether the check passed or not. Into the same database rather than
a separate one because the application authenticates as a user with `readWrite`
on its own database and nothing else — restoring elsewhere would mean granting
the platform cluster-wide privileges it deliberately does not have. Nothing
live is touched: no real collection has that name, and the check never passes
`--drop`, so it cannot write over what it is verifying. It does temporarily
need disk for a second copy of the data.

## Scheduling

Daily backups and a weekly restore check, as systemd timers:

```bash
sudo cp deploy/systemd/agent-context-backup.{service,timer} /etc/systemd/system/
sudo cp deploy/systemd/agent-context-backup-verify.{service,timer} /etc/systemd/system/
# The units run as `agent-context` from /opt/agent-context-platform. Change
# both to match your deployment, or override with `systemctl edit`.
sudo systemctl daemon-reload
sudo systemctl enable --now agent-context-backup.timer
sudo systemctl enable --now agent-context-backup-verify.timer

systemctl list-timers 'agent-context-*'
journalctl -u agent-context-backup.service -n 50
```

`Persistent=true` means a backup missed while the host was off is taken when it
comes back, rather than skipped to tomorrow. `RandomizedDelaySec` keeps several
hosts from starting at the same instant.

Cron instead, if you prefer:

```cron
17 2 * * *  cd /opt/agent-context-platform && ./scripts/backup.sh >> /var/log/agent-context-backup.log 2>&1
40 4 * * 0  cd /opt/agent-context-platform && docker compose exec -T context-api python -m app.cli.contextctl backups verify --restore
```

**Alert on the job not running at all**, not only on it failing. A backup timer
that quietly stops firing produces no errors, and you find out when you need a
backup. `systemctl list-timers` shows the last and next elapse.

## Disaster recovery

The full procedure, in order. Each step is checked before the next, because a
recovery that skips a check is a recovery you find out about later.

### 1. Stop the affected services

```bash
docker compose stop context-api mongot
```

Stop the API first so nothing writes while the database is being replaced, and
mongot so it is not indexing a database that is about to change underneath it.
Leave `mongodb` running — the restore needs it.

### 2. Restore MongoDB

```bash
docker compose run --rm --no-deps -T context-api \
  python -m app.cli.contextctl backups list
RESTORE_CONFIRMED=1 ./scripts/restore.sh --latest
```

The script uses a **one-off container**, not the stopped API container, so no
request-serving process starts during recovery. It verifies the manifest,
target database, checksum, byte count, and archive readability before passing
`--drop`; afterwards it compares every restored collection count with the
manifest. Any mismatch is a failure, not a warning.

Restoring a specific managed backup instead:
`RESTORE_CONFIRMED=1 ./scripts/restore.sh --name context-20260916-131532.archive.gz`.
Copy an external archive and its manifest into the configured backup directory
before recovery so it receives the same integrity and collection checks.

### 3. Verify the replica set

```bash
docker compose exec -T mongodb /usr/local/bin/mongodb-healthcheck.sh
```

Exit status zero means the authenticated replica-set check found this member
primary. Nothing below works until it is: the platform requires a primary for
transactions, and mongot replicates from it.

### 4. Verify the collections

```bash
docker compose run --rm --no-deps -T context-api \
  python -m app.cli.contextctl --json backups list \
  | jq '.backups[0] | {backup, documents, collection_counts}'
```

Keep this output with the recovery record. The restore already compared these
manifest counts with the live database and would have exited non-zero if a
skill, project, project-context item, memory, research source/snapshot/document/chunk,
research note/claim/citation/brief/candidate/promotion event/ingestion job, API key, or audit
record was missing.

### 5. Verify the indexes

```bash
docker compose run --rm --no-deps -T context-api \
  python -m app.cli.contextctl skills reindex --database-only
```

At this point mongot is intentionally still stopped. This verifies/reconciles
the MongoDB collection validators and standard indexes without contacting the
search coordinator. Do not continue on an error.

### 6. Start mongot

```bash
docker compose start mongot
docker compose ps mongot
```

Wait for the Compose health state to become `healthy` before rebuilding search.

### 7. Verify search

```bash
docker compose run --rm --no-deps -T context-api \
  python -m app.cli.contextctl skills reindex
```

Search indexes are not in the backup. `reindex` recreates or reconciles every configured index
against the restored documents and loaded local model. It is idempotent. A new
index can remain `BUILDING`; repeat the command until every index reports
`unchanged`, then prove queries work in the step 11 smoke tests.

### 8. Verify embeddings

```bash
docker compose run --rm --no-deps -T context-api python -c '
import asyncio
from app.core.config import get_settings
from app.db.client import MongoDBClient

async def verify():
    settings = get_settings()
    client = MongoDBClient(settings)
    await client.connect(require_search=False)
    try:
        for name in ("skill_chunks", "project_context", "memories"):
            query = {"embedding": {"$exists": True},
                     "embedding_dimensions": settings.embedding_dimensions}
            print(name, await client.database[name].count_documents(query))
    finally:
        await client.close()

asyncio.run(verify())'
```

Compare the non-zero counts with the pre-disaster inventory for collections
that had embeddings. Nothing should need re-embedding: vectors were in the
backup. If dimensions do not match, the deployment's `EMBEDDING_MODEL` changed
since the backup — fix the setting rather than regenerating data implicitly.

### 9. Start the API

```bash
docker compose start context-api
curl --fail http://127.0.0.1:8000/health/ready
```

Readiness is the final index-state check: it requires MongoDB, mongot, the
embedding model, and all six search indexes to be queryable.

### 10. Start MCP

The MCP server is spawned by its client, so there is nothing to start. Confirm
one can talk to the platform:

```bash
docker compose --profile mcp run --rm -T mcp <<< \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}'
```

If the API requires a key, the server needs one — see [security.md](security.md).

### 11. Run smoke tests

Retrieval is the thing the platform exists to do, so it is the last thing
checked:

```bash
K=$(cat ./secrets/codex-api-key)

# A skill, a project's context, and a memory all reachable, ranked together.
curl --fail -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  -d '{"query":"idempotency","project_id":"payment-service","limit":5}' \
  http://127.0.0.1:8000/api/v1/search | jq '.results[] | {source, title}'

# Each source individually, so a missing one is obvious.
curl --fail -H "Authorization: Bearer $K" http://127.0.0.1:8000/api/v1/skills | jq '.items | length'
curl --fail -H "Authorization: Bearer $K" \
  'http://127.0.0.1:8000/api/v1/context?project_id=payment-service' | jq '.items | length'
curl --fail -H "Authorization: Bearer $K" http://127.0.0.1:8000/api/v1/memory | jq '.items | length'
```

A search that returns results from every expected source is the point at which
the recovery is done. Anything less and something in steps 4–8 did not hold.

### Onto a clean host

The same procedure, with the platform built first:

1. Install Docker with Compose v2.
2. Clone the repository; restore `secrets/` and `.env` from your secret manager.
3. `./scripts/prepare-docker-wheels.sh && docker compose up -d --build --wait`
4. Copy the backup onto the host, into `BACKUP_HOST_DIRECTORY`.
5. `contextctl backups verify --name <backup>` before relying on it.
6. Continue from step 1 above.

## What to check before you need this

- `contextctl backups list` shows a backup from today.
- `systemctl list-timers 'agent-context-*'` shows a next elapse in the future.
- The most recent `backups verify --restore` passed, and you know when.
- The backups exist somewhere that is not this host.
- Someone other than you has followed this document end to end, once.

## Reference

| Setting | Default | Meaning |
| --- | --- | --- |
| `BACKUP_HOST_DIRECTORY` | `./backups` | Host directory mounted into the container; create it owned by uid 999 with mode `0700`. |
| `BACKUP_DIRECTORY` | `/backups` under Compose | Container path where backups are written. Created/tightened to `0700`. |
| `BACKUP_RETAIN_DAILY` | `7` | Days of daily backups to keep. |
| `BACKUP_RETAIN_WEEKLY` | `4` | ISO weeks to keep. |
| `BACKUP_RETAIN_MONTHLY` | `3` | Months to keep. |
| `BACKUP_TIMEOUT_SECONDS` | `3600` | How long one tool invocation may take. |

| Command | Does |
| --- | --- |
| `contextctl backup` | Take one, write its manifest, apply retention |
| `contextctl backup --no-prune` | Take one and keep everything |
| `contextctl backup --output PATH` | One-off to a path; unmanaged |
| `contextctl backups list` | What you have, and what keeps each one |
| `contextctl backups verify` | Checksum and readability of the newest |
| `contextctl backups verify --all` | …of every backup |
| `contextctl backups verify --restore` | Prove it restores, into throwaway collections |
| `contextctl backups prune --dry-run` | What retention would remove, and why |
| `contextctl backups prune` | Apply retention |
| `contextctl restore --latest --yes` | Restore the newest |
| `contextctl restore --name N --yes` | Restore a named one |
| `contextctl restore --input PATH --yes` | Restore from a path |

`scripts/backup.sh` and `scripts/restore.sh` wrap these for a scheduler. The
restore script refuses to run without `RESTORE_CONFIRMED=1`, and prints the
steps it does not do.
