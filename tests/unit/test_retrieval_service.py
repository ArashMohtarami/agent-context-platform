"""Vector retrieval service tests independent of MongoDB and the model."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from app.db.errors import SearchUnavailableError
from app.embeddings.errors import EmbeddingUnavailableError
from app.embeddings.types import EmbeddingVector
from app.models.skill import LifecycleStatus, SkillDocument, SkillTrustLevel
from app.models.skill_chunk import SkillChunkDocument
from app.services.retrieval_service import MAX_PREFILTER_VERSIONS, RetrievalService
from app.skills.types import SkillChunkMatch, SkillVersionKey

DIMENSIONS = 4
MODEL = "stub-embedding-model"


def skill(
    skill_id: str = "fastapi-production",
    version: str = "1.0.0",
    *,
    status: LifecycleStatus = "active",
    category: str = "backend",
    trust_level: SkillTrustLevel = "trusted",
) -> SkillDocument:
    """Build one stored skill version."""
    return SkillDocument(
        skill_id=skill_id,
        name="FastAPI Production",
        version=version,
        description="Production practices",
        category=category,
        tags=["python", "fastapi"],
        trust_level=trust_level,
        status=status,
        source_path=f"skills/{skill_id}.md",
        content_hash="a" * 64,
    )


def chunk(skill_id: str = "fastapi-production", version: str = "1.0.0") -> SkillChunkDocument:
    """Build one stored chunk carrying a vector."""
    return SkillChunkDocument(
        skill_id=skill_id,
        skill_version=version,
        chunk_id="chunk-0000",
        content="Payment requests use idempotency keys.",
        section="Rules",
        chunk_index=0,
        tokens=6,
        embedding=[0.5] * DIMENSIONS,
        embedding_model=MODEL,
        embedding_dimensions=DIMENSIONS,
        embedding_generated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class FakeSkillRepository:
    """Serve bounded version keys and documents from an in-memory catalogue."""

    def __init__(self, documents: Sequence[SkillDocument] = ()) -> None:
        self.documents = list(documents)
        self.key_calls: list[dict[str, object]] = []

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
        self.key_calls.append(
            {
                "status": status,
                "skill_id": skill_id,
                "version": version,
                "category": category,
                "tags": tuple(tags),
                "trust_level": trust_level,
                "limit": limit,
            }
        )
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
    """Return prepared matches and record how the query was scoped."""

    def __init__(
        self,
        matches: Sequence[SkillChunkMatch] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self.matches = list(matches)
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 10,
        num_candidates: int,
        skill_versions: Sequence[SkillVersionKey] = (),
    ) -> tuple[SkillChunkMatch, ...]:
        self.calls.append(
            {
                "embedding": tuple(embedding),
                "limit": limit,
                "num_candidates": num_candidates,
                "skill_versions": tuple(skill_versions),
            }
        )
        if self.error is not None:
            raise self.error
        return tuple(self.matches[:limit])

    async def text_search(
        self,
        query: str,
        *,
        limit: int = 10,
        phrase: bool = False,
        skill_versions: Sequence[SkillVersionKey] = (),
    ) -> tuple[SkillChunkMatch, ...]:
        self.calls.append(
            {
                "query": query,
                "limit": limit,
                "phrase": phrase,
                "skill_versions": tuple(skill_versions),
            }
        )
        if self.error is not None:
            raise self.error
        return tuple(self.matches[:limit])


class FakeEmbedder:
    """Embed a query without loading a model."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.queries: list[str] = []

    async def embed_query(self, text: str) -> EmbeddingVector:
        self.queries.append(text)
        if self.error is not None:
            raise self.error
        return EmbeddingVector(model=MODEL, dimensions=DIMENSIONS, values=(0.1,) * DIMENSIONS)


def build_service(
    documents: Sequence[SkillDocument] = (),
    matches: Sequence[SkillChunkMatch] = (),
    *,
    chunk_error: Exception | None = None,
    embed_error: Exception | None = None,
    candidate_multiplier: int = 10,
    max_candidates: int = 1_000,
) -> tuple[RetrievalService, FakeSkillRepository, FakeChunkRepository, FakeEmbedder]:
    """Compose the service over in-memory doubles."""
    skills = FakeSkillRepository(documents)
    chunks = FakeChunkRepository(matches, error=chunk_error)
    embedder = FakeEmbedder(embed_error)
    service = RetrievalService(
        skills,
        chunks,
        embedder,
        candidate_multiplier=candidate_multiplier,
        max_candidates=max_candidates,
    )
    return service, skills, chunks, embedder


@pytest.mark.asyncio
async def test_results_carry_scores_and_source_metadata() -> None:
    service, _, _, embedder = build_service(
        [skill()],
        [SkillChunkMatch(chunk=chunk(), score=0.88)],
    )

    results = await service.vector_search("  payment   idempotency  ")

    assert embedder.queries == ["payment idempotency"]
    assert len(results) == 1
    assert results[0].score == 0.88
    assert results[0].skill.skill_id == "fastapi-production"
    assert results[0].skill.category == "backend"
    assert results[0].skill.tags == ["python", "fastapi"]
    assert results[0].chunk.content.startswith("Payment requests")


@pytest.mark.asyncio
async def test_active_versions_are_searched_by_default() -> None:
    service, skills, chunks, _ = build_service(
        [skill(), skill("strict-python", "2.0.0", status="superseded")],
        [SkillChunkMatch(chunk=chunk(), score=0.5)],
    )

    await service.vector_search("idempotency")

    assert skills.key_calls[0]["status"] == "active"
    assert chunks.calls[0]["skill_versions"] == (SkillVersionKey("fastapi-production", "1.0.0"),)


@pytest.mark.asyncio
async def test_superseded_versions_are_searchable_on_request() -> None:
    service, skills, chunks, _ = build_service(
        [skill(status="superseded")],
        [SkillChunkMatch(chunk=chunk(), score=0.5)],
    )

    results = await service.vector_search("idempotency", active_only=False)

    assert skills.key_calls[0]["status"] is None
    assert chunks.calls[0]["skill_versions"] == (SkillVersionKey("fastapi-production", "1.0.0"),)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_metadata_filters_reach_the_metadata_query() -> None:
    service, skills, _, _ = build_service([skill()], [])

    await service.vector_search(
        "idempotency",
        category="backend",
        tags=["python"],
        trust_level="trusted",
    )

    assert skills.key_calls[0]["category"] == "backend"
    assert skills.key_calls[0]["tags"] == ("python",)
    assert skills.key_calls[0]["trust_level"] == "trusted"


@pytest.mark.asyncio
async def test_an_explicit_version_targets_that_exact_version() -> None:
    service, skills, chunks, _ = build_service(
        [skill(status="superseded"), skill(version="2.0.0")],
        [SkillChunkMatch(chunk=chunk(), score=0.4)],
    )

    results = await service.vector_search(
        "idempotency",
        skill_id="fastapi-production",
        skill_version="1.0.0",
    )

    assert skills.key_calls[0]["status"] is None
    assert skills.key_calls[0]["version"] == "1.0.0"
    assert chunks.calls[0]["skill_versions"] == (SkillVersionKey("fastapi-production", "1.0.0"),)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_a_version_without_its_skill_is_rejected() -> None:
    service, _, chunks, _ = build_service([skill()], [])

    with pytest.raises(ValueError, match="skill_version requires skill_id"):
        await service.vector_search("idempotency", skill_version="1.0.0")

    assert chunks.calls == []


@pytest.mark.asyncio
async def test_filters_that_match_nothing_skip_the_vector_query() -> None:
    service, _, chunks, embedder = build_service([skill()], [])

    results = await service.vector_search("idempotency", category="frontend")

    assert results == ()
    assert chunks.calls == []
    assert embedder.queries == []


@pytest.mark.asyncio
async def test_results_outside_the_resolved_versions_are_discarded() -> None:
    service, _, _, _ = build_service(
        [skill()],
        [
            SkillChunkMatch(chunk=chunk(), score=0.9),
            SkillChunkMatch(chunk=chunk("other-skill", "3.0.0"), score=0.8),
        ],
    )

    results = await service.vector_search("idempotency")

    assert [result.skill.skill_id for result in results] == ["fastapi-production"]


@pytest.mark.asyncio
async def test_a_result_without_stored_metadata_is_dropped() -> None:
    skills = FakeSkillRepository([skill()])
    chunks = FakeChunkRepository([SkillChunkMatch(chunk=chunk(), score=0.9)])
    service = RetrievalService(skills, chunks, FakeEmbedder())
    skills.documents.clear()

    assert await service.vector_search("idempotency") == ()


@pytest.mark.asyncio
async def test_top_k_is_bounded_by_the_requested_limit() -> None:
    matches = [
        SkillChunkMatch(chunk=chunk(), score=0.9),
        SkillChunkMatch(chunk=chunk(), score=0.8),
        SkillChunkMatch(chunk=chunk(), score=0.7),
    ]
    service, _, chunks, _ = build_service([skill()], matches)

    results = await service.vector_search("idempotency", limit=2)

    assert chunks.calls[0]["limit"] == 2
    assert len(results) == 2


@pytest.mark.asyncio
async def test_a_broad_catalogue_falls_back_to_verifying_results() -> None:
    documents = [skill(f"skill-{index:03d}") for index in range(MAX_PREFILTER_VERSIONS + 1)]
    service, _, chunks, _ = build_service(
        documents,
        [SkillChunkMatch(chunk=chunk("skill-000"), score=0.9)],
    )

    results = await service.vector_search("idempotency", limit=5)

    assert chunks.calls[0]["skill_versions"] == ()
    assert chunks.calls[0]["limit"] == 20
    assert len(results) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "multiplier", "maximum", "expected"),
    [
        (10, 10, 1_000, 100),
        (10, 1, 1_000, 10),
        (5, 10, 20, 20),
        (50, 100, 5_000, 5_000),
        (10, 10, 5, 10),
    ],
)
async def test_the_candidate_pool_is_sized_and_clamped(
    limit: int,
    multiplier: int,
    maximum: int,
    expected: int,
) -> None:
    service, _, chunks, _ = build_service(
        [skill()],
        [],
        candidate_multiplier=multiplier,
        max_candidates=maximum,
    )

    await service.vector_search("idempotency", limit=limit)

    assert chunks.calls[0]["num_candidates"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
async def test_an_empty_query_is_rejected(query: str) -> None:
    service, _, chunks, _ = build_service([skill()], [])

    with pytest.raises(ValueError, match="must not be empty"):
        await service.vector_search(query)

    assert chunks.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 51, True])
async def test_limits_outside_the_supported_range_are_rejected(limit: int) -> None:
    service, _, chunks, _ = build_service([skill()], [])

    with pytest.raises(ValueError, match="limit"):
        await service.vector_search("idempotency", limit=limit)

    assert chunks.calls == []


@pytest.mark.asyncio
async def test_unavailable_search_infrastructure_is_propagated() -> None:
    service, _, _, _ = build_service(
        [skill()],
        [],
        chunk_error=SearchUnavailableError("index is not queryable"),
    )

    with pytest.raises(SearchUnavailableError):
        await service.vector_search("idempotency")


@pytest.mark.asyncio
async def test_an_unavailable_model_is_propagated() -> None:
    service, _, _, _ = build_service(
        [skill()],
        [],
        embed_error=EmbeddingUnavailableError("model is not loaded"),
    )

    with pytest.raises(EmbeddingUnavailableError):
        await service.vector_search("idempotency")


def test_unusable_candidate_configuration_is_rejected() -> None:
    skills = FakeSkillRepository()
    chunks = FakeChunkRepository()

    with pytest.raises(ValueError, match="candidate_multiplier"):
        RetrievalService(skills, chunks, FakeEmbedder(), candidate_multiplier=0)
    with pytest.raises(ValueError, match="max_candidates"):
        RetrievalService(skills, chunks, FakeEmbedder(), max_candidates=0)


@pytest.mark.asyncio
async def test_skill_retrieval_exposes_no_project_scope() -> None:
    """Skills are global, so no project identifier can reach this path."""
    service, _, _, _ = build_service(
        [skill()],
        [SkillChunkMatch(chunk=chunk(), score=0.9)],
    )

    results = await service.vector_search("idempotency")

    assert "project_id" not in results[0].chunk.model_dump()
    assert "project_id" not in results[0].skill.model_dump()
    with pytest.raises(TypeError):
        await service.vector_search("idempotency", project_id="other")  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_lexical_results_use_the_shared_retrieval_model() -> None:
    service, _, chunks, embedder = build_service(
        [skill()],
        [SkillChunkMatch(chunk=chunk(), score=7.25)],
    )

    results = await service.lexical_search("  Keycloak   OAuth2  ")

    assert chunks.calls[0]["query"] == "Keycloak OAuth2"
    assert embedder.queries == [], "lexical search must not embed the query"
    assert len(results) == 1
    assert results[0].method == "lexical"
    assert results[0].score == 7.25
    assert results[0].skill.skill_id == "fastapi-production"
    assert results[0].chunk.content.startswith("Payment requests")


@pytest.mark.asyncio
async def test_vector_results_are_tagged_with_their_method() -> None:
    service, _, _, _ = build_service([skill()], [SkillChunkMatch(chunk=chunk(), score=0.9)])

    results = await service.vector_search("payment idempotency")

    assert results[0].method == "vector"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "wallet:read",
        "INSUFFICIENT_SCOPE",
        "payment-service",
        "/api/v1/payments",
        "Keycloak",
        "OAuth2",
        "PaymentIdempotencyService",
        "MONGODB_REPLICA_SET",
    ],
)
async def test_identifiers_reach_the_index_unchanged(query: str) -> None:
    service, _, chunks, _ = build_service([skill()], [])

    await service.lexical_search(query)

    assert chunks.calls[0]["query"] == query


@pytest.mark.asyncio
async def test_phrase_search_is_passed_through() -> None:
    service, _, chunks, _ = build_service([skill()], [])

    await service.lexical_search("payment idempotency", phrase=True)

    assert chunks.calls[0]["phrase"] is True


@pytest.mark.asyncio
async def test_lexical_metadata_filters_reach_the_metadata_query() -> None:
    service, skills, chunks, _ = build_service([skill()], [])

    await service.lexical_search(
        "Keycloak",
        category="backend",
        tags=["python"],
        trust_level="trusted",
    )

    assert skills.key_calls[0]["category"] == "backend"
    assert skills.key_calls[0]["tags"] == ("python",)
    assert skills.key_calls[0]["trust_level"] == "trusted"
    assert chunks.calls[0]["skill_versions"] == (SkillVersionKey("fastapi-production", "1.0.0"),)


@pytest.mark.asyncio
async def test_lexical_filters_that_match_nothing_skip_the_query() -> None:
    service, _, chunks, _ = build_service([skill()], [])

    assert await service.lexical_search("Keycloak", category="frontend") == ()
    assert chunks.calls == []


@pytest.mark.asyncio
async def test_lexical_results_outside_the_resolved_versions_are_discarded() -> None:
    service, _, _, _ = build_service(
        [skill()],
        [
            SkillChunkMatch(chunk=chunk(), score=9.0),
            SkillChunkMatch(chunk=chunk("other-skill", "3.0.0"), score=8.0),
        ],
    )

    results = await service.lexical_search("Keycloak")

    assert [result.skill.skill_id for result in results] == ["fastapi-production"]


@pytest.mark.asyncio
async def test_a_lexical_query_matching_nothing_returns_no_results() -> None:
    service, _, _, _ = build_service([skill()], [])

    assert await service.lexical_search("Kubernetes") == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
async def test_malformed_lexical_queries_are_rejected(query: str) -> None:
    service, _, chunks, _ = build_service([skill()], [])

    with pytest.raises(ValueError, match="must not be empty"):
        await service.lexical_search(query)

    assert chunks.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 51, True])
async def test_lexical_limits_outside_the_supported_range_are_rejected(limit: int) -> None:
    service, _, chunks, _ = build_service([skill()], [])

    with pytest.raises(ValueError, match="limit"):
        await service.lexical_search("Keycloak", limit=limit)

    assert chunks.calls == []


@pytest.mark.asyncio
async def test_a_lexical_version_without_its_skill_is_rejected() -> None:
    service, _, chunks, _ = build_service([skill()], [])

    with pytest.raises(ValueError, match="skill_version requires skill_id"):
        await service.lexical_search("Keycloak", skill_version="1.0.0")

    assert chunks.calls == []


@pytest.mark.asyncio
async def test_unavailable_lexical_infrastructure_is_propagated() -> None:
    service, _, _, _ = build_service(
        [skill()],
        [],
        chunk_error=SearchUnavailableError("index is not queryable"),
    )

    with pytest.raises(SearchUnavailableError):
        await service.lexical_search("Keycloak")


@pytest.mark.asyncio
async def test_lexical_search_works_without_an_embedding_model() -> None:
    skills = FakeSkillRepository([skill()])
    chunks = FakeChunkRepository([SkillChunkMatch(chunk=chunk(), score=5.0)])
    service = RetrievalService(skills, chunks)

    results = await service.lexical_search("Keycloak")

    assert len(results) == 1
    with pytest.raises(EmbeddingUnavailableError, match="requires an embedding model"):
        await service.vector_search("Keycloak")


@pytest.mark.asyncio
async def test_gather_sources_can_request_lexical_only_without_embedding_or_vector_query() -> None:
    service, _, chunks, embedder = build_service(
        [skill()], [SkillChunkMatch(chunk=chunk(), score=4.0)]
    )

    matches = await service.gather_sources(
        "Keycloak", sources=("active_skill",), methods=("lexical",)
    )

    assert matches.vector == ()
    assert len(matches.lexical) == 1
    assert embedder.queries == []
    assert len(chunks.calls) == 1
    assert chunks.calls[0]["query"] == "Keycloak"
