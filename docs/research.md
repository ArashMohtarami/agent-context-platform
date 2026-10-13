# Research, evidence, and reviewed knowledge

The research system turns explicitly supplied sources into searchable material
while retaining provenance. A claim, candidate review, and authorized promotion
are separate steps. Source class and retrieval relevance never establish truth
or factual confidence.

```text
Research question -> registered sources -> immutable snapshots -> searchable chunks
                 -> notes / claims / citations -> evidence packet / research brief
                 -> pending candidate -> review -> authorized promotion
```

## Research projects and permissions

A research project has a stable `research_project_id`, name, question, type,
active/archived status, and zero or more related knowledge-project IDs. Create,
list, get, and revision-guarded update routes live at `/api/v3/research/projects`.
The server owns authorization metadata. Access requires every linked project
within the caller's grant; stored sources and evidence also retain their original
project authorization set. Research with no project links is global to authorized
research readers, so do not use that scope for private project facts.

Use `research:read` for reads and `research:write` for source, note, claim, brief,
and candidate creation. Candidate decisions require `research:approve`;
promotion requires `research:promote` plus the target's `memory:write` or
`context:write` scope. Project authorization applies at every step. Scope grants
are independent; a research writer need not be a reviewer or promoter.

## Register sources and ingest snapshots

`POST /api/v3/research/sources` records a source before acquisition. Sources have
IDs, canonical locators, descriptive source classes, metadata, and preserved
project grants. Duplicate canonical registrations are refused instead of
replacing history. Snapshot IDs, hashes, retrieval times, and version identifiers
pin each observation. Source registration alone performs no acquisition.

The registry accepts `manual`, `url`, `file`, `markdown`, `pdf`, `git`, `github`,
`documentation`, and `rss` metadata. The configured providers below determine
what can actually be ingested; registration is not a promise of a connector for
all source types. RSS feeds and documentation-site crawling have no acquisition
provider in this release.

`POST /api/v3/research/sources/{source_id}/ingest` accepts one mode:

| Mode | Accepted input | Acquisition boundary |
| --- | --- | --- |
| Manual text | Explicit UTF-8 `content`, plain text or Markdown | Supplied text only |
| File/PDF | Bounded base64 `content_base64`, content type, optional filename | Uploaded bytes; no server-side path |
| Web URL | `fetch_url: true` for an active registered `url` source | One bounded public HTTP(S) page |
| Repository | `fetch_repository: true` for active registered `git` or `github` sources | Public HTTPS repository, read-only |

A `file:` URI is a citation locator, not authorization to read the server's
filesystem. File uploads reject unsafe filenames, archives, executable signatures,
unsupported types, malformed/encrypted PDFs, and oversized input. Raw bytes are
capped at 700,000; normalized text at 200,000 characters; PDF extraction at 100
pages with bounded stream handling. Unsupported PDF constructs fail rather than
being delegated to an external executable.

The web provider accepts HTML/XHTML, requests identity encoding, bounds redirects,
size, and execution time, and does not run JavaScript or crawl links. It validates
public addresses before each request and checks the connected peer after response
headers where the HTTP transport exposes it. Ambient proxies are disabled.

**Network limitation:** preflight DNS validation and the connection do not share
a pinned DNS result. A hostname can change between them, and a post-response peer
check cannot prevent an already-issued request. Repository transport has the same
separate-resolution boundary and no peer hook. Enforce outbound firewall/proxy
rules that block private, loopback, link-local, and cloud metadata destinations
when accepting untrusted sources. These application checks do not alone prove
comprehensive SSRF prevention.

Repository providers disable ambient credentials, redirects, hooks, checkout,
submodules, package scripts, builds, and shell execution. They fetch bounded
repository objects and read eligible blobs only. They exclude binaries, symlinks,
unsafe paths, common build/dependency trees, and detectable secret files/content.
GitHub acquisition uses public `https://github.com/<owner>/<repository>` sources
without a provider token. A registered source may select one validated branch,
tag, or full commit. Git must be available in the ingestion environment.

Each accepted ingestion stores normalized documents and citable chunks with
source/snapshot/document IDs and available headings, page numbers, lines,
repository paths, symbols, and commit IDs. Hash-derived IDs make reingesting the
same source bytes or repository commit idempotent. Ingestion returns snapshot/job
metadata; status is available through the source's ingestion-job route. Jobs are
bounded in-process operations, with no distributed queue or durable worker lease.

## Search, notes, and claims

`POST /api/v3/research/search` supports lexical, vector, and hybrid retrieval.
Profiles are `research_general`, `research_primary_sources`, `research_recent`,
`research_content`, `research_architecture`, and `research_code`. The code profile
searches authorized `source_code` chunks; other profiles can include existing
knowledge through the established retrieval service.

Filter by source type/class, publisher/domain, source dates, retrieval dates,
`as_of` snapshot cutoff, research project, and an optional linked knowledge
project. Stored provenance and current project links are both authorized before
results or hit metadata are exposed. Responses diversify sources where available,
use opaque cursors, and respect `RESEARCH_RETRIEVAL_TOKEN_BUDGET` (default 8,000)
and its configured hard maximum. `retrieval_score` means relevance only.

Notes are working artifacts with source references and working/reviewed/archived
states. Claims are explicit assessments with one of `unreviewed`, `supported`,
`partially_supported`, `contested`, `unsupported`, or `outdated`, and optional
caller-supplied confidence/rationale. Revision-guarded updates reject stale edits.
Neither a note nor a claim automatically creates durable memory.

## Citations, packets, and briefs

`POST /api/v3/research/citations` stores a claim, source, immutable snapshot,
optional chunk, locator, evidence excerpt, and relation: `supports`, `contradicts`,
`qualifies`, or `background`. A chunk-backed excerpt must occur in the cited chunk.
Locators preserve source-supported page, line, URL/anchor, path, and commit details.

`POST /api/v3/research/evidence` builds a read-only packet of relevant context,
supporting/contradicting/qualifying evidence, locators, dates/classes, and
uncertainties. Relations come from persisted citations, not semantic similarity.
Conflicting evidence appears together rather than being silently resolved.
The claim-evidence route exposes the same authorized evidence for a selected claim.

`POST /api/v3/research/briefs` stores structured findings, claim/citation references,
contradictions, unknowns, and next questions. Missing evidence and bounded-result
limits remain visible. The calling agent can write prose from this material;
a stored brief is not a guarantee of completeness or correctness.

## Candidates, promotion, and skill proposals

`POST /api/v3/knowledge-candidates` creates a `pending_review` proposal with
explicit target, provenance, confidence, and grants. Approve/reject routes require
an expected revision and append immutable decision events. Approval alone makes
no change to memory, project context, or canonical skills.

The separate `/knowledge-candidates/{candidate_id}/promote` route requires an
approved revision and supports `memory`, `project_context`, and
`architecture_decision` targets. It reuses the ordinary knowledge services,
retaining candidate, claim, citation, source, and verification metadata. An
explicitly selected old context record may be superseded after the replacement
is created; records are not silently deleted. Unsupported target types fail
rather than being redirected to another domain.

Skill proposals retain `trust_level: generated`, an exact current skill version,
proposed Markdown patch, rationale, and evidence. They can be inspected/exported,
but cannot edit files, commit Git, activate a version, or mutate imported skills.
The workflow is proposal -> human review -> canonical Git change -> normal import.

## Freshness and content integration

Research-derived knowledge retains verification time, source snapshots, optional
`review_after`, and stable/version-dependent/time-sensitive/volatile freshness
metadata. Revalidation identifies due reviews and later changed snapshots, then
creates a pending-review update candidate. A changed hash or source version is a
review signal; it does not automatically invalidate, overwrite, or promote knowledge.

Creator briefs can retain research claims and citations. Content fact review maps
a saved draft to explicitly stored claims and evidence, reporting supported,
unsupported, contested, outdated, and unmapped material. It does not rewrite the
draft, guarantee truth, or publish it. See [content-schema.md](content-schema.md).

## MCP, CLI, and operations

MCP exposes constrained research capabilities such as project/source registration,
manual text or registered repository ingestion, research search, notes/claims,
evidence packets, briefs, candidates, and skill proposals. It has no arbitrary
URL-fetch, filesystem-read, shell, Git-command, or database-query tool. Web
acquisition and uploaded file bytes use the REST/operator CLI boundary.

CLI groups include `research`, `source`, `note`, `claim`, `candidate`, and
`skill proposal`; use `contextctl <group> --help` and [cli.md](cli.md) for current
arguments. The [runnable research example](guide/examples/research_example.py)
and [usage guide](usage-guide.html) walk through the full sequence.

Source-count, shared ingestion-concurrency, and per-ingestion chunk limits bound
expensive work. Failed capacity checks return safe errors without queuing work.
Full-database backups include research snapshots, evidence, review history, and
promotion metadata. Respect the source's license and disclosure rights when
storing excerpts or exporting research-derived content.
