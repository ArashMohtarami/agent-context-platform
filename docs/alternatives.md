# Choosing a knowledge workflow

Primary source review: **29 October 2026**. These are documentation observations
about the named open-source source snapshots, not a benchmark, security audit or
claim that another project lacks capabilities. No competitor was installed or
measured in our retrieval study. Consult the pinned primary source before choosing.

| Specific edition / source snapshot | Documented focus | When to evaluate it |
| --- | --- | --- |
| [Mem0 OSS library and self-hosted server](https://github.com/mem0ai/mem0/blob/26cdefb46600c0fa5953c4b5ee5003c125539fc4/README.md) | Memory APIs for agent applications; repository distinguishes library, self-hosted server and managed platform. Self-hosted server documents authentication/API keys and a dashboard. | When integrating agent/user memory into an application, or evaluating its self-hosted memory service. Our console, MCP and self-hosting are not unique differentiators. |
| [Graphiti open-source framework](https://github.com/getzep/graphiti/blob/72b5c59c06607e09ea855be61b6ff5a3446fc951/README.md) | Temporal context graphs with source episodes, changing facts and semantic/keyword/graph retrieval; graph backends, MCP and a reference REST service are documented. | When relationship/time queries justify a graph framework and operating its surrounding service. Graphiti is not the same edition as the managed Zep service. |
| [Basic Memory local OSS application](https://github.com/basicmachines-co/basic-memory/blob/739436050886390fc57fbe4c4c947e0a61411803/README.md) | Human-editable Markdown knowledge, MCP, relations and local indexing; local and hosted offerings are described separately. The reviewed local source is AGPL-3.0. | When files, editor access and a local knowledge workflow are central. Do not infer missing search, sharing or integrations from its file-based design. |
| [Context7 MCP/CLI source](https://github.com/upstash/context7/blob/eabe36965ec69b2f3487c4eb329c448dd60b817c/README.md) | Version-aware library/API documentation, library resolution and documentation queries. README distinguishes open client/server source from its private API/parsing/crawling backend. | Complementary to this platform: use it for library documentation, and store your own project's decisions here. A local MCP server is not a claim to self-host its entire documentation service. |

Reviewed source revisions (not tested product releases): Mem0
`26cdefb46600c0fa5953c4b5ee5003c125539fc4`; Graphiti
`72b5c59c06607e09ea855be61b6ff5a3446fc951`; Basic Memory
`739436050886390fc57fbe4c4c947e0a61411803`; Context7
`eabe36965ec69b2f3487c4eb329c448dd60b817c`. Links above pin those exact READMEs.

## Where this platform fits

Small engineering teams with multiple agents/projects can evaluate explicit
project facts, canonical reviewed Git skills, intentional memory and current
coupled action/resource policy. The [first handoff](demo.md) is the concrete
outcome. That control comes with MongoDB/mongot, identity, HTTPS, model and backup
operations. A solo project may be better served by maintained Markdown in Git;
our [local study](retrieval-evaluation.md) found no recall lift over the permitted
Markdown baseline on its fictional heldout. Start with the simpler workflow if
it meets your needs. This is a product-fit judgment, not measured superiority.

Mem0's README explicitly attributes its published performance numbers to its
managed platform, including proprietary optimizations. They are not OSS SDK
measurements and are not compared numerically with our synthetic local corpus.
Other projects' marketing timings, setup estimates and benchmark scores are not
normalized onto our workload either. Equivalent configuration/data, licenses,
current policy, complete budgets and pinned editions would be prerequisites for
a future comparative experiment; none is claimed here.

Our [compatibility table](client-onboarding.md#compatibility-and-tested-environments)
and [verification status](verification.md) state what was and was not exercised.
Advanced [research](dashboard-research.md), [creator](dashboard-creator.md) and
[monitoring](dashboard-monitoring.md) remain separate maintained paths. They are
not steps in the first handoff, and publication stays manual.
