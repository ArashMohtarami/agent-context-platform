"""Project-context repository query and isolation tests against a double."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.db.client_types import MongoDocument
from app.db.context_indexes import CONTEXT_TEXT_INDEX, CONTEXT_VECTOR_INDEX, ContextQueryFilters
from app.db.errors import SearchUnavailableError
from app.repositories.context_repository import MongoProjectContextRepository
from app.repositories.errors import ProjectIsolationError
from tests.project_doubles import context

PROJECT = "sampleco-payment"
OTHER_PROJECT = "sampleco-wallet"
DIMENSIONS = 4
VECTOR = [0.5] * DIMENSIONS


def stored_document(**overrides: object) -> MongoDocument:
    """Build a stored context document exactly as MongoDB returns it."""
    document = context(PROJECT, embedded=True, **overrides)  # type: ignore[arg-type]
    return cast(MongoDocument, document.model_dump(by_alias=True, mode="python"))


def build_repository(
    results: list[MongoDocument] | None = None,
    *,
    project_id: str = PROJECT,
    error: Exception | None = None,
) -> tuple[MongoProjectContextRepository, MagicMock]:
    """Build a repository whose aggregation pipeline is observable."""
    collection = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=list(results or []))
    collection.aggregate = AsyncMock(return_value=cursor, side_effect=error)
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)
    repository = MongoProjectContextRepository(
        cast(AsyncDatabase[MongoDocument], database),
        project_id,
    )
    return repository, collection


def pipeline_of(collection: MagicMock) -> list[dict[str, Any]]:
    """Return the aggregation pipeline the repository submitted."""
    return cast(list[dict[str, Any]], collection.aggregate.await_args.args[0])


@pytest.mark.asyncio
async def test_vector_search_scores_and_bounds_results() -> None:
    repository, collection = build_repository([{**stored_document(), "score": 0.93}])

    matches = await repository.vector_search(
        VECTOR,
        limit=5,
        num_candidates=50,
        filters=ContextQueryFilters(project_id=PROJECT),
    )

    pipeline = pipeline_of(collection)
    stage = pipeline[0]["$vectorSearch"]
    assert stage["index"] == CONTEXT_VECTOR_INDEX
    assert stage["path"] == "embedding"
    assert stage["numCandidates"] == 50
    assert stage["limit"] == 5
    assert pipeline[1] == {"$addFields": {"score": {"$meta": "vectorSearchScore"}}}
    assert len(matches) == 1
    assert matches[0].score == 0.93
    assert matches[0].context.project_id == PROJECT


@pytest.mark.asyncio
async def test_the_vector_query_always_pre_filters_by_project() -> None:
    repository, collection = build_repository([])

    await repository.vector_search(
        VECTOR,
        limit=5,
        num_candidates=50,
        filters=ContextQueryFilters(project_id=PROJECT, status=None),
    )

    assert pipeline_of(collection)[0]["$vectorSearch"]["filter"] == {
        "$and": [{"project_id": {"$eq": PROJECT}}, {"org_id": "org-test"}]
    }


@pytest.mark.asyncio
async def test_text_search_scores_bounds_and_filters_results() -> None:
    repository, collection = build_repository([{**stored_document(), "score": 4.2}])

    matches = await repository.text_search(
        "idempotency",
        limit=3,
        filters=ContextQueryFilters(project_id=PROJECT),
    )

    pipeline = pipeline_of(collection)
    assert pipeline[0]["$search"]["index"] == CONTEXT_TEXT_INDEX
    compound = pipeline[0]["$search"]["compound"]
    assert {"equals": {"path": "project_id", "value": PROJECT}} in compound["filter"]
    assert pipeline[1] == {"$addFields": {"score": {"$meta": "searchScore"}}}
    assert pipeline[2] == {"$limit": 3}
    assert matches[0].score == 4.2


@pytest.mark.asyncio
async def test_phrase_search_reaches_the_context_pipeline() -> None:
    repository, collection = build_repository([])

    await repository.text_search(
        "idempotency key",
        limit=3,
        phrase=True,
        filters=ContextQueryFilters(project_id=PROJECT),
    )

    compound = pipeline_of(collection)[0]["$search"]["compound"]
    assert compound["must"][0]["phrase"]["query"] == "idempotency key"


@pytest.mark.asyncio
async def test_a_query_for_another_project_is_refused() -> None:
    repository, collection = build_repository([])
    foreign = ContextQueryFilters(project_id=OTHER_PROJECT)

    with pytest.raises(ProjectIsolationError, match="different project"):
        await repository.vector_search(VECTOR, limit=5, num_candidates=50, filters=foreign)
    with pytest.raises(ProjectIsolationError, match="different project"):
        await repository.text_search("idempotency", limit=5, filters=foreign)

    collection.aggregate.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_write_for_another_project_is_refused() -> None:
    repository, _ = build_repository([])

    with pytest.raises(ProjectIsolationError, match="different project"):
        await repository.create(context(OTHER_PROJECT))


@pytest.mark.asyncio
async def test_reads_are_scoped_to_the_repository_s_project() -> None:
    repository, _ = build_repository([])
    collection = repository._collection
    collection.find_one = AsyncMock(return_value=None)
    document = context(PROJECT)

    await repository.get(document.id)

    assert collection.find_one.await_args.args[0] == {
        "org_id": "org-test",
        "_id": document.id,
        "project_id": PROJECT,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 51, True])
async def test_limits_outside_the_supported_range_are_rejected(limit: int) -> None:
    repository, collection = build_repository([])
    scope = ContextQueryFilters(project_id=PROJECT)

    with pytest.raises(ValueError, match="limit"):
        await repository.text_search("idempotency", limit=limit, filters=scope)

    collection.aggregate.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unusable_candidate_pool_is_rejected() -> None:
    repository, collection = build_repository([])

    with pytest.raises(ValueError, match="num_candidates"):
        await repository.vector_search(
            VECTOR,
            limit=5,
            num_candidates=1,
            filters=ContextQueryFilters(project_id=PROJECT),
        )

    collection.aggregate.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_search_reports_unavailable_infrastructure() -> None:
    repository, _ = build_repository(error=PyMongoError("index is not queryable"))

    with pytest.raises(SearchUnavailableError, match="could not be served"):
        await repository.text_search(
            "idempotency",
            limit=5,
            filters=ContextQueryFilters(project_id=PROJECT),
        )


@pytest.mark.asyncio
async def test_an_unusable_score_is_refused() -> None:
    repository, _ = build_repository([{**stored_document(), "score": "high"}])

    with pytest.raises(SearchUnavailableError, match="unusable score"):
        await repository.text_search(
            "idempotency",
            limit=5,
            filters=ContextQueryFilters(project_id=PROJECT),
        )


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
