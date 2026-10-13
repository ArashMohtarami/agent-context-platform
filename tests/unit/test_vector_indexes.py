"""Vector index definition, reconciliation, and health tests."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.operations import SearchIndexModel

from app.db.client_types import MongoDocument
from app.db.errors import SearchUnavailableError
from app.db.vector_indexes import (
    SKILL_CHUNK_VECTOR_INDEX,
    VECTOR_FILTER_PATHS,
    check_vector_search_health,
    describe_vector_index,
    ensure_vector_indexes,
    skill_chunk_vector_definition,
)


def build_database(
    indexes: list[MongoDocument] | None = None,
    *,
    list_error: Exception | None = None,
    create_error: Exception | None = None,
    update_error: Exception | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build a database double whose search-index calls are observable."""
    collection = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=list(indexes or []))
    if list_error is None:
        collection.list_search_indexes = AsyncMock(return_value=cursor)
    else:
        collection.list_search_indexes = AsyncMock(side_effect=list_error)
    collection.create_search_index = AsyncMock(
        side_effect=create_error,
        return_value=SKILL_CHUNK_VECTOR_INDEX,
    )
    collection.update_search_index = AsyncMock(side_effect=update_error)
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)
    return database, collection


def typed(database: MagicMock) -> AsyncDatabase[MongoDocument]:
    """Cast a double to the database type the production code expects."""
    return cast(AsyncDatabase[MongoDocument], database)


def existing_index(
    dimensions: int = 384,
    *,
    similarity: str = "cosine",
    queryable: bool = True,
    status: str = "READY",
    filters: tuple[str, ...] = VECTOR_FILTER_PATHS,
) -> MongoDocument:
    """Build a `$listSearchIndexes` document as MongoDB returns it."""
    return {
        "id": "6800000000000000000000aa",
        "name": SKILL_CHUNK_VECTOR_INDEX,
        "type": "vectorSearch",
        "status": status,
        "queryable": queryable,
        "latestDefinition": {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": dimensions,
                    "similarity": similarity,
                },
                *({"type": "filter", "path": path} for path in filters),
            ]
        },
    }


def test_the_definition_uses_cosine_over_the_embedding_path() -> None:
    definition = skill_chunk_vector_definition(384)

    fields = cast(list[dict[str, Any]], definition["fields"])
    vector_field = fields[0]
    assert vector_field == {
        "type": "vector",
        "path": "embedding",
        "numDimensions": 384,
        "similarity": "cosine",
    }
    assert [field["path"] for field in fields[1:]] == list(VECTOR_FILTER_PATHS)
    assert all(field["type"] == "filter" for field in fields[1:])


def test_the_definition_takes_dimensions_from_its_caller() -> None:
    assert skill_chunk_vector_definition(768) != skill_chunk_vector_definition(384)

    fields = cast(list[dict[str, Any]], skill_chunk_vector_definition(768)["fields"])

    assert fields[0]["numDimensions"] == 768


@pytest.mark.parametrize("dimensions", [0, -1, 4_097, True, 1.5])
def test_unusable_dimensions_are_rejected(dimensions: object) -> None:
    with pytest.raises(ValueError, match="dimensions"):
        skill_chunk_vector_definition(cast(int, dimensions))


@pytest.mark.asyncio
async def test_a_missing_index_is_created_as_a_vector_search_index() -> None:
    database, collection = build_database([])

    action = await ensure_vector_indexes(typed(database), dimensions=384)

    assert action == "created"
    model = collection.create_search_index.await_args.args[0]
    assert isinstance(model, SearchIndexModel)
    document = model.document
    assert document["name"] == SKILL_CHUNK_VECTOR_INDEX
    assert document["type"] == "vectorSearch"
    assert document["definition"] == skill_chunk_vector_definition(384)
    collection.update_search_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_identical_index_is_left_untouched() -> None:
    database, collection = build_database([existing_index(384)])

    action = await ensure_vector_indexes(typed(database), dimensions=384)

    assert action == "unchanged"
    collection.create_search_index.assert_not_awaited()
    collection.update_search_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_reconciliation_is_idempotent() -> None:
    database, collection = build_database([existing_index(384)])

    actions = [
        await ensure_vector_indexes(typed(database), dimensions=384),
        await ensure_vector_indexes(typed(database), dimensions=384),
        await ensure_vector_indexes(typed(database), dimensions=384),
    ]

    assert actions == ["unchanged", "unchanged", "unchanged"]
    collection.create_search_index.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    [
        existing_index(768),
        existing_index(384, similarity="euclidean"),
        existing_index(384, filters=("skill_id",)),
    ],
)
async def test_a_stale_definition_is_updated_in_place(stored: MongoDocument) -> None:
    database, collection = build_database([stored])

    action = await ensure_vector_indexes(typed(database), dimensions=384)

    assert action == "updated"
    collection.update_search_index.assert_awaited_once_with(
        SKILL_CHUNK_VECTOR_INDEX,
        skill_chunk_vector_definition(384),
    )


@pytest.mark.asyncio
async def test_a_concurrent_creation_race_is_not_an_error() -> None:
    database, _ = build_database([], create_error=OperationFailure("exists", 68))

    assert await ensure_vector_indexes(typed(database), dimensions=384) == "unchanged"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        {"list_error": PyMongoError("mongot is unreachable")},
        {"create_error": PyMongoError("mongot is unreachable")},
        {"create_error": OperationFailure("search is disabled", 197)},
    ],
)
async def test_unavailable_search_infrastructure_raises_a_safe_error(
    failure: dict[str, Exception],
) -> None:
    database, _ = build_database([], **failure)

    with pytest.raises(SearchUnavailableError) as raised:
        await ensure_vector_indexes(typed(database), dimensions=384)

    assert "mongot" not in str(raised.value).lower() or "unreachable" not in str(raised.value)


@pytest.mark.asyncio
async def test_a_failing_update_raises_a_safe_error() -> None:
    database, _ = build_database(
        [existing_index(768)],
        update_error=PyMongoError("index rebuild refused"),
    )

    with pytest.raises(SearchUnavailableError, match="could not be updated"):
        await ensure_vector_indexes(typed(database), dimensions=384)


@pytest.mark.asyncio
async def test_an_existing_index_is_described_for_operators() -> None:
    database, _ = build_database([existing_index(384)])

    status = await describe_vector_index(typed(database))

    assert status.exists is True
    assert status.queryable is True
    assert status.ready is True
    assert status.status == "READY"
    assert status.dimensions == 384
    assert status.similarity == "cosine"


@pytest.mark.asyncio
async def test_a_building_index_is_not_yet_queryable() -> None:
    database, _ = build_database([existing_index(384, queryable=False, status="BUILDING")])

    status = await describe_vector_index(typed(database))

    assert status.exists is True
    assert status.ready is False
    assert status.status == "BUILDING"


@pytest.mark.asyncio
async def test_a_missing_index_is_reported_as_absent() -> None:
    database, _ = build_database([])

    status = await describe_vector_index(typed(database))

    assert (status.exists, status.queryable, status.ready) == (False, False, False)


@pytest.mark.asyncio
async def test_health_confirms_the_index_matches_the_active_model() -> None:
    database, _ = build_database([existing_index(384)])

    health = await check_vector_search_health(typed(database), 384)

    assert health.available is True
    assert health.ready is True
    assert health.dimensions_match is True


@pytest.mark.asyncio
async def test_health_reports_an_index_built_for_another_model() -> None:
    database, _ = build_database([existing_index(768)])

    health = await check_vector_search_health(typed(database), 384)

    assert health.available is True
    assert health.dimensions_match is False


@pytest.mark.asyncio
async def test_health_never_raises_when_search_is_unavailable() -> None:
    database, _ = build_database([], list_error=PyMongoError("coordinator is down"))

    health = await check_vector_search_health(typed(database), 384)

    assert health.available is False
    assert health.ready is False
    assert health.detail == "SearchUnavailableError"
    assert health.index is None
