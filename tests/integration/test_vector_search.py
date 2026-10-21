"""Live MongoDB Vector Search checks through mongod and self-managed mongot."""

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from bson import ObjectId
from v6_legacy_integration import initialize_database

from app.core.config import Settings, get_settings
from app.db.client import MongoDBClient
from app.db.vector_indexes import (
    SKILL_CHUNK_VECTOR_INDEX,
    describe_vector_index,
    ensure_vector_indexes,
)
from app.embeddings.types import EmbeddingVector
from app.models.skill import LifecycleStatus, SkillDocument, SkillTrustLevel
from app.models.skill_chunk import SkillChunkDocument
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.services.retrieval_service import RetrievalService
from app.skills.types import SkillChunkSearchResult, SkillVersionKey

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

INDEX_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 0.1


class UnitVectorEmbedder:
    """Embed queries as fixed basis vectors so similarity is known in advance."""

    def __init__(self, settings: Settings, axis: int) -> None:
        self._settings = settings
        self._axis = axis

    async def embed_query(self, text: str) -> EmbeddingVector:
        _ = text
        return EmbeddingVector(
            model=self._settings.embedding_model,
            dimensions=self._settings.embedding_dimensions,
            values=basis_vector(self._settings.embedding_dimensions, self._axis),
        )


def basis_vector(dimensions: int, axis: int) -> tuple[float, ...]:
    """Return a unit vector pointing along one axis."""
    return tuple(1.0 if index == axis else 0.0 for index in range(dimensions))


def skill_document(
    skill_id: str,
    *,
    version: str = "1.0.0",
    category: str,
    tags: list[str],
    trust_level: SkillTrustLevel,
    status: LifecycleStatus = "active",
) -> SkillDocument:
    """Build one skill version owned by this test run."""
    return SkillDocument(
        skill_id=skill_id,
        name=f"Phase 6 {skill_id}",
        version=version,
        description="Vector search integration coverage.",
        category=category,
        tags=tags,
        trust_level=trust_level,
        status=status,
        source_path=f"skills/{skill_id}.md",
        content_hash="b" * 64,
    )


def chunk_document(
    settings: Settings,
    skill: SkillDocument,
    *,
    axis: int,
    content: str,
) -> SkillChunkDocument:
    """Build one chunk whose vector points along a known axis."""
    return SkillChunkDocument(
        skill_id=skill.skill_id,
        skill_version=skill.version,
        chunk_id="chunk-0000",
        content=content,
        section="Rules",
        chunk_index=0,
        tokens=8,
        embedding=list(basis_vector(settings.embedding_dimensions, axis)),
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
        embedding_generated_at=datetime.now(UTC),
    )


@asynccontextmanager
async def vector_database(skill_ids: Sequence[str]) -> AsyncIterator[MongoDBClient]:
    """Connect, build the vector index, and remove only this test's records."""
    settings = get_settings()
    mongodb = MongoDBClient(settings)
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        await ensure_vector_indexes(
            mongodb.database,
            dimensions=settings.embedding_dimensions,
        )
        await wait_for_queryable_index(mongodb)
        yield mongodb
    finally:
        await mongodb.database["skill_chunks"].delete_many({"skill_id": {"$in": list(skill_ids)}})
        await mongodb.database["skills"].delete_many({"skill_id": {"$in": list(skill_ids)}})
        await mongodb.close()


async def wait_for_queryable_index(mongodb: MongoDBClient) -> None:
    """Wait until mongot reports the vector index as queryable."""
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    while True:
        status = await describe_vector_index(mongodb.database)
        if status.ready:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"{SKILL_CHUNK_VECTOR_INDEX} did not become queryable")
        await asyncio.sleep(POLL_SECONDS)


async def wait_for_indexed_chunks(
    service: RetrievalService,
    *,
    expected: int,
    skill_id: str | None = None,
    skill_version: str | None = None,
    category: str | None = None,
) -> tuple[SkillChunkSearchResult, ...]:
    """Wait until mongot has indexed the chunks this test just inserted."""
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    while True:
        results = await service.vector_search(
            "payment idempotency",
            skill_id=skill_id,
            skill_version=skill_version,
            category=category,
        )
        if len(results) >= expected:
            return results
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"expected {expected} indexed chunks, found {len(results)}")
        await asyncio.sleep(POLL_SECONDS)


@pytest.mark.asyncio
async def test_vector_search_ranks_filters_and_scores_real_chunks() -> None:
    settings = get_settings()
    run_id = str(ObjectId())
    target_id = f"phase6-target-{run_id}"
    other_id = f"phase6-other-{run_id}"
    history_id = f"phase6-history-{run_id}"

    target = skill_document(
        target_id,
        category="phase6-backend",
        tags=["phase6", "payments"],
        trust_level="trusted",
    )
    other = skill_document(
        other_id,
        category="phase6-frontend",
        tags=["phase6", "ui"],
        trust_level="external",
    )
    superseded = skill_document(
        history_id,
        version="0.9.0",
        category="phase6-backend",
        tags=["phase6"],
        trust_level="trusted",
        status="superseded",
    )

    async with vector_database([target_id, other_id, history_id]) as mongodb:
        skills = MongoSkillRepository(mongodb.database)
        chunks = MongoSkillChunkRepository(mongodb.database)
        for document in (target, other, superseded):
            await skills.create(document)
        await chunks.create(
            chunk_document(settings, target, axis=0, content="Payment idempotency keys.")
        )
        await chunks.create(
            chunk_document(settings, other, axis=1, content="Button spacing guidance.")
        )
        await chunks.create(
            chunk_document(settings, superseded, axis=0, content="Historical payment rules.")
        )

        service = RetrievalService(skills, chunks, UnitVectorEmbedder(settings, axis=0))
        active = await wait_for_indexed_chunks(service, expected=1, category="phase6-backend")

        # Ranking: the chunk sharing the query's axis scores highest.
        assert [result.skill.skill_id for result in active] == [target_id]
        assert active[0].score > 0.9
        assert active[0].chunk.content == "Payment idempotency keys."
        assert active[0].chunk.embedding_model == settings.embedding_model

        # Active-only is the default, so the superseded version is excluded.
        assert all(result.skill.status == "active" for result in active)

        history = await wait_for_indexed_chunks(
            service,
            expected=1,
            skill_id=history_id,
            skill_version="0.9.0",
        )
        assert [result.skill.version for result in history] == ["0.9.0"]
        assert history[0].skill.status == "superseded"

        # Metadata filters.
        by_tag = await service.vector_search("payment idempotency", tags=["payments"])
        by_trust = await service.vector_search(
            "payment idempotency",
            trust_level="external",
        )
        by_skill = await service.vector_search("payment idempotency", skill_id=other_id)
        excluded = await service.vector_search(
            "payment idempotency",
            category="phase6-backend",
            trust_level="external",
        )

        assert [result.skill.skill_id for result in by_tag] == [target_id]
        assert [result.skill.skill_id for result in by_trust] == [other_id]
        assert [result.skill.skill_id for result in by_skill] == [other_id]
        assert excluded == ()

        # Top-K is honoured and results stay ordered by score.
        everything = await service.vector_search("payment idempotency", limit=1)
        scores = [result.score for result in everything]
        assert len(everything) == 1
        assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_the_vector_index_is_built_once_and_reconciles_idempotently() -> None:
    settings = get_settings()
    mongodb = MongoDBClient(settings)
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        first = await ensure_vector_indexes(
            mongodb.database,
            dimensions=settings.embedding_dimensions,
        )
        repeated = await ensure_vector_indexes(
            mongodb.database,
            dimensions=settings.embedding_dimensions,
        )
        status = await describe_vector_index(mongodb.database)
    finally:
        await mongodb.close()

    assert first in {"created", "unchanged", "updated"}
    assert repeated == "unchanged"
    assert status.exists is True
    assert status.dimensions == settings.embedding_dimensions
    assert status.similarity == "cosine"


@pytest.mark.asyncio
async def test_skill_vectors_carry_no_project_scope() -> None:
    """Skills are global knowledge, so no project can be leaked through them."""
    settings = get_settings()
    run_id = str(ObjectId())
    skill_id = f"phase6-isolation-{run_id}"
    document = skill_document(
        skill_id,
        category="phase6-backend",
        tags=["phase6"],
        trust_level="trusted",
    )

    async with vector_database([skill_id]) as mongodb:
        skills = MongoSkillRepository(mongodb.database)
        chunks = MongoSkillChunkRepository(mongodb.database)
        await skills.create(document)
        stored = await chunks.create(
            chunk_document(settings, document, axis=0, content="Global engineering rule.")
        )

        service = RetrievalService(skills, chunks, UnitVectorEmbedder(settings, axis=0))
        results = await wait_for_indexed_chunks(service, expected=1, skill_id=skill_id)

    assert "project_id" not in stored.model_dump()
    assert results
    assert all("project_id" not in result.chunk.model_dump() for result in results)
    assert SkillVersionKey(skill_id, "1.0.0") == SkillVersionKey(
        results[0].chunk.skill_id,
        results[0].chunk.skill_version,
    )
