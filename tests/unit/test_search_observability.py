"""What every index query and every whole retrieval reports about itself."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.db.client_types import MongoDocument
from app.db.errors import SearchUnavailableError
from app.models.skill_chunk import SkillChunkDocument
from app.observability import context, instruments
from app.observability.instruments import REGISTRY
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from tests.unit.test_search_service import PROJECT, build_search, context_match, memory_match

DIMENSIONS = 4
COLLECTION = "skill_chunks"


@pytest.fixture(autouse=True)
def empty_registry() -> Iterator[None]:
    """Record into a registry no other test has already written to."""
    REGISTRY.reset()
    yield
    REGISTRY.reset()


def chunk_document() -> MongoDocument:
    """Build a stored chunk exactly as MongoDB returns it."""
    document = SkillChunkDocument(
        skill_id="security-baseline",
        skill_version="1.0.0",
        chunk_id="chunk-0000",
        content="Keycloak issues OAuth2 tokens.",
        section="Rules",
        chunk_index=0,
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
) -> MongoSkillChunkRepository:
    """Build a chunk repository over a MongoDB double."""
    collection = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=list(results or []))
    collection.aggregate = AsyncMock(return_value=cursor, side_effect=error)
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)
    return MongoSkillChunkRepository(cast(AsyncDatabase[MongoDocument], database))


def failures(method: str) -> float | None:
    """Return how many of one collection's queries of one kind failed."""
    return REGISTRY.value(instruments.SEARCH_FAILURES, method=method, collection=COLLECTION)


def timings(method: str) -> tuple[int, float, tuple[float, ...]] | None:
    """Return the recorded durations of one collection's queries of one kind."""
    return REGISTRY.summary(instruments.SEARCH_DURATION, method=method, collection=COLLECTION)


@pytest.mark.asyncio
async def test_a_vector_query_is_timed_against_its_own_method_and_collection() -> None:
    repository = build_repository([{**chunk_document(), "score": 0.9}])

    await repository.vector_search([0.1] * DIMENSIONS, limit=5, num_candidates=50)

    measured = timings("vector")
    assert measured is not None and measured[0] == 1
    assert timings("lexical") is None


@pytest.mark.asyncio
async def test_a_lexical_query_is_timed_against_its_own_method_and_collection() -> None:
    repository = build_repository([{**chunk_document(), "score": 4.2}])

    await repository.text_search("Keycloak", limit=5)

    measured = timings("lexical")
    assert measured is not None and measured[0] == 1
    assert timings("vector") is None


@pytest.mark.asyncio
async def test_a_failing_vector_query_is_counted_as_a_vector_failure() -> None:
    # The whole point of splitting the counter: a rebuilt vector index takes
    # semantic search down while lexical search keeps answering, and an
    # operator needs to see which half is broken.
    repository = build_repository(error=PyMongoError("index is not queryable"))

    with pytest.raises(SearchUnavailableError):
        await repository.vector_search([0.1] * DIMENSIONS, limit=5, num_candidates=50)

    assert failures("vector") == 1
    assert failures("lexical") is None


@pytest.mark.asyncio
async def test_a_failing_lexical_query_is_counted_as_a_lexical_failure() -> None:
    repository = build_repository(error=PyMongoError("index is not queryable"))

    with pytest.raises(SearchUnavailableError):
        await repository.text_search("Keycloak", limit=5)

    assert failures("lexical") == 1
    assert failures("vector") is None


@pytest.mark.asyncio
async def test_a_failing_query_is_timed_as_well_as_counted() -> None:
    repository = build_repository(error=PyMongoError("index is not queryable"))

    with pytest.raises(SearchUnavailableError):
        await repository.text_search("Keycloak", limit=5)

    measured = timings("lexical")
    assert measured is not None and measured[0] == 1


@pytest.mark.asyncio
async def test_a_query_refused_before_it_reaches_mongodb_is_not_a_search_failure() -> None:
    # A rejected argument is the caller's mistake. Counting it as a search
    # failure would make a malformed request look like a broken index.
    repository = build_repository()

    with pytest.raises(ValueError):
        await repository.text_search("Keycloak", limit=0)

    assert failures("lexical") is None
    assert timings("lexical") is None


@pytest.mark.asyncio
async def test_an_unusable_score_is_counted_as_a_failed_search() -> None:
    # The pipeline did not run as written, which is a search that did not work
    # whatever the driver reported.
    repository = build_repository([{**chunk_document(), "score": "high"}])

    with pytest.raises(SearchUnavailableError):
        await repository.text_search("Keycloak", limit=5)

    assert failures("lexical") == 1


# --- the whole retrieval ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_whole_retrieval_records_its_latency_and_its_result_count() -> None:
    # End to end, including the ranking and budgeting no single index accounts
    # for, which is what a caller actually waited for.
    service, _, _, _ = build_search(memory_vector=[memory_match(0.8)])

    result = await service.search_knowledge("typing conventions")

    duration = REGISTRY.summary(instruments.RETRIEVAL_DURATION, operation="search_knowledge")
    results = REGISTRY.summary(instruments.RETRIEVAL_RESULTS, operation="search_knowledge")
    assert duration is not None and duration[0] == 1
    assert results is not None and results[2] == (float(len(result.result_keys)),)


@pytest.mark.asyncio
async def test_a_retrieval_tells_the_call_it_is_part_of_what_it_did() -> None:
    # So one HTTP access line carries the query identifier and the result count
    # of the search it ran, rather than an operator having to join two lines.
    service, _, _, _ = build_search(context_vector=[context_match(0.9)])

    with context.correlated() as call:
        result = await service.search_knowledge("payment idempotency", project_id=PROJECT)

    assert call.operation == "search_knowledge"
    assert call.project_id == PROJECT
    assert call.query_id == result.trace.query_id
    assert call.result_count == len(result.result_keys)


@pytest.mark.asyncio
async def test_a_retrieval_outside_a_correlated_call_still_records_its_metrics() -> None:
    # `contextctl` and a test are both callers with no request around them.
    service, _, _, _ = build_search()

    await service.search_knowledge("typing conventions")

    assert context.current() is None
    assert (
        REGISTRY.summary(instruments.RETRIEVAL_DURATION, operation="search_knowledge") is not None
    )


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
