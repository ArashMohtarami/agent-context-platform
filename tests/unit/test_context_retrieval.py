"""Project context inside hybrid retrieval, and context-only search."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from app.db.context_indexes import ContextQueryFilters
from app.db.errors import SearchUnavailableError
from app.projects.errors import ContextRetrievalUnavailableError
from app.projects.types import ProjectContextMatch
from app.services.retrieval_service import RetrievalService, context_candidate
from app.skills.types import SkillChunkMatch
from tests.project_doubles import FakeEmbedder, context
from tests.unit.test_hybrid_search_service import (
    FakeChunkRepository,
    FakeSkillRepository,
    chunk,
    skill,
)

PROJECT = "sampleco-payment"
OTHER_PROJECT = "sampleco-wallet"
NOW = datetime(2026, 9, 1, tzinfo=UTC)


class FakeContextRepository:
    """Return prepared context matches and record how the query was scoped."""

    def __init__(
        self,
        project_id: str,
        vector: Sequence[ProjectContextMatch] = (),
        lexical: Sequence[ProjectContextMatch] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self.project_id = project_id
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
        filters: ContextQueryFilters,
    ) -> tuple[ProjectContextMatch, ...]:
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
        filters: ContextQueryFilters,
    ) -> tuple[ProjectContextMatch, ...]:
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


class FakeContextFactory:
    """Build one context repository per project, remembering every request."""

    def __init__(
        self,
        by_project: dict[str, FakeContextRepository] | None = None,
    ) -> None:
        self.by_project = by_project or {}
        self.requested: list[str] = []

    def __call__(self, project_id: str) -> FakeContextRepository:
        self.requested.append(project_id)
        if project_id not in self.by_project:
            self.by_project[project_id] = FakeContextRepository(project_id)
        return self.by_project[project_id]


def build_service(
    *,
    context_vector: Sequence[ProjectContextMatch] = (),
    context_lexical: Sequence[ProjectContextMatch] = (),
    skill_vector: Sequence[SkillChunkMatch] = (),
    skill_lexical: Sequence[SkillChunkMatch] = (),
    context_error: Exception | None = None,
    embedder: FakeEmbedder | None = None,
    contexts: FakeContextFactory | None = None,
) -> tuple[RetrievalService, FakeContextFactory]:
    """Compose retrieval over in-memory skill and context doubles."""
    factory = contexts or FakeContextFactory(
        {
            PROJECT: FakeContextRepository(
                PROJECT,
                context_vector,
                context_lexical,
                error=context_error,
            )
        }
    )
    service = RetrievalService(
        FakeSkillRepository([skill()]),
        FakeChunkRepository(skill_vector, skill_lexical),
        FakeEmbedder() if embedder is None else embedder,
        contexts=factory,
        clock=lambda: NOW,
    )
    return service, factory


def match(score: float, **overrides: object) -> ProjectContextMatch:
    """Build one scored context match."""
    return ProjectContextMatch(
        context=context(**overrides),  # type: ignore[arg-type]
        score=score,
    )


def test_a_context_candidate_carries_its_project_importance_and_lifecycle() -> None:
    document = context(PROJECT, importance=0.9, tags=("payments",), status="archived")

    candidate = context_candidate(document)

    assert candidate.source == "project_context"
    assert candidate.project_id == PROJECT
    assert candidate.importance == 0.9
    assert candidate.is_active is False
    assert candidate.tags == ("payments",)
    assert candidate.title == document.title
    assert candidate.tokens > 0
    assert candidate.metadata["context_id"] == str(document.id)
    assert candidate.metadata["type"] == document.type
    assert candidate.key.startswith(f"project_context:{PROJECT}:")


@pytest.mark.asyncio
async def test_hybrid_retrieval_includes_the_named_project_s_context() -> None:
    service, factory = build_service(
        context_vector=[match(0.9, project_id=PROJECT)],
        skill_vector=[SkillChunkMatch(chunk=chunk(), score=0.8)],
    )

    result = await service.hybrid_search("payment idempotency", project_id=PROJECT)

    sources = [item.source for item in result.items]
    assert "project_context" in sources
    assert "active_skill" in sources
    assert factory.requested == [PROJECT]


@pytest.mark.asyncio
async def test_project_context_outranks_a_skill_at_equal_relevance() -> None:
    service, _ = build_service(
        context_vector=[match(0.8, project_id=PROJECT, importance=0.5)],
        skill_vector=[SkillChunkMatch(chunk=chunk(), score=0.8)],
    )

    result = await service.hybrid_search("payment idempotency", project_id=PROJECT)

    # Identical similarity, importance, and recency: only source priority
    # separates 1.00 from 0.95.
    assert result.items[0].source == "project_context"
    assert result.items[0].explanation.source_priority == 1.00
    assert result.items[1].explanation.source_priority == 0.95


@pytest.mark.asyncio
async def test_a_request_naming_no_project_reaches_no_context_at_all() -> None:
    service, factory = build_service(
        context_vector=[match(0.99, project_id=PROJECT)],
        skill_vector=[SkillChunkMatch(chunk=chunk(), score=0.5)],
    )

    result = await service.hybrid_search("payment idempotency")

    assert [item.source for item in result.items] == ["active_skill"]
    assert factory.requested == []


@pytest.mark.asyncio
async def test_hybrid_retrieval_asks_only_the_requested_project_for_context() -> None:
    payment = FakeContextRepository(PROJECT, [match(0.9, project_id=PROJECT)])
    wallet = FakeContextRepository(OTHER_PROJECT, [match(0.99, project_id=OTHER_PROJECT)])
    factory = FakeContextFactory({PROJECT: payment, OTHER_PROJECT: wallet})
    service, _ = build_service(contexts=factory)

    result = await service.hybrid_search("payment idempotency", project_id=PROJECT)

    assert factory.requested == [PROJECT]
    assert wallet.calls == []
    assert all(item.metadata["project_id"] == PROJECT for item in result.items)


@pytest.mark.asyncio
async def test_context_from_another_project_is_dropped_even_if_a_search_returns_it() -> None:
    # The repository is scoped by construction; this proves the service filters
    # again rather than trusting what a search hands back.
    leaking = FakeContextRepository(PROJECT, [match(0.99, project_id=OTHER_PROJECT)])
    factory = FakeContextFactory({PROJECT: leaking})
    service, _ = build_service(contexts=factory)

    result = await service.hybrid_search("payment idempotency", project_id=PROJECT)

    assert [item.source for item in result.items] == []
    assert result.diagnostics.filtered_out == 1


@pytest.mark.asyncio
async def test_the_context_scope_carries_the_requested_filters() -> None:
    payment = FakeContextRepository(PROJECT)
    factory = FakeContextFactory({PROJECT: payment})
    service, _ = build_service(contexts=factory)

    await service.hybrid_search(
        "payment idempotency",
        project_id=PROJECT,
        tags=["payments"],
        minimum_importance=0.4,
        active_only=True,
    )

    scope = payment.calls[0]["filters"]
    assert isinstance(scope, ContextQueryFilters)
    assert scope.project_id == PROJECT
    assert scope.tags == ("payments",)
    assert scope.minimum_importance == 0.4
    assert scope.status == "active"


@pytest.mark.asyncio
async def test_archived_context_is_excluded_unless_it_is_asked_for() -> None:
    archived = match(0.9, project_id=PROJECT, status="archived")
    service, _ = build_service(context_vector=[archived])

    default = await service.hybrid_search("payment idempotency", project_id=PROJECT)
    including = await service.hybrid_search(
        "payment idempotency",
        project_id=PROJECT,
        active_only=False,
    )

    assert default.items == ()
    assert [item.source for item in including.items] == ["project_context"]


@pytest.mark.asyncio
async def test_lexical_scores_are_scaled_within_each_source() -> None:
    # Context relevance of 4.0 and skill relevance of 40.0 come from different
    # indexes: each is the best of its own search, so both normalize to 1.0.
    service, _ = build_service(
        context_lexical=[match(4.0, project_id=PROJECT)],
        skill_lexical=[SkillChunkMatch(chunk=chunk(), score=40.0)],
    )

    result = await service.hybrid_search("payment idempotency", project_id=PROJECT)

    assert {item.explanation.lexical for item in result.items} == {1.0}


@pytest.mark.asyncio
async def test_context_search_ranks_one_project_s_context() -> None:
    first = match(0.9, project_id=PROJECT, title="Payment idempotency strategy")
    second = match(
        0.4,
        project_id=PROJECT,
        title="Refund window",
        content="Refunds are accepted for 30 days after capture.",
    )
    service, _ = build_service(context_vector=[first, second])

    results = await service.context_search("idempotency", project_id=PROJECT)

    assert [result.context.title for result in results] == [
        "Payment idempotency strategy",
        "Refund window",
    ]
    assert results[0].score >= results[1].score
    assert results[0].methods == ("vector",)


@pytest.mark.asyncio
async def test_context_search_passes_every_filter_to_the_repository() -> None:
    payment = FakeContextRepository(PROJECT)
    factory = FakeContextFactory({PROJECT: payment})
    service, _ = build_service(contexts=factory)

    await service.context_search(
        "idempotency",
        project_id=PROJECT,
        phrase=True,
        context_types=["architecture_decision"],
        tags=["payments"],
        status="archived",
        minimum_importance=0.3,
        limit=7,
    )

    lexical = next(call for call in payment.calls if call["method"] == "lexical")
    scope = lexical["filters"]
    assert isinstance(scope, ContextQueryFilters)
    assert scope.types == ("architecture_decision",)
    assert scope.tags == ("payments",)
    assert scope.status == "archived"
    assert scope.minimum_importance == 0.3
    assert lexical["phrase"] is True
    assert lexical["limit"] == 7


@pytest.mark.asyncio
async def test_context_search_never_reaches_another_project() -> None:
    wallet = FakeContextRepository(OTHER_PROJECT, [match(0.99, project_id=OTHER_PROJECT)])
    factory = FakeContextFactory({OTHER_PROJECT: wallet})
    service, _ = build_service(contexts=factory)

    results = await service.context_search("idempotency", project_id=PROJECT)

    assert results == ()
    assert wallet.calls == []


@pytest.mark.asyncio
async def test_context_search_without_a_configured_source_is_refused() -> None:
    service = RetrievalService(
        FakeSkillRepository([skill()]),
        FakeChunkRepository(),
        FakeEmbedder(),
        clock=lambda: NOW,
    )

    with pytest.raises(ContextRetrievalUnavailableError):
        await service.context_search("idempotency", project_id=PROJECT)


@pytest.mark.asyncio
async def test_unavailable_context_search_is_reported_not_hidden() -> None:
    service, _ = build_service(context_error=SearchUnavailableError("index is not queryable"))

    with pytest.raises(SearchUnavailableError):
        await service.context_search("idempotency", project_id=PROJECT)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   "])
async def test_an_empty_context_query_is_rejected(query: str) -> None:
    service, factory = build_service()

    with pytest.raises(ValueError, match="must not be empty"):
        await service.context_search(query, project_id=PROJECT)

    assert factory.requested == []


@pytest.mark.asyncio
async def test_context_without_an_embedding_model_still_answers_lexically() -> None:
    payment = FakeContextRepository(PROJECT, lexical=[match(3.0, project_id=PROJECT)])
    factory = FakeContextFactory({PROJECT: payment})
    service = RetrievalService(
        FakeSkillRepository([skill()]),
        FakeChunkRepository(),
        None,
        contexts=factory,
        clock=lambda: NOW,
    )

    results = await service.context_search("idempotency", project_id=PROJECT)

    assert [call["method"] for call in payment.calls] == ["lexical"]
    assert len(results) == 1
    assert results[0].methods == ("lexical",)


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing domain behavior uses an explicit test organization."""
    yield
