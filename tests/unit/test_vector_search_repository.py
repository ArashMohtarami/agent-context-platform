"""Skill chunk vector query tests against a MongoDB double."""

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.db.client_types import MongoDocument
from app.db.errors import SearchUnavailableError
from app.models.skill_chunk import SkillChunkDocument
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.skills.types import SkillVersionKey

DIMENSIONS = 4
QUERY_VECTOR = [0.1, 0.2, 0.3, 0.4]


def chunk_document(chunk_index: int = 0, skill_version: str = "1.0.0") -> MongoDocument:
    """Build a stored chunk exactly as MongoDB returns it."""
    document = SkillChunkDocument(
        skill_id="fastapi-production",
        skill_version=skill_version,
        chunk_id=f"chunk-{chunk_index:04d}",
        content="Payment requests use idempotency keys.",
        section="Rules",
        chunk_index=chunk_index,
        tokens=6,
        embedding=[0.5] * DIMENSIONS,
        embedding_model="stub-embedding-model",
        embedding_dimensions=DIMENSIONS,
        embedding_generated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return cast(MongoDocument, document.model_dump(by_alias=True, mode="python"))


def build_repository(
    results: list[MongoDocument] | None = None,
    *,
    error: Exception | None = None,
) -> tuple[MongoSkillChunkRepository, MagicMock]:
    """Build a repository whose aggregation pipeline is observable."""
    collection = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=list(results or []))
    collection.aggregate = AsyncMock(return_value=cursor, side_effect=error)
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)
    repository = MongoSkillChunkRepository(cast(AsyncDatabase[MongoDocument], database))
    return repository, collection


def search_stage(collection: MagicMock) -> dict[str, Any]:
    """Return the `$vectorSearch` stage the repository submitted."""
    pipeline = collection.aggregate.await_args.args[0]
    return cast(dict[str, Any], pipeline[0]["$vectorSearch"])


@pytest.mark.asyncio
async def test_the_pipeline_requests_top_k_with_scores() -> None:
    repository, collection = build_repository([{**chunk_document(), "score": 0.91}])

    matches = await repository.vector_search(QUERY_VECTOR, limit=5, num_candidates=50)

    pipeline = collection.aggregate.await_args.args[0]
    assert pipeline[0]["$vectorSearch"] == {
        "index": "skill_chunk_vector_index",
        "path": "embedding",
        "queryVector": QUERY_VECTOR,
        "numCandidates": 50,
        "limit": 5,
    }
    assert pipeline[1] == {"$addFields": {"score": {"$meta": "vectorSearchScore"}}}
    assert len(matches) == 1
    assert matches[0].score == 0.91
    assert matches[0].chunk.chunk_id == "chunk-0000"
    assert matches[0].key == SkillVersionKey("fastapi-production", "1.0.0")


@pytest.mark.asyncio
async def test_an_unfiltered_query_carries_no_filter() -> None:
    repository, collection = build_repository([])

    await repository.vector_search(QUERY_VECTOR, limit=3, num_candidates=30)

    assert "filter" not in search_stage(collection)


@pytest.mark.asyncio
async def test_one_version_is_pre_filtered_exactly() -> None:
    repository, collection = build_repository([])

    await repository.vector_search(
        QUERY_VECTOR,
        limit=3,
        num_candidates=30,
        skill_versions=[SkillVersionKey("fastapi-production", "1.0.0")],
    )

    assert search_stage(collection)["filter"] == {
        "$and": [
            {"skill_id": {"$eq": "fastapi-production"}},
            {"skill_version": {"$eq": "1.0.0"}},
        ]
    }


@pytest.mark.asyncio
async def test_several_versions_are_pre_filtered_as_exact_pairs() -> None:
    repository, collection = build_repository([])

    await repository.vector_search(
        QUERY_VECTOR,
        limit=3,
        num_candidates=30,
        skill_versions=[
            SkillVersionKey("fastapi-production", "1.0.0"),
            SkillVersionKey("strict-python", "2.1.0"),
        ],
    )

    clauses = search_stage(collection)["filter"]["$or"]
    assert clauses == [
        {
            "$and": [
                {"skill_id": {"$eq": "fastapi-production"}},
                {"skill_version": {"$eq": "1.0.0"}},
            ]
        },
        {
            "$and": [
                {"skill_id": {"$eq": "strict-python"}},
                {"skill_version": {"$eq": "2.1.0"}},
            ]
        },
    ]


@pytest.mark.asyncio
async def test_results_preserve_ranking_order_and_source_metadata() -> None:
    repository, _ = build_repository(
        [
            {**chunk_document(0), "score": 0.93},
            {**chunk_document(1), "score": 0.77},
        ]
    )

    matches = await repository.vector_search(QUERY_VECTOR, limit=2, num_candidates=20)

    assert [match.score for match in matches] == [0.93, 0.77]
    assert [match.chunk.chunk_index for match in matches] == [0, 1]
    assert all(match.chunk.embedding_model == "stub-embedding-model" for match in matches)
    assert all(match.chunk.section == "Rules" for match in matches)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 51, True])
async def test_limits_outside_the_supported_range_are_rejected(limit: int) -> None:
    repository, collection = build_repository([])

    with pytest.raises(ValueError, match="limit"):
        await repository.vector_search(QUERY_VECTOR, limit=limit, num_candidates=100)

    collection.aggregate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("num_candidates", [4, 10_001, True])
async def test_unusable_candidate_pools_are_rejected(num_candidates: int) -> None:
    repository, collection = build_repository([])

    with pytest.raises(ValueError, match="num_candidates"):
        await repository.vector_search(QUERY_VECTOR, limit=5, num_candidates=num_candidates)

    collection.aggregate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "embedding",
    [[], [float("nan")], [float("inf")], ["0.1"], [True], "vector", [0.1] * 4_097],
)
async def test_unusable_query_vectors_are_rejected(embedding: object) -> None:
    repository, collection = build_repository([])

    with pytest.raises(ValueError):
        await repository.vector_search(
            cast(list[float], embedding),
            limit=5,
            num_candidates=50,
        )

    collection.aggregate.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_search_reports_unavailable_infrastructure() -> None:
    repository, _ = build_repository(error=PyMongoError("index is not queryable"))

    with pytest.raises(SearchUnavailableError, match="could not be served"):
        await repository.vector_search(QUERY_VECTOR, limit=5, num_candidates=50)


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [None, float("nan"), "high", True])
async def test_an_unusable_score_is_refused(score: object) -> None:
    document = chunk_document()
    if score is not None:
        document["score"] = score
    repository, _ = build_repository([document])

    with pytest.raises(SearchUnavailableError, match="unusable score"):
        await repository.vector_search(QUERY_VECTOR, limit=5, num_candidates=50)


@pytest.mark.asyncio
async def test_the_score_is_not_stored_on_the_returned_chunk() -> None:
    repository, _ = build_repository([{**chunk_document(), "score": 0.5}])

    matches = await repository.vector_search(QUERY_VECTOR, limit=1, num_candidates=10)

    assert "score" not in matches[0].chunk.model_dump()
