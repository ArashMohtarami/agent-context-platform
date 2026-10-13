"""Memory inside hybrid retrieval, and memory-only search."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from app.db.errors import SearchUnavailableError
from app.db.memory_indexes import MemoryQueryFilters
from app.memory.errors import MemoryRetrievalUnavailableError
from app.memory.types import MemoryMatch
from app.services.retrieval_service import RetrievalService, memory_candidate
from app.skills.types import SkillChunkMatch
from tests.memory_doubles import memory
from tests.project_doubles import FakeEmbedder
from tests.unit.test_hybrid_search_service import (
    FakeChunkRepository,
    FakeSkillRepository,
    chunk,
    skill,
)

PROJECT = "sampleco-payment"
OTHER_PROJECT = "sampleco-wallet"
CLAUDE = "claude-code-main"
CODEX = "codex-main"
NOW = datetime(2026, 9, 1, tzinfo=UTC)


class FakeMemorySearchRepository:
    """Return prepared memory matches and record how the query was scoped."""

    def __init__(
        self,
        project_id: str | None,
        vector: Sequence[MemoryMatch] = (),
        lexical: Sequence[MemoryMatch] = (),
        *,
        client_id: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.project_id = project_id
        self.client_id = client_id
        self.vector = list(vector)
        self.lexical = list(lexical)
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 10,
        num_candidates: int,
        filters: MemoryQueryFilters,
    ) -> tuple[MemoryMatch, ...]:
        self.calls.append({"method": "vector", "limit": limit, "filters": filters})
        if self.error is not None:
            raise self.error
        return tuple(self.vector[:limit])

    async def text_search(
        self,
        query: str,
        *,
        limit: int = 10,
        phrase: bool = False,
        filters: MemoryQueryFilters,
    ) -> tuple[MemoryMatch, ...]:
        self.calls.append(
            {
                "method": "lexical",
                "query": query,
                "limit": limit,
                "phrase": phrase,
                "filters": filters,
            }
        )
        if self.error is not None:
            raise self.error
        return tuple(self.lexical[:limit])


class FakeMemoryFactory:
    """Build one memory repository per scope, remembering every request."""

    def __init__(
        self,
        by_scope: dict[str | None | tuple[str | None, str | None], FakeMemorySearchRepository]
        | None = None,
    ) -> None:
        self.by_scope: dict[
            str | None | tuple[str | None, str | None], FakeMemorySearchRepository
        ] = {}
        self._by_address: dict[tuple[str | None, str | None], FakeMemorySearchRepository] = {}
        for key, repository in (by_scope or {}).items():
            resolved_key = key if isinstance(key, tuple) else (key, None)
            self._by_address[resolved_key] = repository
            self.by_scope[key] = repository
        self.requested: list[str | None | tuple[str | None, str]] = []

    def __call__(
        self, project_id: str | None, client_id: str | None = None
    ) -> FakeMemorySearchRepository:
        self.requested.append(project_id if client_id is None else (project_id, client_id))
        key = (project_id, client_id)
        if key not in self._by_address:
            repository = FakeMemorySearchRepository(project_id, client_id=client_id)
            self._by_address[key] = repository
            self.by_scope[key if client_id is not None else project_id] = repository
        return self._by_address[key]


def build_service(
    *,
    memory_vector: Sequence[MemoryMatch] = (),
    memory_lexical: Sequence[MemoryMatch] = (),
    skill_vector: Sequence[SkillChunkMatch] = (),
    skill_lexical: Sequence[SkillChunkMatch] = (),
    memory_error: Exception | None = None,
    memories: FakeMemoryFactory | None = None,
    embedder: FakeEmbedder | None = None,
) -> tuple[RetrievalService, FakeMemoryFactory]:
    """Compose retrieval over in-memory skill and memory doubles."""
    factory = memories or FakeMemoryFactory(
        {
            None: FakeMemorySearchRepository(
                None, memory_vector, memory_lexical, error=memory_error
            ),
            PROJECT: FakeMemorySearchRepository(
                PROJECT,
                memory_vector,
                memory_lexical,
                error=memory_error,
            ),
        }
    )
    service = RetrievalService(
        FakeSkillRepository([skill()]),
        FakeChunkRepository(skill_vector, skill_lexical),
        FakeEmbedder() if embedder is None else embedder,
        memories=factory,
        clock=lambda: NOW,
    )
    return service, factory


def match(score: float, **overrides: object) -> MemoryMatch:
    """Build one scored memory match."""
    return MemoryMatch(memory=memory(**overrides), score=score)  # type: ignore[arg-type]


def test_a_memory_candidate_carries_its_scope_importance_and_lifecycle() -> None:
    document = memory(PROJECT, importance=0.7, summary="Idempotency keys", status="archived")

    candidate = memory_candidate(document)

    assert candidate.source == "memory"
    assert candidate.project_id == PROJECT
    assert candidate.importance == 0.7
    assert candidate.is_active is False
    assert candidate.title == "Idempotency keys"
    assert candidate.metadata["memory_id"] == str(document.id)
    assert candidate.metadata["scope"] == "project"
    assert candidate.metadata["confidence"] == document.confidence
    assert candidate.key.startswith(f"memory:project:{PROJECT}:")


def test_a_memory_without_a_summary_is_titled_by_its_type() -> None:
    candidate = memory_candidate(memory(memory_type="preference"))

    assert candidate.title == "preference memory"
    assert candidate.project_id is None
    assert candidate.key.startswith("memory:global:global:")


def test_a_superseded_memory_reports_both_ends_of_the_link() -> None:
    replacement = memory(content="The corrected rule.")
    replaced = memory(
        content="The old rule.",
        status="superseded",
        superseded_by=replacement.id,
    )

    candidate = memory_candidate(replaced)

    assert candidate.metadata["superseded_by"] == str(replacement.id)
    assert candidate.is_active is False


@pytest.mark.asyncio
async def test_global_memory_reaches_a_request_that_names_no_project() -> None:
    service, factory = build_service(memory_vector=[match(0.9)])

    result = await service.hybrid_search("strict typing")

    assert [item.source for item in result.items] == ["memory"]
    assert factory.requested == [None]


@pytest.mark.asyncio
async def test_a_project_request_reaches_global_memory_and_its_own() -> None:
    service, factory = build_service()
    repository = factory(PROJECT)
    repository.vector = [
        match(0.9, content="Use strict typing in Python projects."),
        match(0.8, project_id=PROJECT, content="Payment retries stop after three attempts."),
    ]
    factory.requested.clear()

    result = await service.hybrid_search("strict typing", project_id=PROJECT)

    scope = repository.calls[0]["filters"]
    assert isinstance(scope, MemoryQueryFilters)
    assert set(scope.scopes) == {"global", "project"}
    assert scope.project_id == PROJECT
    assert {item.metadata["scope"] for item in result.items} == {"global", "project"}


@pytest.mark.asyncio
async def test_agent_retrieval_includes_shared_and_own_operational_memory_only() -> None:
    repository = FakeMemorySearchRepository(
        PROJECT,
        [
            match(0.9, content="Shared engineering convention."),
            match(0.8, project_id=PROJECT, content="Payment-service architecture."),
            match(
                0.7,
                scope="agent",
                client_id=CLAUDE,
                content="Claude's active worktree note.",
            ),
            match(
                0.6,
                scope="project_agent",
                project_id=PROJECT,
                client_id=CLAUDE,
                content="Claude's payment-service checkpoint.",
            ),
            match(
                0.99,
                scope="agent",
                client_id=CODEX,
                content="Codex-only operational note.",
            ),
        ],
        client_id=CLAUDE,
    )
    factory = FakeMemoryFactory({(PROJECT, CLAUDE): repository})
    service, _ = build_service(memories=factory)

    result = await service.hybrid_search(
        "payment work", project_id=PROJECT, memory_client_id=CLAUDE
    )

    scope = repository.calls[0]["filters"]
    assert isinstance(scope, MemoryQueryFilters)
    assert set(scope.scopes) == {"global", "project", "agent", "project_agent"}
    assert scope.client_id == CLAUDE
    assert {item.content for item in result.items} == {
        "Shared engineering convention.",
        "Payment-service architecture.",
        "Claude's active worktree note.",
        "Claude's payment-service checkpoint.",
    }
    assert result.diagnostics.filtered_out == 1


@pytest.mark.asyncio
async def test_agent_scope_requires_gateway_derived_client_identity() -> None:
    service, factory = build_service()

    with pytest.raises(ValueError, match="authenticated client"):
        await service.memory_search("payment work", scopes=["agent"])

    assert factory.requested == []


@pytest.mark.asyncio
async def test_a_request_naming_no_project_searches_global_memory_only() -> None:
    service, factory = build_service()
    factory(None).vector = [match(0.9)]
    factory.requested.clear()

    await service.hybrid_search("strict typing")

    scope = factory(None).calls[0]["filters"]
    assert isinstance(scope, MemoryQueryFilters)
    assert scope.scopes == ("global",)
    assert scope.project_id is None


@pytest.mark.asyncio
async def test_another_project_s_memory_is_dropped_even_if_a_search_returns_it() -> None:
    # The repository is scoped by construction; this proves the service filters
    # again rather than trusting what a search hands back.
    leaking = FakeMemorySearchRepository(PROJECT, [match(0.99, project_id=OTHER_PROJECT)])
    factory = FakeMemoryFactory({PROJECT: leaking})
    service, _ = build_service(memories=factory)

    result = await service.hybrid_search("strict typing", project_id=PROJECT)

    assert [item.source for item in result.items] == []
    assert result.diagnostics.filtered_out == 1


@pytest.mark.asyncio
async def test_memory_ranks_below_project_context_priority_and_above_conversation() -> None:
    service, _ = build_service(memory_vector=[match(0.8)])

    result = await service.hybrid_search("strict typing")

    assert result.items[0].explanation.source_priority == 0.80


@pytest.mark.asyncio
async def test_a_skill_outranks_a_memory_at_equal_relevance() -> None:
    service, _ = build_service(
        memory_vector=[match(0.8, importance=0.5)],
        skill_vector=[SkillChunkMatch(chunk=chunk(), score=0.8)],
    )

    result = await service.hybrid_search("strict typing")

    # Identical similarity and importance: only source priority separates
    # 0.95 from 0.80.
    assert [item.source for item in result.items] == ["active_skill", "memory"]


@pytest.mark.asyncio
async def test_archived_memory_is_excluded_unless_it_is_asked_for() -> None:
    service, factory = build_service(memory_vector=[match(0.9, status="archived")])

    default = await service.hybrid_search("strict typing")
    including = await service.hybrid_search("strict typing", active_only=False)

    assert default.items == ()
    assert [item.source for item in including.items] == ["memory"]
    scope = factory(None).calls[0]["filters"]
    assert isinstance(scope, MemoryQueryFilters)
    assert scope.status == "active"


@pytest.mark.asyncio
async def test_superseded_memory_is_not_retrieved_by_default() -> None:
    service, _ = build_service(memory_vector=[match(0.95, status="superseded")])

    result = await service.hybrid_search("strict typing")

    assert result.items == ()


@pytest.mark.asyncio
async def test_memory_search_ranks_the_memory_a_request_may_see() -> None:
    first = match(0.9, content="Use strict typing in Python projects.")
    second = match(0.4, content="Prefer production-grade solutions over demos.")
    service, _ = build_service(memory_vector=[first, second])

    results = await service.memory_search("strict typing")

    assert [result.memory.content for result in results] == [
        "Use strict typing in Python projects.",
        "Prefer production-grade solutions over demos.",
    ]
    assert results[0].score >= results[1].score
    assert results[0].methods == ("vector",)


@pytest.mark.asyncio
async def test_memory_search_passes_every_filter_to_the_repository() -> None:
    service, factory = build_service()

    await service.memory_search(
        "strict typing",
        project_id=PROJECT,
        phrase=True,
        memory_types=["preference"],
        tags=["python"],
        status="archived",
        minimum_importance=0.3,
        limit=7,
    )

    lexical = next(call for call in factory(PROJECT).calls if call["method"] == "lexical")
    scope = lexical["filters"]
    assert isinstance(scope, MemoryQueryFilters)
    assert scope.types == ("preference",)
    assert scope.tags == ("python",)
    assert scope.status == "archived"
    assert scope.minimum_importance == 0.3
    assert lexical["phrase"] is True
    assert lexical["limit"] == 7


@pytest.mark.asyncio
async def test_memory_search_can_be_restricted_to_one_scope() -> None:
    service, factory = build_service()

    await service.memory_search("strict typing", project_id=PROJECT, scopes=["project"])

    scope = factory(PROJECT).calls[0]["filters"]
    assert isinstance(scope, MemoryQueryFilters)
    assert scope.scopes == ("project",)


@pytest.mark.asyncio
async def test_memory_search_never_reaches_another_project() -> None:
    wallet = FakeMemorySearchRepository(OTHER_PROJECT, [match(0.99, project_id=OTHER_PROJECT)])
    factory = FakeMemoryFactory({OTHER_PROJECT: wallet})
    service, _ = build_service(memories=factory)

    results = await service.memory_search("strict typing", project_id=PROJECT)

    assert results == ()
    assert wallet.calls == []


@pytest.mark.asyncio
async def test_memory_search_without_a_configured_source_is_refused() -> None:
    service = RetrievalService(
        FakeSkillRepository([skill()]),
        FakeChunkRepository(),
        FakeEmbedder(),
        clock=lambda: NOW,
    )

    with pytest.raises(MemoryRetrievalUnavailableError):
        await service.memory_search("strict typing")


@pytest.mark.asyncio
async def test_unavailable_memory_search_is_reported_not_hidden() -> None:
    service, _ = build_service(memory_error=SearchUnavailableError("index is not queryable"))

    with pytest.raises(SearchUnavailableError):
        await service.memory_search("strict typing")


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   "])
async def test_an_empty_memory_query_is_rejected(query: str) -> None:
    service, factory = build_service()

    with pytest.raises(ValueError, match="must not be empty"):
        await service.memory_search(query)

    assert factory.requested == []


@pytest.mark.asyncio
async def test_memory_without_an_embedding_model_still_answers_lexically() -> None:
    repository = FakeMemorySearchRepository(None, lexical=[match(3.0)])
    factory = FakeMemoryFactory({None: repository})
    service = RetrievalService(
        FakeSkillRepository([skill()]),
        FakeChunkRepository(),
        None,
        memories=factory,
        clock=lambda: NOW,
    )

    results = await service.memory_search("strict typing")

    assert [call["method"] for call in repository.calls] == ["lexical"]
    assert results[0].methods == ("lexical",)
