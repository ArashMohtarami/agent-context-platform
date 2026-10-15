"""The unified knowledge search, end to end over in-memory doubles."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from app.db.context_indexes import ContextQueryFilters
from app.db.errors import SearchUnavailableError
from app.db.memory_indexes import MemoryQueryFilters
from app.memory.types import MemoryMatch
from app.projects.types import ProjectContextMatch
from app.retrieval.types import RETRIEVABLE_SOURCES
from app.search.logging import RetrievalLogEntry
from app.services.retrieval_service import RetrievalService
from app.services.search_service import SearchService
from app.skills.types import SkillChunkMatch, SkillVersionKey
from tests.memory_doubles import memory
from tests.project_doubles import FakeEmbedder, context
from tests.unit.test_context_retrieval import FakeContextFactory, FakeContextRepository
from tests.unit.test_hybrid_search_service import (
    FakeChunkRepository,
    FakeSkillRepository,
    chunk,
    skill,
)
from tests.unit.test_memory_retrieval import FakeMemoryFactory, FakeMemorySearchRepository

PROJECT = "sampleco-payment"
OTHER_PROJECT = "sampleco-wallet"
CLAUDE = "claude-code-main"
NOW = datetime(2026, 9, 1, tzinfo=UTC)

SKILL_CONTENT = "Payment requests use idempotency keys everywhere in the payment service."
CONTEXT_CONTENT = "Payment idempotency keys are UUID4 values stored for twenty-four hours."
MEMORY_CONTENT = "Use strict typing in every Python project, checked with mypy in strict mode."


class RecordingLogger:
    """A retrieval hook that remembers every search reported to it."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.entries: list[RetrievalLogEntry] = []

    async def record(self, entry: RetrievalLogEntry) -> None:
        if self.error is not None:
            raise self.error
        self.entries.append(entry)


def context_match(
    score: float, project_id: str = PROJECT, **overrides: object
) -> ProjectContextMatch:
    """Build one scored project-context match."""
    overrides.setdefault("content", CONTEXT_CONTENT)
    document = context(project_id, **overrides)  # type: ignore[arg-type]
    return ProjectContextMatch(context=document, score=score)


def memory_match(score: float, **overrides: object) -> MemoryMatch:
    """Build one scored memory match."""
    overrides.setdefault("content", MEMORY_CONTENT)
    return MemoryMatch(memory=memory(**overrides), score=score)  # type: ignore[arg-type]


def skill_match(score: float, **overrides: object) -> SkillChunkMatch:
    """Build one scored skill-chunk match."""
    overrides.setdefault("content", SKILL_CONTENT)
    return SkillChunkMatch(chunk=chunk(**overrides), score=score)  # type: ignore[arg-type]


def build_search(
    *,
    skill_vector: Sequence[SkillChunkMatch] = (),
    skill_lexical: Sequence[SkillChunkMatch] = (),
    context_vector: Sequence[ProjectContextMatch] = (),
    context_lexical: Sequence[ProjectContextMatch] = (),
    memory_vector: Sequence[MemoryMatch] = (),
    memory_lexical: Sequence[MemoryMatch] = (),
    contexts: FakeContextFactory | None = None,
    memories: FakeMemoryFactory | None = None,
    chunks: FakeChunkRepository | None = None,
    embedder: FakeEmbedder | None = None,
    embeddings_enabled: bool = True,
    logger: RecordingLogger | None = None,
    token_budget: int = 4_000,
    max_token_budget: int = 16_000,
) -> tuple[SearchService, FakeContextFactory, FakeMemoryFactory, FakeChunkRepository]:
    """Compose the unified search over all three retrievable sources."""
    chunk_repository = chunks or FakeChunkRepository(skill_vector, skill_lexical)
    context_factory = contexts or FakeContextFactory(
        {PROJECT: FakeContextRepository(PROJECT, context_vector, context_lexical)}
    )
    memory_factory = memories or FakeMemoryFactory(
        {
            None: FakeMemorySearchRepository(None, memory_vector, memory_lexical),
            PROJECT: FakeMemorySearchRepository(PROJECT, memory_vector, memory_lexical),
        }
    )
    retrieval = RetrievalService(
        FakeSkillRepository([skill()]),
        chunk_repository,
        (FakeEmbedder() if embedder is None else embedder) if embeddings_enabled else None,
        contexts=context_factory,
        memories=memory_factory,
        token_budget=token_budget,
        max_token_budget=max_token_budget,
        clock=lambda: NOW,
    )
    service = SearchService(retrieval, logger=logger)
    return service, context_factory, memory_factory, chunk_repository


@pytest.mark.asyncio
async def test_one_query_answers_from_every_source_at_once() -> None:
    service, contexts, memories, chunks = build_search(
        skill_vector=[skill_match(0.80)],
        context_vector=[context_match(0.82)],
        memory_vector=[memory_match(0.78)],
    )

    result = await service.search_knowledge("payment idempotency", project_id=PROJECT)

    assert {item.source for item in result.context.items} == set(RETRIEVABLE_SOURCES)
    assert result.trace.searched_sources == RETRIEVABLE_SOURCES
    assert result.trace.skipped_sources == ()
    # Every source was searched both ways, in one round of concurrent queries.
    assert {call["method"] for call in chunks.calls} == {"vector", "lexical"}
    assert {call["method"] for call in contexts.by_project[PROJECT].calls} == {
        "vector",
        "lexical",
    }
    assert {call["method"] for call in memories.by_scope[PROJECT].calls} == {"vector", "lexical"}


@pytest.mark.asyncio
async def test_sources_are_ranked_together_rather_than_concatenated() -> None:
    service, _, _, _ = build_search(
        skill_vector=[skill_match(0.90)],
        context_vector=[context_match(0.90, importance=0.5)],
        memory_vector=[memory_match(0.90, importance=0.5)],
    )

    result = await service.search_knowledge("payment idempotency", project_id=PROJECT)

    # Identical similarity and importance, so only source priority separates
    # them: what a project recorded about itself, then generic knowledge, then
    # what was remembered in general.
    assert [item.source for item in result.context.items] == [
        "project_context",
        "active_skill",
        "memory",
    ]


@pytest.mark.asyncio
async def test_project_context_outranks_agent_operational_memory_at_equal_relevance() -> None:
    memories = FakeMemoryFactory(
        {
            (PROJECT, CLAUDE): FakeMemorySearchRepository(
                PROJECT,
                [
                    memory_match(
                        0.8,
                        scope="agent",
                        client_id=CLAUDE,
                        content="Claude's local operational checkpoint.",
                    )
                ],
                client_id=CLAUDE,
            )
        }
    )
    service, _, _, _ = build_search(
        context_vector=[context_match(0.8)],
        memories=memories,
    )

    result = await service.search_knowledge(
        "payment idempotency",
        project_id=PROJECT,
        memory_client_id=CLAUDE,
    )

    assert [item.source for item in result.context.items] == ["project_context", "memory"]
    assert [item.score for item in result.context.items] == sorted(
        (item.score for item in result.context.items),
        reverse=True,
    )


@pytest.mark.asyncio
async def test_importance_can_lift_a_lower_priority_source_above_a_higher_one() -> None:
    service, _, _, _ = build_search(
        skill_vector=[skill_match(0.90)],
        memory_vector=[memory_match(0.90, importance=0.8)],
    )

    result = await service.search_knowledge("payment idempotency")

    # A skill carries no curator-assigned importance and scores neutrally at
    # 0.5, so a memory somebody rated 0.8 outweighs the 0.15 source-priority
    # gap. Ranking combines the signals rather than ordering by source.
    assert [item.source for item in result.context.items] == ["memory", "active_skill"]


@pytest.mark.asyncio
async def test_a_source_that_was_not_asked_for_is_never_searched_or_returned() -> None:
    service, contexts, memories, chunks = build_search(
        skill_vector=[skill_match(0.80)],
        context_vector=[context_match(0.99)],
        memory_vector=[memory_match(0.99)],
    )

    result = await service.search_knowledge(
        "payment idempotency",
        project_id=PROJECT,
        sources=["active_skill"],
    )

    assert [item.source for item in result.context.items] == ["active_skill"]
    assert contexts.by_project[PROJECT].calls == []
    assert memories.by_scope[PROJECT].calls == []
    assert chunks.calls != []
    assert result.trace.requested_sources == ("active_skill",)
    assert result.trace.skipped_sources == ()


@pytest.mark.asyncio
async def test_an_unrequested_source_is_refused_at_the_filter_as_well() -> None:
    # Skipping the search and filtering the results are two separate defences.
    # Gathering every source directly proves the context entry is reachable, so
    # the unified search returning only memory is the filter doing its work.
    contexts = FakeContextFactory({PROJECT: FakeContextRepository(PROJECT, [context_match(0.99)])})
    memories = FakeMemoryFactory(
        {PROJECT: FakeMemorySearchRepository(PROJECT, [memory_match(0.7)])}
    )
    retrieval = RetrievalService(
        FakeSkillRepository([skill()]),
        FakeChunkRepository(),
        FakeEmbedder(),
        contexts=contexts,
        memories=memories,
        clock=lambda: NOW,
    )
    unrestricted = await retrieval.gather_sources("payment idempotency", project_id=PROJECT)

    filtered = await SearchService(retrieval).search_knowledge(
        "payment idempotency",
        project_id=PROJECT,
        sources=["memory"],
    )

    assert any(match.candidate.source == "project_context" for match in unrestricted.vector)
    assert [item.source for item in filtered.context.items] == ["memory"]


@pytest.mark.asyncio
async def test_a_conversation_can_be_requested_and_is_reported_as_unsearched() -> None:
    service, _, _, _ = build_search(memory_vector=[memory_match(0.80)])

    result = await service.search_knowledge(
        "payment idempotency",
        sources=["conversation", "memory"],
    )

    # Conversations are stored but carry no index, so asking for one is
    # answered honestly instead of silently returning nothing.
    assert result.trace.requested_sources == ("memory", "conversation")
    assert result.trace.searched_sources == ("memory",)
    assert result.trace.skipped_sources == ("conversation",)
    assert [item.source for item in result.context.items] == ["memory"]


@pytest.mark.asyncio
async def test_asking_only_for_conversations_returns_nothing_and_says_why() -> None:
    service, _, _, chunks = build_search(skill_vector=[skill_match(0.99)])

    result = await service.search_knowledge("payment idempotency", sources=["conversation"])

    assert result.context.items == ()
    assert result.trace.skipped_sources == ("conversation",)
    assert chunks.calls == []


@pytest.mark.asyncio
async def test_a_request_naming_no_project_reaches_no_project_owned_knowledge() -> None:
    service, contexts, memories, _ = build_search(
        skill_vector=[skill_match(0.80)],
        memory_vector=[memory_match(0.90), memory_match(0.85, project_id=PROJECT)],
    )

    result = await service.search_knowledge("payment idempotency")

    assert contexts.requested == []
    assert memories.requested == [None]
    assert {item.metadata.get("project_id") for item in result.context.items} == {None}
    assert result.trace.searched_sources == ("active_skill", "memory")
    assert result.trace.skipped_sources == ("project_context",)


@pytest.mark.asyncio
async def test_another_project_is_never_searched_for_context_or_memory() -> None:
    wallet_context = FakeContextRepository(OTHER_PROJECT, [context_match(0.99, OTHER_PROJECT)])
    wallet_memory = FakeMemorySearchRepository(
        OTHER_PROJECT,
        [memory_match(0.99, project_id=OTHER_PROJECT)],
    )
    service, _, _, _ = build_search(
        contexts=FakeContextFactory({OTHER_PROJECT: wallet_context}),
        memories=FakeMemoryFactory({OTHER_PROJECT: wallet_memory}),
    )

    result = await service.search_knowledge("payment idempotency", project_id=PROJECT)

    assert wallet_context.calls == []
    assert wallet_memory.calls == []
    assert result.context.items == ()


@pytest.mark.asyncio
async def test_another_project_s_result_is_dropped_even_if_a_search_returns_it() -> None:
    # Both repositories are scoped by construction; this proves the unified
    # search filters again rather than trusting what a search hands back.
    leaking_context = FakeContextRepository(PROJECT, [context_match(0.99, OTHER_PROJECT)])
    leaking_memory = FakeMemorySearchRepository(
        PROJECT,
        [memory_match(0.99, project_id=OTHER_PROJECT)],
    )
    service, _, _, _ = build_search(
        contexts=FakeContextFactory({PROJECT: leaking_context}),
        memories=FakeMemoryFactory({PROJECT: leaking_memory}),
    )

    result = await service.search_knowledge("payment idempotency", project_id=PROJECT)

    assert result.context.items == ()
    assert result.context.diagnostics.filtered_out == 2


@pytest.mark.asyncio
async def test_global_memory_reaches_every_request_while_project_memory_does_not() -> None:
    service, _, memories, _ = build_search()
    memories.by_scope[PROJECT].vector = [
        memory_match(0.90, content=MEMORY_CONTENT),
        memory_match(0.88, project_id=PROJECT, content=CONTEXT_CONTENT),
    ]

    result = await service.search_knowledge("strict typing", project_id=PROJECT)

    scope = memories.by_scope[PROJECT].calls[0]["filters"]
    assert isinstance(scope, MemoryQueryFilters)
    assert set(scope.scopes) == {"global", "project"}
    assert {item.metadata["scope"] for item in result.context.items} == {"global", "project"}


@pytest.mark.asyncio
async def test_memory_can_be_narrowed_to_one_scope() -> None:
    service, _, memories, _ = build_search(memory_vector=[memory_match(0.90, project_id=PROJECT)])

    await service.search_knowledge(
        "strict typing",
        project_id=PROJECT,
        memory_scopes=["project"],
    )

    scope = memories.by_scope[PROJECT].calls[0]["filters"]
    assert isinstance(scope, MemoryQueryFilters)
    assert scope.scopes == ("project",)


@pytest.mark.asyncio
async def test_an_exact_identifier_reaches_the_result_only_lexical_search_found() -> None:
    identifier = FakeMemorySearchRepository(
        None,
        lexical=[memory_match(9.0, content="The scope wallet:read grants read-only access.")],
    )
    service, _, _, _ = build_search(
        memories=FakeMemoryFactory({None: identifier}),
        skill_vector=[skill_match(0.30, content="Some conceptually adjacent prose about money.")],
    )

    result = await service.search_knowledge("wallet:read")

    lexical = next(call for call in identifier.calls if call["method"] == "lexical")
    assert lexical["query"] == "wallet:read"
    assert result.context.items[0].source == "memory"
    assert result.context.items[0].explanation.lexical == 1.0
    assert result.context.items[0].explanation.vector == 0.0


@pytest.mark.asyncio
async def test_a_semantic_query_reaches_the_result_only_vector_search_found() -> None:
    service, _, _, _ = build_search(
        context_vector=[context_match(0.95)],
        skill_lexical=[skill_match(1.0, content="Unrelated prose that shares a word or two.")],
    )

    result = await service.search_knowledge(
        "how do we avoid charging twice",
        project_id=PROJECT,
    )

    assert result.context.items[0].source == "project_context"
    assert result.context.items[0].explanation.vector == 0.95


@pytest.mark.asyncio
async def test_agreement_between_both_searches_outranks_a_single_strong_hit() -> None:
    agreed = memory_match(0.70)
    service, _, _, _ = build_search(
        memory_vector=[agreed],
        memory_lexical=[agreed],
        context_vector=[context_match(0.80)],
    )

    result = await service.search_knowledge("payment idempotency", project_id=PROJECT)

    assert [item.source for item in result.context.items] == ["memory", "project_context"]


@pytest.mark.asyncio
async def test_the_token_budget_bounds_the_context_that_comes_back() -> None:
    service, _, _, _ = build_search(
        skill_vector=[
            skill_match(0.90, chunk_id="chunk-0000", tokens=300),
            skill_match(0.80, chunk_id="chunk-0001", tokens=300, content="Another rule entirely."),
        ],
        context_vector=[context_match(0.95)],
    )

    result = await service.search_knowledge(
        "payment idempotency",
        project_id=PROJECT,
        token_budget=400,
    )

    assert result.context.token_count <= 400
    assert result.context.token_budget == 400
    assert result.context.diagnostics.dropped_for_budget >= 1


@pytest.mark.asyncio
async def test_a_request_can_ask_for_less_context_but_never_for_more() -> None:
    service, _, _, _ = build_search(token_budget=1_000, max_token_budget=2_000)

    modest = await service.search_knowledge("payment idempotency", token_budget=500)
    greedy = await service.search_knowledge("payment idempotency", token_budget=10_000)

    assert modest.context.token_budget == 500
    assert greedy.context.token_budget == 2_000


@pytest.mark.asyncio
async def test_an_impossible_budget_is_refused_before_any_search_runs() -> None:
    service, _, _, chunks = build_search()

    with pytest.raises(ValueError, match="token budget"):
        await service.search_knowledge("payment idempotency", token_budget=1)

    assert chunks.calls == []


@pytest.mark.asyncio
async def test_results_are_never_unbounded() -> None:
    service, _, _, _ = build_search(
        memory_vector=[
            memory_match(0.9 - index / 100, content=f"Distinct memory number {index} of many.")
            for index in range(40)
        ],
    )

    result = await service.search_knowledge("payment idempotency", limit=5)

    assert len(result.context.items) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 51, True])
async def test_a_limit_outside_the_supported_range_is_rejected(limit: int) -> None:
    service, _, _, chunks = build_search()

    with pytest.raises(ValueError, match="limit must be between"):
        await service.search_knowledge("payment idempotency", limit=limit)

    assert chunks.calls == []


@pytest.mark.asyncio
async def test_a_result_a_better_ranked_one_already_covers_is_returned_once() -> None:
    repeated = "Payment requests carry a UUID4 idempotency key stored for twenty-four hours."
    service, _, _, _ = build_search(
        skill_vector=[skill_match(0.90, content=repeated)],
        context_vector=[context_match(0.95, content=repeated)],
    )

    result = await service.search_knowledge("payment idempotency", project_id=PROJECT)

    assert len(result.context.items) == 1
    assert result.context.items[0].source == "project_context"
    assert result.context.diagnostics.duplicates_removed == 1


@pytest.mark.asyncio
async def test_every_shared_filter_reaches_the_source_it_applies_to() -> None:
    service, contexts, memories, _ = build_search()

    await service.search_knowledge(
        "payment idempotency",
        project_id=PROJECT,
        tags=["python"],
        minimum_importance=0.4,
        context_types=["architecture_decision"],
        memory_types=["preference"],
        phrase=True,
    )

    context_calls = contexts.by_project[PROJECT].calls
    memory_calls = memories.by_scope[PROJECT].calls
    context_scope = context_calls[0]["filters"]
    memory_scope = memory_calls[0]["filters"]
    assert isinstance(context_scope, ContextQueryFilters)
    assert isinstance(memory_scope, MemoryQueryFilters)
    assert context_scope.types == ("architecture_decision",)
    assert context_scope.tags == ("python",)
    assert context_scope.minimum_importance == 0.4
    assert memory_scope.types == ("preference",)
    assert memory_scope.tags == ("python",)
    assert memory_scope.minimum_importance == 0.4
    for calls in (context_calls, memory_calls):
        assert next(call for call in calls if call["method"] == "lexical")["phrase"] is True


@pytest.mark.asyncio
async def test_skill_metadata_filters_reach_the_chunk_search_as_a_prefilter() -> None:
    service, _, _, chunks = build_search(skill_vector=[skill_match(0.80)])

    await service.search_knowledge(
        "payment idempotency",
        category="backend",
        tags=["python"],
        trust_level="trusted",
    )

    lexical = next(call for call in chunks.calls if call["method"] == "lexical")
    assert lexical["skill_versions"] == (SkillVersionKey("fastapi-production", "1.0.0"),)


@pytest.mark.asyncio
async def test_a_skill_only_filter_narrows_the_answer_to_skills_and_says_so() -> None:
    service, contexts, memories, _ = build_search(
        skill_vector=[skill_match(0.80)],
        context_vector=[context_match(0.99)],
        memory_vector=[memory_match(0.99)],
    )

    result = await service.search_knowledge(
        "payment idempotency",
        project_id=PROJECT,
        category="backend",
    )

    # A category describes a skill. Searching context and memory would only
    # produce candidates this filter then discards, which would read like both
    # sources being empty rather than out of scope.
    assert [item.source for item in result.context.items] == ["active_skill"]
    assert contexts.by_project[PROJECT].calls == []
    assert memories.by_scope[PROJECT].calls == []
    assert result.trace.searched_sources == ("active_skill",)
    assert result.trace.skipped_sources == ("project_context", "memory")


@pytest.mark.asyncio
async def test_a_source_that_found_nothing_still_counts_as_searched() -> None:
    service, _, _, chunks = build_search(memory_vector=[memory_match(0.80)])

    result = await service.search_knowledge("payment idempotency")

    assert [item.source for item in result.context.items] == ["memory"]
    assert chunks.calls != []
    assert result.trace.searched_sources == ("active_skill", "memory")
    assert result.trace.skipped_sources == ("project_context",)


@pytest.mark.asyncio
async def test_a_deployment_with_no_skills_answers_from_the_sources_it_has() -> None:
    retrieval = RetrievalService(
        FakeSkillRepository([]),
        FakeChunkRepository(),
        FakeEmbedder(),
        memories=FakeMemoryFactory({None: FakeMemorySearchRepository(None, [memory_match(0.8)])}),
        clock=lambda: NOW,
    )

    result = await SearchService(retrieval).search_knowledge("payment idempotency")

    assert [item.source for item in result.context.items] == ["memory"]
    assert result.trace.searched_sources == ("memory",)
    assert result.trace.skipped_sources == ("project_context", "active_skill")


@pytest.mark.asyncio
async def test_one_exact_skill_version_can_be_targeted() -> None:
    service, _, _, chunks = build_search(skill_vector=[skill_match(0.80)])

    await service.search_knowledge(
        "payment idempotency",
        skill_id="fastapi-production",
        skill_version="1.0.0",
    )

    assert chunks.calls[0]["skill_versions"] == (SkillVersionKey("fastapi-production", "1.0.0"),)


@pytest.mark.asyncio
async def test_a_version_without_its_skill_is_rejected() -> None:
    service, _, _, chunks = build_search()

    with pytest.raises(ValueError, match="skill_version requires skill_id"):
        await service.search_knowledge("payment idempotency", skill_version="1.0.0")

    assert chunks.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
async def test_an_empty_query_is_rejected_before_anything_is_searched(query: str) -> None:
    service, _, memories, chunks = build_search()

    with pytest.raises(ValueError, match="must not be empty"):
        await service.search_knowledge(query)

    assert chunks.calls == []
    assert memories.requested == []


@pytest.mark.asyncio
async def test_the_query_reaches_every_source_normalized_the_same_way() -> None:
    embedder = FakeEmbedder()
    service, contexts, memories, chunks = build_search(embedder=embedder)

    result = await service.search_knowledge("  wallet:read   OAuth2 ", project_id=PROJECT)

    assert result.context.query == "wallet:read OAuth2"
    # One embedding, not one per source. Every source searches the same query,
    # so they can share the vector it produces — and producing it costs more
    # than every index query in the retrieval put together.
    assert embedder.texts == ["wallet:read OAuth2"]
    for call in (
        *chunks.calls,
        *contexts.by_project[PROJECT].calls,
        *memories.by_scope[PROJECT].calls,
    ):
        if call["method"] == "lexical":
            assert call["query"] == "wallet:read OAuth2"


@pytest.mark.asyncio
async def test_equivalent_searches_produce_identical_results() -> None:
    matches = {
        "skill_vector": [skill_match(0.80)],
        "context_vector": [context_match(0.82)],
        "memory_vector": [memory_match(0.78)],
    }
    first, _, _, _ = build_search(**matches)  # type: ignore[arg-type]
    second, _, _, _ = build_search(**matches)  # type: ignore[arg-type]

    one = await first.search_knowledge("payment idempotency", project_id=PROJECT)
    other = await second.search_knowledge("payment idempotency", project_id=PROJECT)

    assert [(item.key, item.score) for item in one.context.items] == [
        (item.key, item.score) for item in other.context.items
    ]
    # The query identifier is the one part that must differ every time.
    assert one.trace.query_id != other.trace.query_id


@pytest.mark.asyncio
async def test_a_deployment_without_an_embedding_model_still_answers_lexically() -> None:
    service, _, _, _ = build_search(
        embeddings_enabled=False,
        memory_lexical=[memory_match(4.0)],
        skill_lexical=[skill_match(3.0)],
    )

    result = await service.search_knowledge("payment idempotency")

    assert result.trace.retrieval_method == "lexical"
    assert result.context.diagnostics.methods == ("lexical",)
    # The vector weight is redistributed rather than scored against nothing.
    assert result.context.diagnostics.weights["vector"] == 0.0
    # Each source's lexical score is scaled against the best result of its own
    # search, so 4.0 and 3.0 both normalize to 1.0 and importance decides.
    assert [item.source for item in result.context.items] == ["memory", "active_skill"]
    assert {item.explanation.lexical for item in result.context.items} == {1.0}


@pytest.mark.asyncio
async def test_unavailable_search_infrastructure_is_reported_not_hidden() -> None:
    service, _, _, _ = build_search(
        chunks=FakeChunkRepository(error=SearchUnavailableError("index is not queryable")),
    )

    with pytest.raises(SearchUnavailableError):
        await service.search_knowledge("payment idempotency")


@pytest.mark.asyncio
async def test_every_result_reports_its_source_score_and_metadata() -> None:
    service, _, _, _ = build_search(context_vector=[context_match(0.90)])

    result = await service.search_knowledge("payment idempotency", project_id=PROJECT)

    item = result.context.items[0]
    assert item.source == "project_context"
    assert item.title == "Payment idempotency strategy"
    assert 0.0 < item.score <= 1.0
    assert item.metadata["project_id"] == PROJECT
    assert item.metadata["type"] == "architecture_decision"
    assert item.key.startswith(f"project_context:{PROJECT}:")


@pytest.mark.asyncio
async def test_the_trace_reports_what_the_pipeline_did() -> None:
    service, _, _, _ = build_search(
        skill_vector=[skill_match(0.90)],
        memory_vector=[memory_match(0.80)],
    )

    result = await service.search_knowledge("payment idempotency", limit=10)

    assert result.trace.retrieval_method == "hybrid"
    assert result.trace.latency_ms >= 0.0
    assert len(result.trace.query_id) == 32
    diagnostics = result.context.diagnostics
    assert diagnostics.vector_matches == 2
    assert diagnostics.merged == 2
    assert diagnostics.ranked == 2


@pytest.mark.asyncio
async def test_the_search_is_reported_to_the_retrieval_hook() -> None:
    logger = RecordingLogger()
    service, _, _, _ = build_search(
        memory_vector=[memory_match(0.80)],
        logger=logger,
    )

    result = await service.search_knowledge("payment idempotency", project_id=PROJECT)

    recorded = logger.entries[0]
    assert recorded.query == "payment idempotency"
    assert recorded.project_id == PROJECT
    assert recorded.query_id == result.trace.query_id
    assert recorded.requested_sources == RETRIEVABLE_SOURCES
    assert recorded.result_count == len(result.context.items)
    assert recorded.selected_result_ids == result.result_keys
    assert recorded.retrieval_method == "hybrid"


@pytest.mark.asyncio
async def test_a_search_answers_even_when_its_hook_raises() -> None:
    # The composite hook swallows failures; a bare hook that raises would fail
    # the search, which is why the wiring never installs one.
    logger = RecordingLogger(error=RuntimeError("the log is unreachable"))
    service, _, _, _ = build_search(memory_vector=[memory_match(0.80)], logger=logger)

    with pytest.raises(RuntimeError):
        await service.search_knowledge("payment idempotency")

    assert logger.entries == []


@pytest.mark.asyncio
async def test_a_search_without_a_hook_still_answers() -> None:
    service, _, _, _ = build_search(memory_vector=[memory_match(0.80)])

    result = await service.search_knowledge("payment idempotency")

    assert [item.source for item in result.context.items] == ["memory"]


@pytest.mark.asyncio
async def test_a_unified_search_embeds_the_query_exactly_once() -> None:
    # Measured under load before this was true: embedding a query took 855ms at
    # the median while a whole index query took about four, and a unified
    # search was paying that cost three times — once per source — for the same
    # text. Everything else in retrieval is cheap by comparison, so this is the
    # number that decides how much traffic one process can serve.
    embedder = FakeEmbedder()
    service, _, _, _ = build_search(
        embedder=embedder,
        skill_vector=[skill_match(0.9)],
        context_vector=[context_match(0.8)],
        memory_vector=[memory_match(0.7)],
    )

    await service.search_knowledge("payment idempotency", project_id=PROJECT)

    assert len(embedder.texts) == 1


@pytest.mark.asyncio
async def test_a_search_with_nothing_to_search_embeds_nothing() -> None:
    # The vector is produced for the sources about to use one. A request that
    # reaches none of them should not pay for one.
    embedder = FakeEmbedder()
    service, _, _, _ = build_search(embedder=embedder)

    await service.search_knowledge("anything", sources=["active_skill"], skill_id="absent-skill")

    assert embedder.texts == []


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
