# Skills: canonical sources and runtime import

## Canonical source and directory layout

Git-tracked UTF-8 Markdown under `skills/` is the canonical source. MongoDB is a validated runtime
representation and must not be edited as a replacement for the files. The expanded structure is:

```text
skills/
├── architecture/
├── content/
├── django/
├── docker/
├── engineering/
├── fastapi/
├── frontend/
├── mongodb/
├── operations/
├── platform/
├── postgresql/
├── python/
└── security/
```

The catalog contains 39 canonical skills across 11 metadata categories,
including six Django skills, four FastAPI skills and ten content skills.
Skill versions are declared individually in frontmatter; consult the catalog
for the current version of each file. Directory names do not always equal category names: FastAPI uses
`backend`, while Docker uses `operations`.

Use [skill-catalog.md](skill-catalog.md) for task routing, source search and the
complete generated table, or the [HTML catalog](usage-guide.html#skill-catalog)
for offline search and full bodies. `scripts/skill_catalog.py` validates and
searches the Git sources without importing or connecting a database. Content
guidance is also documented in [content-skills.md](content-skills.md).

## Required frontmatter

Every `*.md` file below the configured directory must start with YAML frontmatter:

```markdown
---
id: fastapi-production
name: FastAPI Production Engineering
version: 1.0.0
description: Production-grade FastAPI engineering practices.
category: backend
trust_level: trusted
tags:
  - python
  - fastapi
---

# FastAPI Production Engineering

## Purpose

...
```

All seven fields are required except `tags`, which defaults to an empty list. Unknown keys, duplicate
tags, invalid identifiers, unsupported trust levels, malformed semantic versions, non-UTF-8 input,
empty bodies, symlinks, and files above `SKILL_MAX_FILE_BYTES` are rejected. YAML is parsed with the
safe loader. Import errors identify the file but never print its body.

Trust levels are `trusted`, `user`, `external`, and `generated`. Retrieved text remains data; its
trust label does not allow it to override system or application instructions.

## Version lifecycle and transactions

The frontmatter version is explicit. The importer never guesses or silently increments a version.

- First import: creates the active version and its chunks.
- Identical retry: reports `unchanged` and performs no writes.
- Changed bytes with the same version: fails and requires a new semantic version.
- New declared version: in one replica-set transaction, marks the prior active version
  `superseded`, inserts the new active document, and inserts its chunks.

Historical skill documents and chunks are never overwritten or deleted by import. A transaction
failure rolls back the entire version transition, so the preceding active version remains valid.
Only one canonical Markdown file may declare a given skill ID in one import run.

## Section-aware chunking

The parser recognizes ATX headings outside fenced code and retains heading paths in chunk metadata.
Short adjacent sections are packed together with their Markdown headings intact. Large sections aim
for the environment-driven minimum, target, maximum, and overlap sizes, which default to 500, 650,
800, and 75 approximate tokens respectively. Overlap is taken only from non-code content and stays
within the configured maximum.

Chunk token counts use a deterministic word/punctuation
estimate. Fenced blocks using backticks or tildes are atomic and are never split or copied as
overlap. An unterminated fence or a single atomic block larger than MongoDB's persisted chunk limits
is rejected instead of corrupted.

Each chunk is stored separately with:

- deterministic `chunk-NNNN` identity within `(skill_id, version)`;
- global `chunk_index` order;
- section label and heading path;
- source path and overlap count;
- approximate token count;
- an optional vector with its embedding model metadata.

## Import operation

Start the stack, then run:

```bash
make import-skills
```

Equivalent direct command:

```bash
docker compose run --rm --build context-api python -m app.cli.import_skills
```

For a different canonical root:

```bash
python -m app.cli.import_skills --directory /path/to/skills
```

The CLI connects with the application MongoDB user, ensures the existing schema/index migration,
and prints only aggregate counts: discovered, new, updated, unchanged, chunks created, and chunks
embedded. It exits non-zero on invalid input, a version conflict, or an unusable embedding model.
All files are parsed and chunked before the first database write, preventing a malformed later file
from causing a partial directory import.

When embeddings are enabled the importer loads the configured local model before the first write,
so a missing or mismatched model fails the run instead of storing half-embedded chunks. Every chunk
of a new version is embedded together with that version. Skip embedding with:

```bash
python -m app.cli.import_skills --no-embeddings
```

See [embeddings.md](embeddings.md) for the model configuration and the rules that keep vectors from
different models apart.

Configuration:

```text
SKILLS_DIRECTORY=skills
SKILL_MAX_FILE_BYTES=1000000
SKILL_CHUNK_MIN_TOKENS=500
SKILL_CHUNK_TARGET_TOKENS=650
SKILL_CHUNK_MAX_TOKENS=800
SKILL_CHUNK_OVERLAP_TOKENS=75
```

## REST API

```text
GET  /api/v1/skills
GET  /api/v1/skills/{skill_id}
POST /api/v1/skills/search
```

Listing returns active versions by default and supports status, category, tag, trust-level, limit,
and cursor filters. The detail endpoint returns the active version unless `?version=1.0.0` is
provided. It includes preserved version history and a chunk page controlled by `chunk_limit` and
`chunk_cursor`.

Search accepts a bounded query plus optional category, tags, trust level, and limit. It matches
escaped literal query terms case-insensitively against active skill metadata and chunk content. At
most three matching chunks accompany each result. Regex input is escaped, results are bounded, and
BSON identifiers/revisions are not exposed.

This endpoint deliberately uses no search index, so it keeps working when `mongot` is
unavailable, and it returns no relevance scores. Scored retrieval over the same chunks lives at
`POST /api/v1/skills/search/vector` and `POST /api/v1/skills/search/lexical`, documented in
[retrieval.md](retrieval.md). Hybrid retrieval uses the same stored chunks and is described in that guide.

## Runtime integration

The importer supplies versioned chunks and model-tagged vectors to lexical, vector,
and hybrid retrieval. The API and MCP gateway enforce the caller's scopes before
returning that knowledge. Canonical skill edits remain an operator-reviewed Git
workflow; generated proposals cannot import or activate themselves.
