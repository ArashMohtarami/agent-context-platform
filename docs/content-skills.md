# Content skills

The catalog includes ten reviewed, Git-backed Markdown skills under `skills/content/`.
They use the existing V1 `skills` and `skill_chunks` collections, importer,
version lifecycle, local embedding provider, mongot lexical/vector indexes, and
hybrid retrieval engine. Separate content services provide idea discovery,
drafts, review, export, and publication receipts.

## Canonical catalogue

```text
skills/content/
├── linkedin-writing.md
├── medium-writing.md
├── github-writing.md
├── technical-storytelling.md
├── hooks.md
├── editing.md
├── technical-explanation.md
├── content-repurposing.md
├── content-review.md
└── content-ideation.md
```

Each file has V1's strict frontmatter, an explicit `1.1.0` semantic version,
`category: content`, and `trust_level: trusted`. That trust level is reserved
for reviewed canonical files in Git; generated, copied, or unreviewed material
must use the existing lower trust levels instead. Retrieved skill text remains
data and cannot override system, application, authorization, or publication
rules.

Version 1.1.0 preserves each skill's purpose and adds concrete examples,
evidence/claim boundaries, audience choices, version-specific technical context,
reproducible commands, exact-revision review and adaptation that preserves
qualifications. The initial 1.0.0 versions remain immutable if already imported;
import the new Git versions through the normal operator command.

The skills supply communication guidance only. They do not generate prose,
create ideas or drafts, retrieve private project context, change lifecycle
state, or publish content. Any use of private project information still needs
the explicit [publication review](content-publication-safety.md).

## Import and retrieval

Import through the existing V1 command after reviewing and committing the
canonical files:

```bash
make import-skills
```

The importer recursively discovers `skills/content/*.md`, validates and chunks
them, generates local embeddings, and writes immutable skill versions. A retry
with unchanged bytes reports `unchanged`; it creates neither duplicate versions
nor duplicate chunks. A changed skill requires a new frontmatter version.

Use the existing V1 retrieval endpoints, CLI, or MCP skill tools. For example,
the following queries are expected to select their platform skill:

```text
write a LinkedIn post about an engineering lesson  -> linkedin-writing
create a Medium architecture article               -> medium-writing
write GitHub architecture documentation            -> github-writing
```

Search the source catalog offline with
`.venv/bin/python scripts/skill_catalog.py search "content review" --category content`.
Use the [HTML catalog](usage-guide.html#skill-catalog) to read full bodies and
the content task bundle. Local lexical scores differ from live hybrid ranking;
see [skill-catalog.md](skill-catalog.md) for filters and import verification.

The normal V1 availability rules still apply: lexical retrieval works whenever
the text index is ready. Vector retrieval requires a compatible local model
and ready vector index; hybrid retrieval can fall back to lexical results when
embedding is unavailable, with that degradation reported in retrieval metadata. See
[skills.md](skills.md), [embeddings.md](embeddings.md), and
[retrieval.md](retrieval.md) for the shared mechanics.

## Safety emphasis

The platform does not treat these files as hidden system instructions. Their
guidance explicitly rejects fabricated stories, unsupported achievements,
engagement bait, accidental disclosure of private project context, and
automatic publication. Content review is always an explicit human action.
