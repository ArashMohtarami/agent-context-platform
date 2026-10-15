"""Memory search index definition, scope filter, and reconciliation tests."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.operations import SearchIndexModel

from app.db.client_types import MongoDocument
from app.db.errors import SearchUnavailableError
from app.db.memory_indexes import (
    MEMORY_FILTER_PATHS,
    MEMORY_TEXT_INDEX,
    MEMORY_VECTOR_INDEX,
    SUMMARY_BOOST,
    MemoryQueryFilters,
    check_memory_text_health,
    check_memory_vector_health,
    describe_memory_text_index,
    describe_memory_vector_index,
    ensure_memory_text_index,
    ensure_memory_vector_index,
    memory_filter_clauses,
    memory_text_definition,
    memory_text_query,
    memory_vector_definition,
    memory_vector_filter,
)
from app.db.text_indexes import EXACT_MULTI, STANDARD_ANALYZER

DIMENSIONS = 384
PROJECT = "sampleco-payment"
CLAUDE = "claude-code-main"


def build_database(
    indexes: list[MongoDocument] | None = None,
    *,
    list_error: Exception | None = None,
    create_error: Exception | None = None,
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
    collection.update_search_index = AsyncMock()
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)
    return database, collection


def typed(database: MagicMock) -> AsyncDatabase[MongoDocument]:
    """Cast a double to the database type the production code expects."""
    return cast(AsyncDatabase[MongoDocument], database)


def existing_index(name: str, definition: MongoDocument) -> MongoDocument:
    """Build a `$listSearchIndexes` document as MongoDB returns it."""
    return {
        "id": "6800000000000000000000dd",
        "name": name,
        "status": "READY",
        "queryable": True,
        "latestDefinition": definition,
    }


def test_the_vector_index_filters_on_scope_and_every_restriction() -> None:
    definition = memory_vector_definition(DIMENSIONS)
    fields = cast(list[dict[str, Any]], definition["fields"])
    vector = next(field for field in fields if field["type"] == "vector")
    filters = {field["path"] for field in fields if field["type"] == "filter"}

    assert vector["numDimensions"] == DIMENSIONS
    assert vector["similarity"] == "cosine"
    # Scope and project are both indexed: the rule needs them together.
    assert {"scope", "project_id"} <= filters
    assert filters == set(MEMORY_FILTER_PATHS)


@pytest.mark.parametrize("dimensions", [0, -1, 4_097, True])
def test_unusable_vector_dimensions_are_rejected(dimensions: int) -> None:
    with pytest.raises(ValueError, match="dimensions"):
        memory_vector_definition(dimensions)


def test_the_text_index_maps_content_summary_and_every_filter_field() -> None:
    definition = memory_text_definition()
    mappings = cast(dict[str, Any], definition["mappings"])
    fields = cast(dict[str, Any], mappings["fields"])

    assert mappings["dynamic"] is False
    assert set(fields) == {
        "org_id",
        "_id",
        "content",
        "summary",
        "scope",
        "project_id",
        "client_id",
        "type",
        "status",
        "tags",
        "importance",
    }
    assert fields["content"]["multi"][EXACT_MULTI]["analyzer"] == "identifier_exact"
    assert fields["summary"]["analyzer"] == STANDARD_ANALYZER
    assert fields["scope"] == {"type": "token"}
    assert "embedding" not in repr(definition)


def test_a_memory_query_searches_content_identifiers_and_the_summary() -> None:
    compound = memory_text_query("strict typing")
    should = cast(list[dict[str, Any]], compound["should"])
    paths = [clause[next(iter(clause))]["path"] for clause in should]

    assert "content" in paths
    assert {"value": "content", "multi": EXACT_MULTI} in paths
    summary = next(clause for clause in should if clause.get("text", {}).get("path") == "summary")
    assert summary["text"]["score"] == {"boost": {"value": SUMMARY_BOOST}}


@pytest.mark.parametrize("query", ["", "   "])
def test_an_empty_memory_query_is_rejected(query: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        memory_text_query(query)


def test_a_memory_scope_must_name_something_searchable() -> None:
    with pytest.raises(ValueError, match="at least one scope"):
        MemoryQueryFilters(scopes=())


def test_searching_project_memory_requires_a_project() -> None:
    with pytest.raises(ValueError, match="requires a project"):
        MemoryQueryFilters(scopes=("project",))


def test_naming_a_project_without_searching_project_memory_is_rejected() -> None:
    with pytest.raises(ValueError, match="project memory was not searched"):
        MemoryQueryFilters(scopes=("global",), project_id=PROJECT)


def test_repeated_scopes_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        MemoryQueryFilters(scopes=("global", "global"))


def test_a_global_query_reaches_only_global_memory() -> None:
    built = memory_vector_filter(MemoryQueryFilters(scopes=("global",), status=None))

    assert built == {"scope": {"$eq": "global"}}


def test_a_project_query_reaches_global_memory_and_its_own() -> None:
    built = memory_vector_filter(
        MemoryQueryFilters(scopes=("global", "project"), project_id=PROJECT, status=None)
    )
    alternatives = cast(list[dict[str, Any]], built["$or"])

    assert {"scope": {"$eq": "global"}} in alternatives
    assert {
        "$and": [{"scope": {"$eq": "project"}}, {"project_id": {"$eq": PROJECT}}]
    } in alternatives
    # No clause can ever match another project's memory.
    assert len(alternatives) == 2


def test_a_project_only_query_excludes_global_memory() -> None:
    built = memory_vector_filter(
        MemoryQueryFilters(scopes=("project",), project_id=PROJECT, status=None)
    )

    assert built == {"$and": [{"scope": {"$eq": "project"}}, {"project_id": {"$eq": PROJECT}}]}


def test_agent_scope_filters_are_bound_to_the_authenticated_client() -> None:
    filters = MemoryQueryFilters(
        scopes=("global", "project", "agent", "project_agent"),
        project_id=PROJECT,
        client_id=CLAUDE,
        status=None,
    )

    vector = memory_vector_filter(filters)
    alternatives = cast(list[dict[str, Any]], vector["$or"])
    assert {
        "$and": [
            {"scope": {"$eq": "agent"}},
            {"client_id": {"$eq": CLAUDE}},
        ]
    } in alternatives
    assert {
        "$and": [
            {"scope": {"$eq": "project_agent"}},
            {"project_id": {"$eq": PROJECT}},
            {"client_id": {"$eq": CLAUDE}},
        ]
    } in alternatives

    lexical = memory_filter_clauses(filters)[0]["compound"]["should"]
    assert {
        "compound": {
            "must": [
                {"equals": {"path": "scope", "value": "agent"}},
                {"equals": {"path": "client_id", "value": CLAUDE}},
            ]
        }
    } in lexical


def test_agent_memory_queries_require_a_client_identity() -> None:
    with pytest.raises(ValueError, match="authenticated client"):
        MemoryQueryFilters(scopes=("agent",))
    with pytest.raises(ValueError, match="client was named"):
        MemoryQueryFilters(scopes=("global",), client_id=CLAUDE)


def test_the_vector_pre_filter_carries_every_requested_restriction() -> None:
    built = memory_vector_filter(
        MemoryQueryFilters(
            scopes=("global",),
            types=("preference", "convention"),
            tags=("python",),
            status="active",
            minimum_importance=0.6,
        )
    )
    clauses = cast(list[dict[str, Any]], built["$and"])

    assert {"type": {"$in": ["preference", "convention"]}} in clauses
    assert {"status": {"$eq": "active"}} in clauses
    assert {"tags": {"$eq": "python"}} in clauses
    assert {"importance": {"$gte": 0.6}} in clauses


def test_the_lexical_scope_filter_covers_both_scopes() -> None:
    clauses = memory_filter_clauses(
        MemoryQueryFilters(scopes=("global", "project"), project_id=PROJECT, status=None)
    )
    scope = clauses[0]["compound"]["should"]

    assert {"equals": {"path": "scope", "value": "global"}} in scope
    project = next(entry for entry in scope if "compound" in entry)
    assert project["compound"]["must"] == [
        {"equals": {"path": "scope", "value": "project"}},
        {"equals": {"path": "project_id", "value": PROJECT}},
    ]


def test_the_lexical_filter_carries_every_requested_restriction() -> None:
    clauses = memory_filter_clauses(
        MemoryQueryFilters(
            scopes=("global",),
            types=("preference",),
            tags=("python",),
            status="archived",
            minimum_importance=0.5,
        )
    )

    assert {"equals": {"path": "status", "value": "archived"}} in clauses
    assert {"equals": {"path": "tags", "value": "python"}} in clauses
    assert {"range": {"path": "importance", "gte": 0.5}} in clauses


@pytest.mark.asyncio
async def test_missing_memory_indexes_are_created() -> None:
    vector_database, vector_collection = build_database([])
    text_database, text_collection = build_database([])

    vector_action = await ensure_memory_vector_index(typed(vector_database), dimensions=DIMENSIONS)
    text_action = await ensure_memory_text_index(typed(text_database))

    assert (vector_action, text_action) == ("created", "created")
    vector_model = vector_collection.create_search_index.await_args.args[0]
    text_model = text_collection.create_search_index.await_args.args[0]
    assert isinstance(vector_model, SearchIndexModel)
    assert vector_model.document["name"] == MEMORY_VECTOR_INDEX
    assert text_model.document["name"] == MEMORY_TEXT_INDEX
    assert vector_database.__getitem__.call_args.args[0] == "memories"


@pytest.mark.asyncio
async def test_identical_memory_indexes_are_left_untouched() -> None:
    vector_database, vector_collection = build_database(
        [existing_index(MEMORY_VECTOR_INDEX, memory_vector_definition(DIMENSIONS))]
    )
    text_database, text_collection = build_database(
        [existing_index(MEMORY_TEXT_INDEX, memory_text_definition())]
    )

    vector_action = await ensure_memory_vector_index(typed(vector_database), dimensions=DIMENSIONS)
    text_action = await ensure_memory_text_index(typed(text_database))

    assert (vector_action, text_action) == ("unchanged", "unchanged")
    vector_collection.update_search_index.assert_not_awaited()
    text_collection.update_search_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_changed_dimension_count_updates_the_memory_vector_index() -> None:
    database, collection = build_database(
        [existing_index(MEMORY_VECTOR_INDEX, memory_vector_definition(8))]
    )

    assert await ensure_memory_vector_index(typed(database), dimensions=DIMENSIONS) == "updated"
    collection.update_search_index.assert_awaited_once_with(
        MEMORY_VECTOR_INDEX,
        memory_vector_definition(DIMENSIONS),
    )


@pytest.mark.asyncio
async def test_a_concurrent_memory_index_creation_race_is_not_an_error() -> None:
    database, _ = build_database([], create_error=OperationFailure("exists", 68))

    assert await ensure_memory_text_index(typed(database)) == "unchanged"


@pytest.mark.asyncio
async def test_unavailable_search_infrastructure_raises_a_safe_error() -> None:
    database, _ = build_database([], list_error=PyMongoError("mongot is unreachable"))

    with pytest.raises(SearchUnavailableError):
        await ensure_memory_text_index(typed(database))


@pytest.mark.asyncio
async def test_the_memory_index_states_are_described_for_operators() -> None:
    vector_database, _ = build_database(
        [existing_index(MEMORY_VECTOR_INDEX, memory_vector_definition(DIMENSIONS))]
    )
    text_database, _ = build_database([existing_index(MEMORY_TEXT_INDEX, memory_text_definition())])

    vector = await describe_memory_vector_index(typed(vector_database))
    text = await describe_memory_text_index(typed(text_database))

    assert (vector.ready, vector.dimensions, vector.similarity) == (True, DIMENSIONS, "cosine")
    assert (text.ready, text.analyzer) == (True, STANDARD_ANALYZER)


@pytest.mark.asyncio
async def test_memory_health_never_raises_when_search_is_unavailable() -> None:
    database, _ = build_database([], list_error=PyMongoError("coordinator is down"))

    vector = await check_memory_vector_health(typed(database), DIMENSIONS)
    text = await check_memory_text_health(typed(database))

    assert (vector.available, vector.ready) == (False, False)
    assert (text.available, text.ready) == (False, False)
