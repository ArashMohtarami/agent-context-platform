# Evidence-backed research console (V6 Phase 12)

The organization console provides research projects, source registration,
bounded ingestion, retained snapshots/documents/chunks, working notes, claims
and citation relations, structured briefs, candidate/skill-patch review,
controlled promotion and advisory freshness. It reuses the existing research
services through session-only adapters under
`/api/v6/organizations/{org_id}/research`. Human requests never borrow a machine
credential or make an internal HTTP call to a privileged legacy route.

Every adapter requires an enabled console, current verified membership/session,
matching active organization and action-specific research-project and linked
project resources. Unlinked research requires explicit organization-global
knowledge permission. NONE stays NONE. Lists are limited to 50 records (default
20), with tenant/filter/policy-bound cursors. Skill proposal lists prefilter the
intersection of research and canonical-skill read access before pagination.

| Operation | Current authority |
| --- | --- |
| Projects, sources, notes, documents, snapshots, evidence, briefs, decisions, freshness | `research:read` for the research project and all linked resources |
| Project/source/note/claim/citation/brief/candidate/proposal creation and ingestion | `research:write` for each affected resource |
| Claim assessment, candidate or skill-proposal decision | `research:approve`, readable evidence, `reviews:decide`, independent verified authorship and exact expected revision |
| Candidate promotion | `research:promote`, readable provenance and separate `context:write` or `memory:write` for the target |
| Skill proposal/patch | Above research action for the skill plus `skills:read`; approval never applies Git changes |
| Job inspection/download | Current read authority plus revalidation of the original initiating session, membership and write policy |

Mutations require recent MFA, Origin/CSRF and the shared session/policy write
fences. Decision gates also apply at the repository boundary to existing
machine/REST/MCP operations. A normal managed machine cannot decide using
`content:write`; independently approved service-reviewer custody rules still
apply. Human provenance uses real human identities, without synthetic API-key
IDs. Review events have no promoted-object reference until promotion creates
that object. Original authorship is retained by the existing immutable review
history. Rewriting a reviewed artifact cannot erase the self-review boundary.

The console accepts at most **8 KiB** of submitted text or decoded file bytes,
within the existing 16 KiB browser-request cap. Files use submitted bytes only;
file URIs are source identifiers, not paths fetched from the server. Supported
browser acquisition types are manual, URL, file, Markdown and PDF. Repository,
GitHub and RSS acquisition are unavailable in the console. URL acquisition
accepts only the registered source and retains the existing provider's public
address checks, per-hop redirect checks, byte/time/content limits and disabled
environment proxy discovery. **DNS resolution is not pinned to the connection**;
the existing residual limitation in [research](research.md) and
[security](security.md) remains. The dashboard adds no arbitrary URL proxy.

Ingestion uses the actual persisted pending/running/completed/failed lifecycle;
it does not introduce a background queue. The jobs panel is restricted to jobs
started in the current browser session. A JSON download retrieves a freshly
authorized, bounded page and releases its temporary browser Blob URL; it is not
a permanent server download link. Revoking the initiating authority denies
future job-result access without undoing committed domain records. Job failures
are projected to fixed, content-free messages, without raw exceptions or
initiating session/credential metadata.

Evidence shows citation IDs, exact source/snapshot/chunk locators, retrieval
time, source class and relation. Confidence is an explicit authored assessment,
not a score inferred from citation counts or source class. Claim evidence is
marked bounded at 50 citations; absence does not establish complete coverage.
Briefs preserve their stored references, unknowns and contradictions and make no
claim of automatic prose synthesis. Freshness compares retained snapshot
versions and review deadlines; it never silently replaces promoted knowledge.

Candidate decisions and promotion use exact current revisions. Conflicts keep
the draft in component memory; the review dialog requires an explicit reload
and reinspection before another decision. Promotion revalidates same-org
provenance and uses the existing V1 context/memory services. Their documented
ordered-write/idempotency behavior is retained; this phase does not claim a new
transaction across every promotion side effect. Skill patches remain inert,
generated proposals even when approved, for separate reviewed Git changes and
canonical import. There is no filesystem editor or auto-apply endpoint.

All untrusted Markdown reuses the Phase 11 allowlisted renderer: raw HTML,
scripts, images/embeds and unsafe link schemes are inert. Patch/locator text is
escaped. No research bodies, prompts or search terms are added to workforce
monitoring. No dependencies or schema versions are added in this phase.

## Verification

```sh
python -m pytest -q tests/unit/test_v6_research_adapters.py
python -m pytest -q tests/unit/test_research_ingestion_api.py tests/unit/test_research_notes_claims_api.py tests/unit/test_research_evidence_api.py tests/unit/test_research_brief_api.py tests/unit/test_knowledge_candidate_api.py tests/unit/test_skill_update_proposal_api.py tests/unit/test_revalidation_api.py
V6_RESEARCH_ADAPTERS_ONLY=1 bash scripts/test-v6-isolation.sh
npm --prefix dashboard run test:unit -- src/research/evidence.test.tsx src/research/review.test.tsx
npm --prefix dashboard run build
npm --prefix dashboard run test:e2e -- e2e/research.spec.ts
```

The integration runner selects exactly
`tests/integration/test_v6_research_adapters.py`, using disposable real MongoDB
and existing domain services. Fixtures reuse logical project/source/skill IDs
across two organizations with different private sentinel content. Browser tests
use the built SPA and real HTTPS API/provider infrastructure, persisted sessions
and fictional recovery factors, via the existing private fixture channel.
They produce actual revision-bound decisions and promoted provenance, rather
than mocking a review response. Screenshots, Graphify output, infrastructure
logs and planning remain ignored local artifacts. Record actual browser/version
and results with each run; no other browser-engine coverage is implied.
