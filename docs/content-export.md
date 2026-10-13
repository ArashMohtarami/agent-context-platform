# Platform formatting and manual export

Platform adapters format and validate an already stored, agent-authored content
item for a supported platform. It does not generate prose, create versions,
change lifecycle state, call a remote publishing API, upload a file, or set an
external URL. See [content-publishing.md](content-publishing.md) for the separate explicit
manual-publication receipt.

## Platform adapter contract

`app.content.platforms.base.ContentPlatform` is the small adapter protocol:
`validate(content)`, `format(content)`, and `export(content, mode)`. The
persisted `app.models.content.ContentPlatform` enum remains unchanged. The
registry currently contains:

| Platform | Representation | Validation and metadata |
| --- | --- | --- |
| LinkedIn | UTF-8 plain text | Normalized readable blank paragraphs and an optional configured character ceiling |
| Medium | Markdown | Article title/slug, headings, links, and paired code fences |
| GitHub | Markdown | Paired code fences and a suggested `README.md`, `docs/…`, or `docs/adr/…` path |

Adapters preserve supplied text apart from stable line endings, trailing
whitespace, and the one required document H1 for Markdown representations.
They do not make content claims, add engagement language, or connect to any
external platform. A deployment may set `CONTENT_LINKEDIN_MAX_CHARACTERS` to a
positive number; leaving it unset imposes no local LinkedIn character ceiling.

## API and authorization

```text
POST /api/v2/content/{content_id}/export
```

The request body has the owning `profile_id` and a `mode` of `stdout`, `file`,
or `markdown`. It requires `content:read`, an `approved` or `published` item,
validates profile access, and checks
every `project_id` and provenance reference before formatting. It returns only
a manual export representation: `text`, `format`, suggested local names/paths,
and platform metadata. `file` is a client intent, not permission for the API
server to write a file.

No response performs an external request or changes `status`,
`published_at`, `external_url`, `current_version`, or provenance.

## CLI

```bash
# print an API-returned representation
contextctl content export --profile-id personal-brand --content-id retry-post --mode stdout

# ask for Markdown formatting and print it
contextctl content export --profile-id personal-brand --content-id architecture-article --mode markdown

# opt in to a local UTF-8 write; existing files need an explicit --force
contextctl content export --profile-id personal-brand --content-id readme-draft \
  --mode file --output ./README.md
```

The CLI always calls the protected API for authorization and formatting. Only
its explicit `--mode file --output PATH` writes locally; it creates missing
parent directories and refuses to overwrite an existing file without
`--force`. It never uses suggested paths automatically and never publishes.

## Persistence and operations

Export adds no collection, index, migration, backup input, search index,
embedding, or MCP tool. It formats the current stored item only, so immutable
content versions remain untouched and embeddings continue to use the existing
current-representation policy.
