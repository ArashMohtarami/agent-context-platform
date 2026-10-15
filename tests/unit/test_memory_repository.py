"""Memory repository query and scope-isolation tests against a double."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.db.client_types import MongoDocument
from app.db.errors import SearchUnavailableError
from app.db.memory_indexes import MEMORY_TEXT_INDEX, MEMORY_VECTOR_INDEX, MemoryQueryFilters
from app.repositories.errors import ProjectIsolationError
from app.repositories.memory_repository import MongoMemoryRepository
from tests.memory_doubles import memory

PROJECT = "sampleco-payment"
OTHER_PROJECT = "sampleco-wallet"
VECTOR = [0.5] * 4
GLOBAL_SCOPE = MemoryQueryFilters(scopes=("global",))
PROJECT_SCOPE = MemoryQueryFilters(scopes=("global", "project"), project_id=PROJECT)


def stored_document(project_id: str | None = None, **overrides: object) -> MongoDocument:
    """Build a stored memory exactly as MongoDB returns it."""
    document = memory(project_id, embedded=True, **overrides)  # type: ignore[arg-type]
    return cast(MongoDocument, document.model_dump(by_alias=True, mode="python"))


def build_repository(
    results: list[MongoDocument] | None = None,
    *,
    project_id: str | None = None,
    error: Exception | None = None,
) -> tuple[MongoMemoryRepository, MagicMock]:
    """Build a repository whose aggregation pipeline is observable."""
    collection = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=list(results or []))
    collection.aggregate = AsyncMock(return_value=cursor, side_effect=error)
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)
    repository = MongoMemoryRepository(
        cast(AsyncDatabase[MongoDocument], database),
        project_id=project_id,
    )
    return repository, collection


def pipeline_of(collection: MagicMock) -> list[dict[str, Any]]:
    """Return the aggregation pipeline the repository submitted."""
    return cast(list[dict[str, Any]], collection.aggregate.await_args.args[0])


@pytest.mark.asyncio
async def test_vector_search_scores_and_bounds_results() -> None:
    repository, collection = build_repository([{**stored_document(), "score": 0.91}])

    matches = await repository.vector_search(
        VECTOR,
        limit=5,
        num_candidates=50,
        filters=GLOBAL_SCOPE,
    )

    pipeline = pipeline_of(collection)
    stage = pipeline[0]["$vectorSearch"]
    assert stage["index"] == MEMORY_VECTOR_INDEX
    assert stage["limit"] == 5
    assert pipeline[1] == {"$addFields": {"score": {"$meta": "vectorSearchScore"}}}
    assert matches[0].score == 0.91
    assert matches[0].memory.scope == "global"


@pytest.mark.asyncio
async def test_a_global_repository_pre_filters_to_global_memory() -> None:
    repository, collection = build_repository([])

    await repository.vector_search(
        VECTOR,
        limit=5,
        num_candidates=50,
        filters=MemoryQueryFilters(scopes=("global",), status=None),
    )

    assert pipeline_of(collection)[0]["$vectorSearch"]["filter"] == {
        "$and": [{"scope": {"$eq": "global"}}, {"org_id": "org-test"}]
    }


@pytest.mark.asyncio
async def test_a_project_repository_reaches_global_memory_and_its_own() -> None:
    repository, collection = build_repository([], project_id=PROJECT)

    await repository.text_search(
        "strict typing",
        limit=5,
        filters=MemoryQueryFilters(
            scopes=("global", "project"),
            project_id=PROJECT,
            status=None,
        ),
    )

    compound = pipeline_of(collection)[0]["$search"]["compound"]
    assert pipeline_of(collection)[0]["$search"]["index"] == MEMORY_TEXT_INDEX
    scope = compound["filter"][0]["compound"]["should"]
    assert {"equals": {"path": "scope", "value": "global"}} in scope
    assert any(
        entry.get("compound", {}).get("must", [{}])[-1]
        == {"equals": {"path": "project_id", "value": PROJECT}}
        for entry in scope
    )


@pytest.mark.asyncio
async def test_a_query_for_another_project_is_refused() -> None:
    repository, collection = build_repository([], project_id=PROJECT)
    foreign = MemoryQueryFilters(scopes=("global", "project"), project_id=OTHER_PROJECT)

    with pytest.raises(ProjectIsolationError, match="different project"):
        await repository.vector_search(VECTOR, limit=5, num_candidates=50, filters=foreign)
    with pytest.raises(ProjectIsolationError, match="different project"):
        await repository.text_search("strict typing", limit=5, filters=foreign)

    collection.aggregate.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_global_repository_cannot_search_project_memory() -> None:
    repository, collection = build_repository([])

    with pytest.raises(ProjectIsolationError):
        await repository.text_search(
            "strict typing",
            limit=5,
            filters=MemoryQueryFilters(scopes=("global", "project"), project_id=PROJECT),
        )

    collection.aggregate.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_write_for_another_scope_is_refused() -> None:
    repository, _ = build_repository([], project_id=PROJECT)

    with pytest.raises(ProjectIsolationError, match="different scope or project"):
        await repository.create(memory())
    with pytest.raises(ProjectIsolationError, match="different scope or project"):
        await repository.create(memory(OTHER_PROJECT))


@pytest.mark.asyncio
async def test_reads_are_scoped_to_the_repository_s_scope() -> None:
    repository, _ = build_repository([], project_id=PROJECT)
    collection = repository._collection
    collection.find_one = AsyncMock(return_value=None)
    document = memory(PROJECT)

    await repository.get(document.id)

    assert collection.find_one.await_args.args[0] == {
        "org_id": "org-test",
        "_id": document.id,
        "scope": "project",
        "project_id": PROJECT,
        "client_id": None,
    }


@pytest.mark.asyncio
async def test_phrase_search_reaches_the_memory_pipeline() -> None:
    repository, collection = build_repository([])

    await repository.text_search("strict typing", limit=3, phrase=True, filters=GLOBAL_SCOPE)

    compound = pipeline_of(collection)[0]["$search"]["compound"]
    assert compound["must"][0]["phrase"]["query"] == "strict typing"


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 51, True])
async def test_limits_outside_the_supported_range_are_rejected(limit: int) -> None:
    repository, collection = build_repository([])

    with pytest.raises(ValueError, match="limit"):
        await repository.text_search("strict typing", limit=limit, filters=GLOBAL_SCOPE)

    collection.aggregate.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unusable_candidate_pool_is_rejected() -> None:
    repository, collection = build_repository([])

    with pytest.raises(ValueError, match="num_candidates"):
        await repository.vector_search(VECTOR, limit=5, num_candidates=1, filters=GLOBAL_SCOPE)

    collection.aggregate.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_search_reports_unavailable_infrastructure() -> None:
    repository, _ = build_repository(error=PyMongoError("index is not queryable"))

    with pytest.raises(SearchUnavailableError, match="could not be served"):
        await repository.text_search("strict typing", limit=5, filters=GLOBAL_SCOPE)


@pytest.mark.asyncio
async def test_an_unusable_score_is_refused() -> None:
    repository, _ = build_repository([{**stored_document(), "score": None}])

    with pytest.raises(SearchUnavailableError, match="unusable score"):
        await repository.text_search("strict typing", limit=5, filters=GLOBAL_SCOPE)


@pytest.mark.asyncio
async def test_listing_filters_within_the_scope() -> None:
    repository, _ = build_repository([], project_id=PROJECT)
    collection = repository._collection
    mongo_cursor = MagicMock()
    mongo_cursor.sort = MagicMock(return_value=mongo_cursor)
    mongo_cursor.limit = MagicMock(return_value=mongo_cursor)
    mongo_cursor.to_list = AsyncMock(return_value=[])
    collection.find = MagicMock(return_value=mongo_cursor)

    await repository.list(memory_type="preference", status="active", tags=["python"])

    assert collection.find.call_args.args[0] == {
        "org_id": "org-test",
        "scope": "project",
        "project_id": PROJECT,
        "client_id": None,
        "type": "preference",
        "status": "active",
        "tags": {"$all": ["python"]},
    }


def test_the_scope_is_fixed_when_the_repository_is_built() -> None:
    global_repository, _ = build_repository([])
    project_repository, _ = build_repository([], project_id=PROJECT)

    assert (global_repository.scope, global_repository.project_id) == ("global", None)
    assert (project_repository.scope, project_repository.project_id) == ("project", PROJECT)


@pytest.mark.asyncio
async def test_scoped_search_results_keep_their_own_scope() -> None:
    repository, _ = build_repository(
        [
            {**stored_document(), "score": 0.9},
            {**stored_document(PROJECT), "score": 0.8},
        ],
        project_id=PROJECT,
    )

    matches = await repository.vector_search(
        VECTOR,
        limit=5,
        num_candidates=50,
        filters=PROJECT_SCOPE,
    )

    assert [match.memory.scope for match in matches] == ["global", "project"]
    assert [match.memory.project_id for match in matches] == [None, PROJECT]
    assert matches[0].key != matches[1].key


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
