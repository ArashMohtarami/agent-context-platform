"""Project-context search index definition, query, and reconciliation tests."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.operations import SearchIndexModel

from app.db.client_types import MongoDocument
from app.db.context_indexes import (
    CONTEXT_FILTER_PATHS,
    CONTEXT_TEXT_INDEX,
    CONTEXT_VECTOR_INDEX,
    TITLE_BOOST,
    ContextQueryFilters,
    check_context_text_health,
    check_context_vector_health,
    context_filter_clauses,
    context_text_definition,
    context_text_query,
    context_vector_definition,
    context_vector_filter,
    describe_context_text_index,
    describe_context_vector_index,
    ensure_context_text_index,
    ensure_context_vector_index,
)
from app.db.errors import SearchUnavailableError
from app.db.text_indexes import EXACT_MULTI, IDENTIFIER_BOOST, STANDARD_ANALYZER

DIMENSIONS = 384
PROJECT = "sampleco-payment"


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
    collection.create_search_index = AsyncMock(side_effect=create_error)
    collection.update_search_index = AsyncMock(side_effect=update_error)
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)
    return database, collection


def typed(database: MagicMock) -> AsyncDatabase[MongoDocument]:
    """Cast a double to the database type the production code expects."""
    return cast(AsyncDatabase[MongoDocument], database)


def existing_index(
    name: str,
    definition: MongoDocument,
    *,
    queryable: bool = True,
    status: str = "READY",
) -> MongoDocument:
    """Build a `$listSearchIndexes` document as MongoDB returns it."""
    return {
        "id": "6800000000000000000000cc",
        "name": name,
        "status": status,
        "queryable": queryable,
        "latestDefinition": definition,
    }


def mapped_fields(definition: MongoDocument) -> dict[str, Any]:
    """Return the field mappings of a text definition."""
    mappings = cast(dict[str, Any], definition["mappings"])
    return cast(dict[str, Any], mappings["fields"])


def test_the_vector_index_filters_on_every_field_a_query_can_restrict() -> None:
    definition = context_vector_definition(DIMENSIONS)
    fields = cast(list[dict[str, Any]], definition["fields"])
    vector = next(field for field in fields if field["type"] == "vector")
    filters = {field["path"] for field in fields if field["type"] == "filter"}

    assert vector["numDimensions"] == DIMENSIONS
    assert vector["similarity"] == "cosine"
    assert vector["path"] == "embedding"
    # Project isolation is a pre-filter, not an afterthought.
    assert "project_id" in filters
    assert filters == set(CONTEXT_FILTER_PATHS)


@pytest.mark.parametrize("dimensions", [0, -1, 4_097, True])
def test_unusable_vector_dimensions_are_rejected(dimensions: int) -> None:
    with pytest.raises(ValueError, match="dimensions"):
        context_vector_definition(dimensions)


def test_the_text_index_maps_content_title_and_every_filter_field() -> None:
    definition = context_text_definition()
    fields = mapped_fields(definition)
    mappings = cast(dict[str, Any], definition["mappings"])

    assert mappings["dynamic"] is False
    assert set(fields) == {
        "org_id",
        "_id",
        "content",
        "title",
        "project_id",
        "type",
        "status",
        "tags",
        "importance",
    }
    assert fields["content"]["analyzer"] == STANDARD_ANALYZER
    assert fields["content"]["multi"][EXACT_MULTI]["analyzer"] == "identifier_exact"
    assert fields["title"]["analyzer"] == STANDARD_ANALYZER
    assert fields["project_id"] == {"type": "token"}
    assert fields["importance"] == {"type": "number"}
    # Vectors must never reach the text index.
    assert "embedding" not in repr(definition)


def test_a_context_query_searches_content_identifiers_and_the_title() -> None:
    compound = context_text_query("payment idempotency")
    should = cast(list[dict[str, Any]], compound["should"])
    paths = [clause[next(iter(clause))]["path"] for clause in should]

    assert compound["minimumShouldMatch"] == 1
    assert "content" in paths
    assert {"value": "content", "multi": EXACT_MULTI} in paths
    assert "title" in paths
    title_clause = next(
        clause for clause in should if clause.get("text", {}).get("path") == "title"
    )
    assert title_clause["text"]["score"] == {"boost": {"value": TITLE_BOOST}}
    exact = next(
        clause
        for clause in should
        if clause.get("text", {}).get("path") == {"value": "content", "multi": EXACT_MULTI}
    )
    assert exact["text"]["score"] == {"boost": {"value": IDENTIFIER_BOOST}}


def test_phrase_search_requires_the_words_together() -> None:
    compound = context_text_query("idempotency key", phrase=True)

    must = cast(list[dict[str, Any]], compound["must"])
    assert must[0]["phrase"]["query"] == "idempotency key"


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_an_empty_context_query_is_rejected(query: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        context_text_query(query)


def test_a_context_query_scope_always_names_a_project() -> None:
    with pytest.raises(ValueError, match="require a project"):
        ContextQueryFilters(project_id="")


@pytest.mark.parametrize("importance", [-0.1, 1.5])
def test_an_unusable_importance_floor_is_rejected(importance: float) -> None:
    with pytest.raises(ValueError, match="minimum_importance"):
        ContextQueryFilters(project_id=PROJECT, minimum_importance=importance)


def test_the_vector_pre_filter_restricts_to_one_project() -> None:
    built = context_vector_filter(ContextQueryFilters(project_id=PROJECT, status=None))

    assert built == {"project_id": {"$eq": PROJECT}}


def test_the_vector_pre_filter_carries_every_requested_restriction() -> None:
    built = context_vector_filter(
        ContextQueryFilters(
            project_id=PROJECT,
            types=("architecture_decision", "business_rule"),
            tags=("payments", "critical"),
            status="active",
            minimum_importance=0.7,
        )
    )
    clauses = cast(list[dict[str, Any]], built["$and"])

    assert {"project_id": {"$eq": PROJECT}} in clauses
    assert {"type": {"$in": ["architecture_decision", "business_rule"]}} in clauses
    assert {"status": {"$eq": "active"}} in clauses
    assert {"tags": {"$eq": "payments"}} in clauses
    assert {"tags": {"$eq": "critical"}} in clauses
    assert {"importance": {"$gte": 0.7}} in clauses


def test_the_lexical_filter_restricts_to_one_project() -> None:
    clauses = context_filter_clauses(ContextQueryFilters(project_id=PROJECT, status=None))

    assert clauses == [{"equals": {"path": "project_id", "value": PROJECT}}]


def test_the_lexical_filter_carries_every_requested_restriction() -> None:
    clauses = context_filter_clauses(
        ContextQueryFilters(
            project_id=PROJECT,
            types=("api",),
            tags=("payments",),
            status="archived",
            minimum_importance=0.5,
        )
    )

    assert {"equals": {"path": "project_id", "value": PROJECT}} in clauses
    assert {"equals": {"path": "status", "value": "archived"}} in clauses
    assert {"equals": {"path": "tags", "value": "payments"}} in clauses
    assert {"range": {"path": "importance", "gte": 0.5}} in clauses
    types = next(clause for clause in clauses if "compound" in clause)
    assert types["compound"]["should"] == [{"equals": {"path": "type", "value": "api"}}]


@pytest.mark.asyncio
async def test_a_missing_context_vector_index_is_created() -> None:
    database, collection = build_database([])

    action = await ensure_context_vector_index(typed(database), dimensions=DIMENSIONS)

    assert action == "created"
    model = collection.create_search_index.await_args.args[0]
    assert isinstance(model, SearchIndexModel)
    assert model.document["name"] == CONTEXT_VECTOR_INDEX
    assert model.document["type"] == "vectorSearch"
    assert database.__getitem__.call_args.args[0] == "project_context"


@pytest.mark.asyncio
async def test_a_missing_context_text_index_is_created() -> None:
    database, collection = build_database([])

    action = await ensure_context_text_index(typed(database))

    assert action == "created"
    model = collection.create_search_index.await_args.args[0]
    assert model.document["name"] == CONTEXT_TEXT_INDEX
    assert model.document["type"] == "search"
    assert model.document["definition"] == context_text_definition()


@pytest.mark.asyncio
async def test_identical_context_indexes_are_left_untouched() -> None:
    vector_database, vector_collection = build_database(
        [existing_index(CONTEXT_VECTOR_INDEX, context_vector_definition(DIMENSIONS))]
    )
    text_database, text_collection = build_database(
        [existing_index(CONTEXT_TEXT_INDEX, context_text_definition())]
    )

    vector_action = await ensure_context_vector_index(typed(vector_database), dimensions=DIMENSIONS)
    text_action = await ensure_context_text_index(typed(text_database))

    assert (vector_action, text_action) == ("unchanged", "unchanged")
    vector_collection.update_search_index.assert_not_awaited()
    text_collection.update_search_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_changed_dimension_count_updates_the_context_vector_index() -> None:
    database, collection = build_database(
        [existing_index(CONTEXT_VECTOR_INDEX, context_vector_definition(8))]
    )

    action = await ensure_context_vector_index(typed(database), dimensions=DIMENSIONS)

    assert action == "updated"
    collection.update_search_index.assert_awaited_once_with(
        CONTEXT_VECTOR_INDEX,
        context_vector_definition(DIMENSIONS),
    )


@pytest.mark.asyncio
async def test_a_removed_context_field_mapping_is_restored() -> None:
    stale = context_text_definition()
    del mapped_fields(stale)["title"]
    database, collection = build_database([existing_index(CONTEXT_TEXT_INDEX, stale)])

    assert await ensure_context_text_index(typed(database)) == "updated"
    collection.update_search_index.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_concurrent_context_index_creation_race_is_not_an_error() -> None:
    database, _ = build_database([], create_error=OperationFailure("exists", 68))

    assert await ensure_context_text_index(typed(database)) == "unchanged"


@pytest.mark.asyncio
async def test_unavailable_search_infrastructure_raises_a_safe_error() -> None:
    database, _ = build_database([], list_error=PyMongoError("mongot is unreachable"))

    with pytest.raises(SearchUnavailableError):
        await ensure_context_vector_index(typed(database), dimensions=DIMENSIONS)


@pytest.mark.asyncio
async def test_the_context_index_states_are_described_for_operators() -> None:
    vector_database, _ = build_database(
        [existing_index(CONTEXT_VECTOR_INDEX, context_vector_definition(DIMENSIONS))]
    )
    text_database, _ = build_database(
        [
            existing_index(
                CONTEXT_TEXT_INDEX, context_text_definition(), status="BUILDING", queryable=False
            )
        ]
    )

    vector = await describe_context_vector_index(typed(vector_database))
    text = await describe_context_text_index(typed(text_database))

    assert (vector.exists, vector.queryable, vector.ready) == (True, True, True)
    assert (vector.dimensions, vector.similarity) == (DIMENSIONS, "cosine")
    assert text.ready is False
    assert text.status == "BUILDING"
    assert text.analyzer == STANDARD_ANALYZER


@pytest.mark.asyncio
async def test_context_health_reports_ready_indexes() -> None:
    vector_database, _ = build_database(
        [existing_index(CONTEXT_VECTOR_INDEX, context_vector_definition(DIMENSIONS))]
    )
    text_database, _ = build_database(
        [existing_index(CONTEXT_TEXT_INDEX, context_text_definition())]
    )

    vector = await check_context_vector_health(typed(vector_database), DIMENSIONS)
    text = await check_context_text_health(typed(text_database))

    assert (vector.available, vector.ready, vector.dimensions_match) == (True, True, True)
    assert (text.available, text.ready) == (True, True)


@pytest.mark.asyncio
async def test_context_health_never_raises_when_search_is_unavailable() -> None:
    database, _ = build_database([], list_error=PyMongoError("coordinator is down"))

    vector = await check_context_vector_health(typed(database), DIMENSIONS)
    text = await check_context_text_health(typed(database))

    assert (vector.available, vector.ready) == (False, False)
    assert (text.available, text.ready) == (False, False)
    assert vector.detail == "SearchUnavailableError"
