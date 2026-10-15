"""Hybrid retrieval service tests independent of MongoDB and the model."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from app.db.errors import SearchUnavailableError
from app.embeddings.types import EmbeddingVector
from app.models.skill import LifecycleStatus, SkillDocument, SkillTrustLevel
from app.models.skill_chunk import SkillChunkDocument
from app.retrieval.context_builder import DEFAULT_TOKEN_BUDGET
from app.services.retrieval_service import RetrievalService, skill_candidate
from app.skills.types import SkillChunkMatch, SkillVersionKey

DIMENSIONS = 4
MODEL = "stub-embedding-model"
NOW = datetime(2026, 9, 1, tzinfo=UTC)


def skill(
    skill_id: str = "fastapi-production",
    version: str = "1.0.0",
    *,
    status: LifecycleStatus = "active",
    category: str = "backend",
    tags: list[str] | None = None,
    trust_level: SkillTrustLevel = "trusted",
) -> SkillDocument:
    """Build one stored skill version."""
    return SkillDocument(
        skill_id=skill_id,
        name="FastAPI Production",
        version=version,
        description="Production practices",
        category=category,
        tags=["python", "fastapi"] if tags is None else tags,
        trust_level=trust_level,
        status=status,
        source_path=f"skills/{skill_id}.md",
        content_hash="a" * 64,
    )


def chunk(
    chunk_id: str = "chunk-0000",
    *,
    skill_id: str = "fastapi-production",
    version: str = "1.0.0",
    content: str = "Payment requests use idempotency keys everywhere in the payment service.",
    tokens: int = 100,
    section: str | None = "Rules",
) -> SkillChunkDocument:
    """Build one stored chunk."""
    return SkillChunkDocument(
        skill_id=skill_id,
        skill_version=version,
        chunk_id=chunk_id,
        content=content,
        section=section,
        chunk_index=0,
        tokens=tokens,
        embedding=[0.5] * DIMENSIONS,
        embedding_model=MODEL,
        embedding_dimensions=DIMENSIONS,
        embedding_generated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class FakeSkillRepository:
    """Serve version keys and documents from an in-memory catalogue."""

    def __init__(self, documents: Sequence[SkillDocument] = ()) -> None:
        self.documents = list(documents)

    async def list_version_keys(
        self,
        *,
        status: LifecycleStatus | None = "active",
        skill_id: str | None = None,
        version: str | None = None,
        category: str | None = None,
        tags: Sequence[str] = (),
        trust_level: SkillTrustLevel | None = None,
        limit: int = 500,
    ) -> tuple[SkillVersionKey, ...]:
        selected = [
            document
            for document in self.documents
            if (status is None or document.status == status)
            and (skill_id is None or document.skill_id == skill_id)
            and (version is None or document.version == version)
            and (category is None or document.category == category)
            and (trust_level is None or document.trust_level == trust_level)
            and all(tag in document.tags for tag in tags)
        ]
        return tuple(SkillVersionKey(item.skill_id, item.version) for item in selected)

    async def get_many(self, keys: Sequence[SkillVersionKey]) -> tuple[SkillDocument, ...]:
        wanted = set(keys)
        return tuple(
            document
            for document in self.documents
            if SkillVersionKey(document.skill_id, document.version) in wanted
        )


class FakeChunkRepository:
    """Return separate vector and lexical matches, recording both calls."""

    def __init__(
        self,
        vector: Sequence[SkillChunkMatch] = (),
        lexical: Sequence[SkillChunkMatch] = (),
        *,
        error: Exception | None = None,
        require_overlap: bool = False,
    ) -> None:
        self.vector = list(vector)
        self.lexical = list(lexical)
        self.error = error
        self.calls: list[dict[str, object]] = []
        self._lexical_started = asyncio.Event()
        self._require_overlap = require_overlap

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 10,
        num_candidates: int,
        skill_versions: Sequence[SkillVersionKey] = (),
    ) -> tuple[SkillChunkMatch, ...]:
        if self._require_overlap:
            # Deadlocks unless the lexical search is already in flight.
            await asyncio.wait_for(self._lexical_started.wait(), timeout=1.0)
        self.calls.append(
            {
                "method": "vector",
                "limit": limit,
                "num_candidates": num_candidates,
                "skill_versions": tuple(skill_versions),
            }
        )
        if self.error is not None:
            raise self.error
        return tuple(self.vector[:limit])

    async def text_search(
        self,
        query: str,
        *,
        limit: int = 10,
        phrase: bool = False,
        skill_versions: Sequence[SkillVersionKey] = (),
    ) -> tuple[SkillChunkMatch, ...]:
        self._lexical_started.set()
        self.calls.append(
            {
                "method": "lexical",
                "query": query,
                "limit": limit,
                "phrase": phrase,
                "skill_versions": tuple(skill_versions),
            }
        )
        if self.error is not None:
            raise self.error
        return tuple(self.lexical[:limit])


class FakeEmbedder:
    """Embed a query without loading a model."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def embed_query(self, text: str) -> EmbeddingVector:
        self.queries.append(text)
        return EmbeddingVector(model=MODEL, dimensions=DIMENSIONS, values=(0.1,) * DIMENSIONS)


def build_service(
    documents: Sequence[SkillDocument] = (),
    vector: Sequence[SkillChunkMatch] = (),
    lexical: Sequence[SkillChunkMatch] = (),
    *,
    embedder: FakeEmbedder | None = None,
    error: Exception | None = None,
    require_overlap: bool = False,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    max_token_budget: int = 16_000,
) -> tuple[RetrievalService, FakeChunkRepository]:
    """Compose the service over in-memory doubles with a fixed clock."""
    skills = FakeSkillRepository(documents)
    chunks = FakeChunkRepository(vector, lexical, error=error, require_overlap=require_overlap)
    service = RetrievalService(
        skills,
        chunks,
        FakeEmbedder() if embedder is None else embedder,
        token_budget=token_budget,
        max_token_budget=max_token_budget,
        clock=lambda: NOW,
    )
    return service, chunks


@pytest.mark.asyncio
async def test_both_searches_run_against_the_same_scope_at_the_same_time() -> None:
    service, chunks = build_service(
        [skill()],
        vector=[SkillChunkMatch(chunk=chunk(), score=0.9)],
        lexical=[SkillChunkMatch(chunk=chunk(), score=7.0)],
        require_overlap=True,
    )

    context = await service.hybrid_search("payment idempotency", limit=5)

    methods = {call["method"] for call in chunks.calls}
    scopes = {call["skill_versions"] for call in chunks.calls}
    assert methods == {"vector", "lexical"}
    assert scopes == {(SkillVersionKey("fastapi-production", "1.0.0"),)}
    assert context.diagnostics.methods == ("vector", "lexical")


@pytest.mark.asyncio
async def test_a_chunk_both_searches_found_is_returned_once() -> None:
    service, _ = build_service(
        [skill()],
        vector=[SkillChunkMatch(chunk=chunk(), score=0.9)],
        lexical=[SkillChunkMatch(chunk=chunk(), score=7.0)],
    )

    context = await service.hybrid_search("payment idempotency")

    assert len(context.items) == 1
    assert context.diagnostics.merged == 1
    assert context.items[0].explanation.methods == ("vector", "lexical")


@pytest.mark.asyncio
async def test_results_from_either_search_are_ranked_together() -> None:
    service, _ = build_service(
        [skill()],
        vector=[SkillChunkMatch(chunk=chunk("chunk-0000"), score=0.9)],
        lexical=[
            SkillChunkMatch(
                chunk=chunk("chunk-0001", content="Keycloak issues OAuth2 tokens for the caller."),
                score=7.0,
            )
        ],
    )

    context = await service.hybrid_search("payment idempotency")

    assert len(context.items) == 2
    assert context.items[0].score >= context.items[1].score
    assert context.diagnostics.vector_matches == 1
    assert context.diagnostics.lexical_matches == 1


@pytest.mark.asyncio
async def test_without_an_embedding_model_the_lexical_half_still_answers() -> None:
    skills = FakeSkillRepository([skill()])
    chunks = FakeChunkRepository(lexical=[SkillChunkMatch(chunk=chunk(), score=7.0)])
    service = RetrievalService(skills, chunks, None, clock=lambda: NOW)

    context = await service.hybrid_search("payment idempotency")

    assert [call["method"] for call in chunks.calls] == ["lexical"]
    assert context.diagnostics.methods == ("lexical",)
    assert context.diagnostics.weights["vector"] == 0.0
    assert len(context.items) == 1


@pytest.mark.asyncio
async def test_metadata_filters_scope_the_searches_before_they_run() -> None:
    service, chunks = build_service(
        [skill(), skill("strict-python", category="python", tags=["python"])],
        vector=[SkillChunkMatch(chunk=chunk(), score=0.9)],
    )

    await service.hybrid_search("typing", category="python")

    assert all(
        call["skill_versions"] == (SkillVersionKey("strict-python", "1.0.0"),)
        for call in chunks.calls
    )


@pytest.mark.asyncio
async def test_a_match_outside_the_requested_scope_never_reaches_the_ranking() -> None:
    service, _ = build_service(
        [skill()],
        vector=[
            SkillChunkMatch(chunk=chunk(), score=0.9),
            SkillChunkMatch(chunk=chunk(skill_id="unrelated-skill"), score=0.99),
        ],
    )

    context = await service.hybrid_search("payment idempotency")

    assert [item.metadata["skill_id"] for item in context.items] == ["fastapi-production"]


@pytest.mark.asyncio
async def test_filters_matching_no_skill_version_search_nothing_at_all() -> None:
    service, chunks = build_service([skill()])

    context = await service.hybrid_search("payment idempotency", category="absent")

    assert context.items == ()
    assert chunks.calls == []
    assert context.diagnostics.methods == ("vector", "lexical")
    assert context.token_budget == DEFAULT_TOKEN_BUDGET


@pytest.mark.asyncio
async def test_superseded_versions_are_excluded_unless_asked_for_by_version() -> None:
    documents = [skill(), skill(version="0.9.0", status="superseded")]
    service, _ = build_service(
        documents,
        vector=[
            SkillChunkMatch(chunk=chunk(), score=0.9),
            SkillChunkMatch(
                chunk=chunk(version="0.9.0", content="Older payment rules."), score=0.8
            ),
        ],
    )

    active = await service.hybrid_search("payment idempotency")
    historical = await service.hybrid_search(
        "payment idempotency",
        skill_id="fastapi-production",
        skill_version="0.9.0",
    )

    assert [item.metadata["skill_version"] for item in active.items] == ["1.0.0"]
    assert [item.metadata["skill_version"] for item in historical.items] == ["0.9.0"]
    assert historical.items[0].metadata["status"] == "superseded"


@pytest.mark.asyncio
async def test_an_importance_floor_above_unrated_knowledge_returns_nothing() -> None:
    service, _ = build_service([skill()], vector=[SkillChunkMatch(chunk=chunk(), score=0.9)])

    context = await service.hybrid_search("payment idempotency", minimum_importance=0.9)

    assert context.items == ()
    assert context.diagnostics.filtered_out == 1


@pytest.mark.asyncio
async def test_skills_are_global_knowledge_that_no_project_scope_hides() -> None:
    service, _ = build_service([skill()], vector=[SkillChunkMatch(chunk=chunk(), score=0.9)])

    scoped = await service.hybrid_search("payment idempotency", project_id="sampleco-payment")
    other = await service.hybrid_search("payment idempotency", project_id="sampleco-wallet")

    assert scoped.project_id == "sampleco-payment"
    assert len(scoped.items) == 1
    assert len(other.items) == 1


@pytest.mark.asyncio
async def test_the_token_budget_bounds_what_is_returned() -> None:
    service, _ = build_service(
        [skill()],
        vector=[
            SkillChunkMatch(chunk=chunk("chunk-0000", tokens=300), score=0.9),
            SkillChunkMatch(
                chunk=chunk("chunk-0001", tokens=300, content="Keycloak issues OAuth2 tokens."),
                score=0.8,
            ),
        ],
    )

    context = await service.hybrid_search("payment idempotency", token_budget=400)

    assert len(context.items) == 1
    assert context.token_count == 300
    assert context.token_budget == 400
    assert context.diagnostics.dropped_for_budget == 1


@pytest.mark.asyncio
async def test_a_request_cannot_raise_its_own_budget_past_the_configured_maximum() -> None:
    service, _ = build_service(
        [skill()],
        vector=[SkillChunkMatch(chunk=chunk(), score=0.9)],
        token_budget=1_000,
        max_token_budget=2_000,
    )

    default = await service.hybrid_search("payment idempotency")
    greedy = await service.hybrid_search("payment idempotency", token_budget=10_000)

    assert default.token_budget == 1_000
    assert greedy.token_budget == 2_000


@pytest.mark.asyncio
async def test_overlapping_chunks_are_returned_once() -> None:
    repeated = "Payment requests use idempotency keys everywhere in the payment service."
    service, _ = build_service(
        [skill()],
        vector=[
            SkillChunkMatch(chunk=chunk("chunk-0000", content=repeated), score=0.9),
            SkillChunkMatch(chunk=chunk("chunk-0001", content=repeated), score=0.8),
        ],
    )

    context = await service.hybrid_search("payment idempotency")

    assert [item.metadata["chunk_id"] for item in context.items] == ["chunk-0000"]
    assert context.diagnostics.duplicates_removed == 1


@pytest.mark.asyncio
async def test_results_are_bounded_by_the_requested_limit() -> None:
    service, _ = build_service(
        [skill()],
        vector=[
            SkillChunkMatch(
                chunk=chunk(f"chunk-{index:04d}", content=f"Distinct chunk number {index}."),
                score=0.9 - index / 100,
            )
            for index in range(5)
        ],
    )

    context = await service.hybrid_search("payment idempotency", limit=2)

    assert len(context.items) == 2


@pytest.mark.asyncio
async def test_equivalent_requests_produce_identical_results() -> None:
    matches = [
        SkillChunkMatch(chunk=chunk("chunk-0000"), score=0.9),
        SkillChunkMatch(
            chunk=chunk("chunk-0001", content="Keycloak issues OAuth2 tokens."), score=0.7
        ),
    ]
    service, _ = build_service([skill()], vector=matches, lexical=matches[::-1])

    first = await service.hybrid_search("payment idempotency")
    second = await service.hybrid_search("payment idempotency")

    assert [item.score for item in first.items] == [item.score for item in second.items]
    assert [item.title for item in first.items] == [item.title for item in second.items]


@pytest.mark.asyncio
async def test_unavailable_search_infrastructure_is_reported_not_hidden() -> None:
    service, _ = build_service(
        [skill()],
        error=SearchUnavailableError("index is not queryable"),
    )

    with pytest.raises(SearchUnavailableError):
        await service.hybrid_search("payment idempotency")


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
async def test_an_empty_query_is_rejected(query: str) -> None:
    service, chunks = build_service([skill()])

    with pytest.raises(ValueError, match="must not be empty"):
        await service.hybrid_search(query)

    assert chunks.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 51, True])
async def test_a_limit_outside_the_supported_range_is_rejected(limit: int) -> None:
    service, _ = build_service([skill()])

    with pytest.raises(ValueError, match="limit"):
        await service.hybrid_search("payment idempotency", limit=limit)


@pytest.mark.asyncio
async def test_a_version_without_its_skill_is_rejected() -> None:
    service, _ = build_service([skill()])

    with pytest.raises(ValueError, match="skill_version requires skill_id"):
        await service.hybrid_search("payment idempotency", skill_version="1.0.0")


def test_a_misconfigured_budget_fails_when_the_service_is_built() -> None:
    with pytest.raises(ValueError, match="budget"):
        RetrievalService(
            FakeSkillRepository(),
            FakeChunkRepository(),
            token_budget=9_000,
            max_token_budget=4_000,
        )


def test_a_skill_chunk_candidate_preserves_its_source() -> None:
    document = skill()
    candidate = skill_candidate(chunk(), document)

    assert candidate.key == "skill_chunk:fastapi-production:1.0.0:chunk-0000"
    assert candidate.source == "active_skill"
    assert candidate.title == "FastAPI Production: Rules"
    assert candidate.project_id is None
    assert candidate.is_active is True
    assert candidate.category == "backend"
    assert candidate.trust_level == "trusted"
    assert candidate.metadata["source_path"] == "skills/fastapi-production.md"
    assert candidate.metadata["chunk_index"] == 0


def test_a_chunk_without_a_section_is_titled_by_its_skill() -> None:
    candidate = skill_candidate(chunk(section=None), skill())

    assert candidate.title == "FastAPI Production"


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
