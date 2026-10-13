"""Search index definition, query shape, reconciliation, and health tests."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.operations import SearchIndexModel

from app.db.client_types import MongoDocument
from app.db.errors import SearchUnavailableError
from app.db.text_indexes import (
    CONTENT_PATH,
    EXACT_MULTI,
    IDENTIFIER_ANALYZER,
    IDENTIFIER_BOOST,
    PHRASE_BOOST,
    SKILL_CHUNK_TEXT_INDEX,
    STANDARD_ANALYZER,
    check_text_search_health,
    describe_text_index,
    ensure_text_indexes,
    skill_chunk_text_definition,
    skill_chunk_text_query,
    version_filter_clauses,
)
from app.skills.types import SkillVersionKey


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
    definition: MongoDocument | None = None,
    *,
    queryable: bool = True,
    status: str = "READY",
) -> MongoDocument:
    """Build a `$listSearchIndexes` document as MongoDB returns it."""
    return {
        "id": "6800000000000000000000bb",
        "name": SKILL_CHUNK_TEXT_INDEX,
        "type": "search",
        "status": status,
        "queryable": queryable,
        "latestDefinition": definition or skill_chunk_text_definition(),
    }


def mapped_fields(definition: MongoDocument) -> dict[str, Any]:
    """Return the field mappings of a definition."""
    mappings = cast(dict[str, Any], definition["mappings"])
    return cast(dict[str, Any], mappings["fields"])


def test_content_is_indexed_for_prose_and_for_exact_identifiers() -> None:
    definition = skill_chunk_text_definition()
    content = mapped_fields(definition)[CONTENT_PATH]

    assert content["type"] == "string"
    assert content["analyzer"] == STANDARD_ANALYZER
    assert content["multi"][EXACT_MULTI] == {
        "type": "string",
        "analyzer": IDENTIFIER_ANALYZER,
    }


def test_the_identifier_analyzer_keeps_punctuated_tokens_whole() -> None:
    analyzers = cast(list[dict[str, Any]], skill_chunk_text_definition()["analyzers"])

    assert len(analyzers) == 1
    assert analyzers[0]["name"] == IDENTIFIER_ANALYZER
    # Whitespace tokenization is what keeps wallet:read and /api/v1/payments whole.
    assert analyzers[0]["tokenizer"] == {"type": "whitespace"}
    assert analyzers[0]["tokenFilters"] == [{"type": "lowercase"}]


def test_only_queried_and_filtered_fields_are_indexed() -> None:
    definition = skill_chunk_text_definition()
    mappings = cast(dict[str, Any], definition["mappings"])

    assert mappings["dynamic"] is False
    assert set(mapped_fields(definition)) == {"content", "section", "skill_id", "skill_version"}
    assert mapped_fields(definition)["skill_id"] == {"type": "token"}
    assert mapped_fields(definition)["skill_version"] == {"type": "token"}
    # Vectors and internal fields must never reach the text index.
    assert "embedding" not in repr(definition)


def test_a_query_searches_prose_identifiers_and_phrases() -> None:
    compound = skill_chunk_text_query("wallet:read scope")
    should = cast(list[dict[str, Any]], compound["should"])
    operators = [next(iter(clause)) for clause in should]

    assert compound["minimumShouldMatch"] == 1
    assert "must" not in compound
    assert operators.count("text") == 3
    assert operators.count("phrase") == 1
    exact = next(
        clause["text"]
        for clause in should
        if clause.get("text", {}).get("path") == {"value": CONTENT_PATH, "multi": EXACT_MULTI}
    )
    assert exact["query"] == "wallet:read scope"
    assert exact["score"] == {"boost": {"value": IDENTIFIER_BOOST}}


def test_a_query_reaches_the_index_with_identifiers_intact() -> None:
    query = "POST /api/v1/payments returns INSUFFICIENT_SCOPE"
    compound = skill_chunk_text_query(f"  {query}  ")
    should = cast(list[dict[str, Any]], compound["should"])

    assert all(clause[next(iter(clause))]["query"] == query for clause in should), (
        "whitespace normalization must not rewrite the query"
    )


def test_phrase_search_requires_the_words_together() -> None:
    compound = skill_chunk_text_query("payment idempotency", phrase=True)
    must = cast(list[dict[str, Any]], compound["must"])

    assert len(must) == 1
    assert must[0]["phrase"]["query"] == "payment idempotency"
    assert must[0]["phrase"]["path"] == CONTENT_PATH
    assert must[0]["phrase"]["score"] == {"boost": {"value": PHRASE_BOOST}}
    assert compound["minimumShouldMatch"] == 1


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_an_empty_query_is_rejected(query: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        skill_chunk_text_query(query)


def test_no_versions_produce_no_filter() -> None:
    assert version_filter_clauses([]) == []


def test_one_version_filters_on_an_exact_pair() -> None:
    clauses = version_filter_clauses([SkillVersionKey("fastapi-production", "1.0.0")])

    assert clauses == [
        {
            "compound": {
                "must": [
                    {"equals": {"path": "skill_id", "value": "fastapi-production"}},
                    {"equals": {"path": "skill_version", "value": "1.0.0"}},
                ]
            }
        }
    ]


def test_several_versions_filter_as_alternatives_of_exact_pairs() -> None:
    clauses = version_filter_clauses(
        [
            SkillVersionKey("fastapi-production", "1.0.0"),
            SkillVersionKey("strict-python", "2.0.0"),
        ]
    )

    assert len(clauses) == 1
    alternatives = clauses[0]["compound"]["should"]
    assert len(alternatives) == 2
    assert alternatives[1]["compound"]["must"][0]["equals"]["value"] == "strict-python"
    assert clauses[0]["compound"]["minimumShouldMatch"] == 1


@pytest.mark.asyncio
async def test_a_missing_index_is_created_as_a_search_index() -> None:
    database, collection = build_database([])

    action = await ensure_text_indexes(typed(database))

    assert action == "created"
    model = collection.create_search_index.await_args.args[0]
    assert isinstance(model, SearchIndexModel)
    assert model.document["name"] == SKILL_CHUNK_TEXT_INDEX
    assert model.document["type"] == "search"
    assert model.document["definition"] == skill_chunk_text_definition()


@pytest.mark.asyncio
async def test_an_identical_index_is_left_untouched() -> None:
    database, collection = build_database([existing_index()])

    actions = [
        await ensure_text_indexes(typed(database)),
        await ensure_text_indexes(typed(database)),
    ]

    assert actions == ["unchanged", "unchanged"]
    collection.create_search_index.assert_not_awaited()
    collection.update_search_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_extra_keys_returned_by_mongodb_do_not_force_an_update() -> None:
    stored = skill_chunk_text_definition()
    fields = mapped_fields(stored)
    fields[CONTENT_PATH]["store"] = True
    fields[CONTENT_PATH]["norms"] = "include"
    stored["storedSource"] = False
    database, collection = build_database([existing_index(stored)])

    assert await ensure_text_indexes(typed(database)) == "unchanged"
    collection.update_search_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_changed_analyzer_or_mapping_is_updated_in_place() -> None:
    stale = skill_chunk_text_definition()
    mapped_fields(stale)[CONTENT_PATH]["analyzer"] = "lucene.keyword"
    database, collection = build_database([existing_index(stale)])

    action = await ensure_text_indexes(typed(database))

    assert action == "updated"
    collection.update_search_index.assert_awaited_once_with(
        SKILL_CHUNK_TEXT_INDEX,
        skill_chunk_text_definition(),
    )


@pytest.mark.asyncio
async def test_a_removed_field_mapping_is_restored() -> None:
    stale = skill_chunk_text_definition()
    del mapped_fields(stale)["skill_version"]
    database, collection = build_database([existing_index(stale)])

    assert await ensure_text_indexes(typed(database)) == "updated"
    collection.update_search_index.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_concurrent_creation_race_is_not_an_error() -> None:
    database, _ = build_database([], create_error=OperationFailure("exists", 68))

    assert await ensure_text_indexes(typed(database)) == "unchanged"


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

    with pytest.raises(SearchUnavailableError):
        await ensure_text_indexes(typed(database))


@pytest.mark.asyncio
async def test_a_failing_update_raises_a_safe_error() -> None:
    stale = skill_chunk_text_definition()
    mapped_fields(stale)[CONTENT_PATH]["analyzer"] = "lucene.keyword"
    database, _ = build_database(
        [existing_index(stale)],
        update_error=PyMongoError("index rebuild refused"),
    )

    with pytest.raises(SearchUnavailableError, match="could not be updated"):
        await ensure_text_indexes(typed(database))


@pytest.mark.asyncio
async def test_the_index_state_is_described_for_operators() -> None:
    database, _ = build_database([existing_index()])

    status = await describe_text_index(typed(database))

    assert (status.exists, status.queryable, status.ready) == (True, True, True)
    assert status.status == "READY"
    assert status.analyzer == STANDARD_ANALYZER


@pytest.mark.asyncio
async def test_a_building_index_is_not_yet_queryable() -> None:
    database, _ = build_database([existing_index(queryable=False, status="BUILDING")])

    status = await describe_text_index(typed(database))

    assert status.ready is False
    assert status.status == "BUILDING"


@pytest.mark.asyncio
async def test_health_reports_a_ready_index() -> None:
    database, _ = build_database([existing_index()])

    health = await check_text_search_health(typed(database))

    assert (health.available, health.ready) == (True, True)
    assert health.detail is None


@pytest.mark.asyncio
async def test_health_never_raises_when_search_is_unavailable() -> None:
    database, _ = build_database([], list_error=PyMongoError("coordinator is down"))

    health = await check_text_search_health(typed(database))

    assert (health.available, health.ready) == (False, False)
    assert health.detail == "SearchUnavailableError"
    assert health.index is None
