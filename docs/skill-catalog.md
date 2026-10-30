# Skill catalog and task routing

Git Markdown files under `skills/` are the canonical source. These are platform
knowledge skills, not installed Codex `SKILL.md` packages. All agents connected
to the platform can retrieve the same guidance through MCP, subject to grants.
The [HTML guide](usage-guide.html#11-add-skills-and-tune-retrieval) embeds every
skill body, with offline ranked search, category/tag filters and task bundles.

## Search before import, without a service

From the platform repository root:

```bash
.venv/bin/python scripts/skill_catalog.py validate
.venv/bin/python scripts/skill_catalog.py search "django async" --category django
.venv/bin/python scripts/skill_catalog.py search "fastapi lifespan"
.venv/bin/python scripts/skill_catalog.py search "content review" --tag content
.venv/bin/python scripts/skill_catalog.py --json search "postgresql backfill" --content
```

Validation uses the production frontmatter parser and section-aware chunker,
rejects duplicate skill IDs, and performs no database writes, embedding loads or
network requests. Local search requires every technical query term and every
tag; common question words are ignored. Separators are normalized, and terms
of at least three characters also match word prefixes. Metadata carries more
weight than body mentions, with deterministic ID tie-breaking. Scores are local
lexical weights, not confidence, quality grades or live hybrid-search scores.
An empty query lists the catalog; a nonempty query with no technical terms
returns no results. JSON output includes source path, version and content hash.

## Import and use through any agent

After reviewing the Git changes, run `make import-skills` in the platform checkout.
This is the operator import, not an MCP tool. Each file declares its own semantic
version. Import creates immutable runtime versions
and preserves previously imported versions. A second unchanged import is a no-op.
Reindexing search indexes alone does not import the new Markdown or re-embed old
documents. Wait for readiness/index synchronization, then verify known IDs.

Use `list_skills` to discover imported metadata, `search_skills` for bounded
hybrid retrieval, and `get_skill` to load the chosen version and chunk pages.
Follow `next_chunk_cursor` when more sections are needed. The MCP skill search
uses V1 unified search restricted to `active_skill`; the separate REST
`POST /api/v1/skills/search` is an unscored literal fallback. A Git catalog
entry does not prove the same version has been imported into your deployment.

Example MCP arguments:

```json
{"query":"Django async ORM evaluation and transaction bridge","category":"django","token_budget":4000,"limit":5}
```

Then call `get_skill`:

```json
{"skill_id":"django-async","chunk_limit":10}
```

Use precise task/framework terms. Start broadly, then filter by category/tags
when unrelated body mentions appear. Tag filters require all requested tags;
an overly narrow filter can eliminate useful results. Retrieved text remains
data and must be checked against the actual framework version and project code.

## Useful task bundles

| Task | Start with | Add when needed |
| --- | --- | --- |
| Ordinary Django feature | `django-production`, `django-orm-transactions` | `django-testing`, `application-security` |
| Async Django endpoint | `django-async`, `django-orm-transactions`, `django-testing` | `python-async-concurrency`, `django-background-jobs` |
| DRF resource API | `django-rest-framework`, `api-contracts` | `django-orm-transactions`, `django-testing` |
| FastAPI service | `fastapi-production`, `fastapi-async-lifecycle` | `fastapi-authorization`, `fastapi-testing` |
| Authorized API change | `api-contracts`, framework authorization skill | `application-security`, `code-review` |
| Database migration | `postgresql-migrations` or `mongodb-data-modeling` | `django-orm-transactions`, `backup-recovery` |
| React form or screen | `react-accessibility`, `typescript-contracts` | `api-contracts`, `code-review` |
| Data import/replay | `data-pipeline-reliability`, `python-testing` | database skill, `service-observability` |
| Technical article/post | target-platform writing skill, `technical-explanation` | `content-editing`, `content-review`, `content-repurposing` |
| Research to project guidance | `research-evidence`, `agent-context-workflow` | domain skill, `architecture-decisions` |
| Agent/MCP outcome comparison | `agent-outcome-benchmarking` | `retrieval-evaluation`, `python-testing` |

Load only the relevant bundle and sections, not every skill. Put reusable
procedures in skills, verified project-specific facts in context, and scoped
operational preferences in memory. A content draft does not authorize publication;
a skill describing recovery does not authorize replacing a working database.

The runnable [async Django example](../examples/django-async/README.md) has a
separate environment and real behavioral tests. Its SQLite tests do not establish
PostgreSQL row-lock/concurrent-writer behavior. Django 5.2 documentation is the
baseline; Django's task API is explicitly identified as a 6.0 feature.

## All canonical skills

The following table is regenerated by `docs/guide/build.py` from validated source
metadata. Read the linked file for its applicability, decisions, examples and limits.

<!-- START GENERATED CATALOG -->

| Skill | Version | Category | Applies when |
| --- | --- | --- | --- |
| [API Contracts and Compatibility](../skills/architecture/api-contracts.md) · `api-contracts` | 1.0.0 | architecture | Design and evolve REST or tool API contracts with explicit validation, authorization, retry semantics, pagination, errors and compatibility. Use when clients and services need a stable shared boundary. |
| [Evidence-Based Architecture Decisions](../skills/architecture/decisions.md) · `architecture-decisions` | 1.0.0 | architecture | Evaluate and record architecture decisions with constraints, invariants, alternatives, evidence and rollout/reversal criteria. Use for meaningful design choices rather than routine implementation detail. |
| [Technical Content Ideation](../skills/content/content-ideation.md) · `content-ideation` | 1.1.0 | content | Frame evidence-backed technical content ideas from engineering lessons, architecture decisions, implementation tradeoffs, and useful questions. |
| [Technical Content Repurposing](../skills/content/content-repurposing.md) · `content-repurposing` | 1.1.0 | content | Adapt one verified technical idea for LinkedIn, Medium, and GitHub while preserving provenance, factual limits, and platform-specific purpose. |
| [Technical Content Review](../skills/content/content-review.md) · `content-review` | 1.1.0 | content | Review technical content for factual grounding, source provenance, privacy, clarity, platform fit, and explicit approval before publication. |
| [Technical Content Editing](../skills/content/editing.md) · `content-editing` | 1.1.0 | content | Edit technical writing for evidence, clarity, structure, accessible language, and platform fit while preserving factual limits. |
| [GitHub Technical Documentation Writing](../skills/content/github-writing.md) · `github-writing` | 1.1.0 | content | Write GitHub architecture documentation, READMEs, ADRs, examples, reproducible installation commands, troubleshooting, and API usage guides. |
| [Technical Content Hooks](../skills/content/hooks.md) · `content-hooks` | 1.1.0 | content | Create honest technical openings for LinkedIn posts, Medium articles, documentation, and engineering lessons without hype or engagement bait. |
| [LinkedIn Technical Writing](../skills/content/linkedin-writing.md) · `linkedin-writing` | 1.1.0 | content | Write a LinkedIn post about an engineering lesson, technical insight, architecture decision, career lesson, or concise mini tutorial. |
| [Medium Technical Article Writing](../skills/content/medium-writing.md) · `medium-writing` | 1.1.0 | content | Create a Medium architecture article or long-form technical explanation with first-principles reasoning, implementation detail, and tradeoffs. |
| [Technical Explanation](../skills/content/technical-explanation.md) · `technical-explanation` | 1.1.0 | content | Explain an engineering concept from first principles with definitions, mechanisms, examples, invariants, tradeoffs, and practical application. |
| [Technical Storytelling](../skills/content/technical-storytelling.md) · `technical-storytelling` | 1.1.0 | content | Turn a verified engineering experience into a technical story with setup, tension, explanation, resolution, and a useful lesson. |
| [Async Django Engineering](../skills/django/async-django.md) · `django-async` | 1.0.0 | django | Implement and diagnose Django async views, async ORM evaluation, ASGI middleware boundaries, cancellation, and safe synchronous transaction bridges. Use when concurrency or async-safety is part of the task. |
| [Django Durable Background Jobs](../skills/django/background-jobs.md) · `django-background-jobs` | 1.0.0 | django | Design Django background work with commit-safe scheduling, durable operation identity, retries, outbox delivery, and worker lifecycle. Use for queued tasks and reliable side effects, not request-local asyncio concurrency. |
| [Django ORM and Transaction Design](../skills/django/orm-transactions.md) · `django-orm-transactions` | 1.0.0 | django | Design Django queries, database constraints, atomic state transitions, efficient relationship loading, and safe schema/data migrations. Use for ORM correctness, N+1 problems, races, or migration reviews. |
| [Production Django Engineering](../skills/django/production-django.md) · `django-production` | 1.0.0 | django | Build and review Django applications with explicit domain boundaries, authorized queries, validated input, migration discipline, and production settings. Use for synchronous Django features and service design. |
| [Django REST Framework Contracts](../skills/django/rest-framework.md) · `django-rest-framework` | 1.0.0 | django | Build Django REST Framework APIs with explicit serializers, authorized querysets, object permissions, bounded pagination, schema contracts, and error behavior. Use for DRF views and viewsets. |
| [Django Behavioral and Async Testing](../skills/django/testing.md) · `django-testing` | 1.0.0 | django | Test Django and async Django behavior with appropriate test classes, AsyncClient, real permission/CSRF boundaries, query budgets, commit hooks, and production-engine integration checks. |
| [Production Docker and Compose](../skills/docker/production-docker.md) · `docker-production` | 1.0.0 | operations | Build reproducible containers and operate Compose services with explicit secrets, network boundaries, readiness, persistent state and recovery. Use for Dockerfile, build and deployment changes. |
| [Agent Outcome Benchmarking](../skills/engineering/agent-outcome-benchmarking.md) · `agent-outcome-benchmarking` | 1.0.0 | engineering | Compare coding agents, MCP knowledge delivery and model configurations through behavioral task outcomes, matched controls, actual token/cost accounting and uncertainty. Use for agent benchmark design, execution and evidence review; retrieval relevance alone needs a retrieval evaluation. |
| [Risk-Focused Code Review](../skills/engineering/code-review.md) · `code-review` | 1.0.0 | engineering | Review a change for concrete correctness, authorization, concurrency, migration and operational regressions using evidence from code and tests. Use for requested reviews and high-risk implementation checks. |
| [Reliable Data Pipelines](../skills/engineering/data-pipelines.md) · `data-pipeline-reliability` | 1.0.0 | engineering | Build and review repeatable ingestion and transformation pipelines with explicit schema, deduplication, checkpoints, late-data policy and recovery. Use for batch imports, ETL and analytics jobs. |
| [Systematic Debugging and Incident Diagnosis](../skills/engineering/debugging.md) · `systematic-debugging` | 1.0.0 | engineering | Diagnose failures through a minimal reproduction, bounded hypotheses, boundary evidence and verified repairs. Use for incorrect behavior, intermittent failures and performance regressions. |
| [FastAPI Async Lifespan and Work Budgets](../skills/fastapi/async-lifecycle.md) · `fastapi-async-lifecycle` | 1.0.0 | backend | Design FastAPI lifespan-owned clients, nonblocking request paths, bounded concurrency, cancellation and durable-work boundaries. Use for resource lifecycle and async performance/correctness changes. |
| [FastAPI Authentication and Resource Authorization](../skills/fastapi/authorization.md) · `fastapi-authorization` | 1.0.0 | backend | Implement FastAPI identity verification, dependency-based scope checks and per-resource authorization without trusting client headers or UI filters. Use for authentication, grants, and protected API flows. |
| [FastAPI Production Engineering](../skills/fastapi/production-fastapi.md) · `fastapi-production` | 1.1.0 | backend | Production service boundaries, lifecycle management, and API validation for FastAPI. |
| [FastAPI Boundary and Lifespan Testing](../skills/fastapi/testing.md) · `fastapi-testing` | 1.0.0 | backend | Test FastAPI validation, authorization, serialization, lifecycle, retries and failures at the real HTTP boundary using controlled adapters and appropriate integration infrastructure. |
| [React State and Accessible Interaction](../skills/frontend/react-accessibility.md) · `react-accessibility` | 1.0.0 | frontend | Build React interactions with clear state ownership, accessible forms, focus/error behavior and correct effect cleanup. Use for functional UI work and interaction regressions. |
| [TypeScript Runtime and API Contracts](../skills/frontend/typescript-contracts.md) · `typescript-contracts` | 1.0.0 | frontend | Design TypeScript client contracts with runtime validation, discriminated states, null semantics and compatible API changes. Use for typed frontend/data boundaries rather than cosmetic UI edits. |
| [MongoDB Data Integrity and Query Design](../skills/mongodb/data-modeling.md) · `mongodb-data-modeling` | 1.0.0 | database | Design MongoDB documents, validation, indexes, atomic updates and transactions around real ownership and query workloads. Use for persistence correctness, schema evolution and query performance. |
| [Hybrid Retrieval Evaluation](../skills/mongodb/retrieval-evaluation.md) · `retrieval-evaluation` | 1.0.0 | database | Evaluate and tune lexical/vector/hybrid retrieval using representative queries, authorized candidate sets, deduplication, context budgets and failure-mode evidence. Use for search quality and ranking changes. |
| [Verified Backup and Recovery](../skills/operations/backup-recovery.md) · `backup-recovery` | 1.0.0 | operations | Create and verify backup/recovery procedures that preserve durable state, isolate restore tests and rebuild derived indexes. Use for backup changes and explicitly intended recovery. |
| [Service Observability and Capacity](../skills/operations/observability.md) · `service-observability` | 1.0.0 | operations | Instrument service behavior with bounded safe logs, useful metrics, correlation and capacity evidence. Use for operability, diagnosis and performance work. |
| [Agent Context Retrieval and Handoff](../skills/platform/agent-context-workflow.md) · `agent-context-workflow` | 1.0.0 | platform | Use this platform with explicit project resolution, bounded skill/context retrieval, intentional durable writes, revision checks and cross-agent handoff. Use for Codex, Claude, Gemini or custom MCP workflows. |
| [Research Evidence and Controlled Learning](../skills/platform/research-evidence.md) · `research-evidence` | 1.0.0 | platform | Build evidence-linked research claims and reviewed knowledge candidates without confusing retrieval relevance with truth or automatically promoting generated content. Use for research and controlled learning workflows. |
| [PostgreSQL Schema Evolution](../skills/postgresql/migrations.md) · `postgresql-migrations` | 1.0.0 | database | Plan PostgreSQL schema/index changes around lock behavior, rolling compatibility, bounded backfills and recovery. Use for migrations and persistence changes that can affect live availability. |
| [Python Structured Async Concurrency](../skills/python/async-concurrency.md) · `python-async-concurrency` | 1.0.0 | python | Design Python async I/O with owned tasks and clients, bounded concurrency, deadlines, cancellation cleanup, and blocking-work boundaries. Use for concurrency correctness and event-loop performance. |
| [Strict Python Engineering](../skills/python/strict-python.md) · `strict-python-engineering` | 1.1.0 | python | Typed, testable Python practices for production services. |
| [Python Behavioral Testing](../skills/python/testing.md) · `python-testing` | 1.0.0 | python | Choose and implement Python tests that prove behavior, failure handling, boundary contracts, and isolation. Use for regressions, service changes and test reliability; match test depth to change risk. |
| [Application Security Boundaries](../skills/security/application-security.md) · `application-security` | 1.0.0 | security | Implement and review application trust boundaries, resource authorization, secret handling and untrusted input controls. Use for authentication/authorization changes, ingestion and concrete security-sensitive flows. |

<!-- END GENERATED CATALOG -->

## Maintain and validate

Change a skill's semantic version whenever its bytes change after import. Keep
one canonical file per ID. Write a precise description and tags that help task
selection; keep headings meaningful so independently retrieved sections retain
context. Include specific failures and evidence, without asserting perfection.
Review generated changes as proposals before activating them.

Regenerate documentation with `.venv/bin/python docs/guide/build.py`. This refreshes
the table, embedded skill bodies and `docs/guide/skill-manifest.json`. It does not
import anything into MongoDB or connect a client. See [skills.md](skills.md) for
the parser, version lifecycle and operator contract.
