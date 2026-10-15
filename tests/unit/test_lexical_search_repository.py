"""Skill chunk lexical query tests against a MongoDB double."""

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.db.client_types import MongoDocument
from app.db.errors import SearchUnavailableError
from app.db.text_indexes import SKILL_CHUNK_TEXT_INDEX
from app.models.skill_chunk import SkillChunkDocument
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.skills.types import SkillVersionKey

DIMENSIONS = 4


def chunk_document(
    chunk_index: int = 0, content: str = "Keycloak issues OAuth2 tokens."
) -> MongoDocument:
    """Build a stored chunk exactly as MongoDB returns it."""
    document = SkillChunkDocument(
        skill_id="security-baseline",
        skill_version="1.0.0",
        chunk_id=f"chunk-{chunk_index:04d}",
        content=content,
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


def pipeline_of(collection: MagicMock) -> list[dict[str, Any]]:
    """Return the aggregation pipeline the repository submitted."""
    return cast(list[dict[str, Any]], collection.aggregate.await_args.args[0])


@pytest.mark.asyncio
async def test_the_pipeline_searches_scores_and_bounds_results() -> None:
    repository, collection = build_repository([{**chunk_document(), "score": 4.2}])

    matches = await repository.text_search("Keycloak", limit=5)

    pipeline = pipeline_of(collection)
    assert list(pipeline[0]) == ["$search"]
    assert pipeline[0]["$search"]["index"] == SKILL_CHUNK_TEXT_INDEX
    assert pipeline[1] == {"$addFields": {"score": {"$meta": "searchScore"}}}
    assert pipeline[2] == {"$limit": 5}
    assert len(matches) == 1
    assert matches[0].score == 4.2
    assert matches[0].chunk.content == "Keycloak issues OAuth2 tokens."
    assert matches[0].key == SkillVersionKey("security-baseline", "1.0.0")


@pytest.mark.asyncio
async def test_a_query_without_skill_filters_still_has_tenant_prefilter() -> None:
    repository, collection = build_repository([])

    await repository.text_search("OAuth2", limit=3)

    assert pipeline_of(collection)[0]["$search"]["compound"]["filter"] == [
        {"equals": {"path": "org_id", "value": "org-test"}}
    ]


@pytest.mark.asyncio
async def test_metadata_scope_becomes_a_non_scoring_filter() -> None:
    repository, collection = build_repository([])

    await repository.text_search(
        "OAuth2",
        limit=3,
        skill_versions=[
            SkillVersionKey("security-baseline", "1.0.0"),
            SkillVersionKey("fastapi-production", "2.0.0"),
        ],
    )

    compound = pipeline_of(collection)[0]["$search"]["compound"]
    alternatives = compound["filter"][0]["compound"]["should"]
    assert len(alternatives) == 2
    assert alternatives[0]["compound"]["must"][0]["equals"]["value"] == "security-baseline"
    # Filters must not add score, so the should clauses stay the scoring ones.
    assert compound["minimumShouldMatch"] == 1


@pytest.mark.asyncio
async def test_phrase_search_reaches_the_pipeline() -> None:
    repository, collection = build_repository([])

    await repository.text_search("payment idempotency", limit=3, phrase=True)

    compound = pipeline_of(collection)[0]["$search"]["compound"]
    assert compound["must"][0]["phrase"]["query"] == "payment idempotency"


@pytest.mark.asyncio
async def test_results_preserve_relevance_order_and_source_metadata() -> None:
    repository, _ = build_repository(
        [
            {**chunk_document(0, "Keycloak realm settings."), "score": 7.5},
            {**chunk_document(1, "Keycloak client scopes."), "score": 3.1},
        ]
    )

    matches = await repository.text_search("Keycloak", limit=5)

    assert [match.score for match in matches] == [7.5, 3.1]
    assert [match.chunk.chunk_index for match in matches] == [0, 1]
    assert all(match.chunk.section == "Rules" for match in matches)


@pytest.mark.asyncio
async def test_a_query_matching_nothing_returns_no_results() -> None:
    repository, _ = build_repository([])

    assert await repository.text_search("Kubernetes", limit=5) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 51, True])
async def test_limits_outside_the_supported_range_are_rejected(limit: int) -> None:
    repository, collection = build_repository([])

    with pytest.raises(ValueError, match="limit"):
        await repository.text_search("Keycloak", limit=limit)

    collection.aggregate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
async def test_malformed_queries_are_rejected_before_mongodb(query: str) -> None:
    repository, collection = build_repository([])

    with pytest.raises(ValueError, match="must not be empty"):
        await repository.text_search(query, limit=5)

    collection.aggregate.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_search_reports_unavailable_infrastructure() -> None:
    repository, _ = build_repository(error=PyMongoError("index is not queryable"))

    with pytest.raises(SearchUnavailableError, match="could not be served"):
        await repository.text_search("Keycloak", limit=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [None, float("nan"), "high", True])
async def test_an_unusable_score_is_refused(score: object) -> None:
    document = chunk_document()
    if score is not None:
        document["score"] = score
    repository, _ = build_repository([document])

    with pytest.raises(SearchUnavailableError, match="unusable score"):
        await repository.text_search("Keycloak", limit=5)


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
