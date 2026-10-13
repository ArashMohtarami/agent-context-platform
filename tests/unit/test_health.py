"""MongoDB health behavior tests."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.asynchronous.mongo_client import AsyncMongoClient
from pymongo.errors import ConnectionFailure, OperationFailure

from app.db.client_types import MongoDocument
from app.db.health import (
    MONGOT_PROBE_COLLECTION,
    MONGOT_PROBE_TIMEOUT_SECONDS,
    check_mongodb_health,
    database_is_available,
    mongot_is_available,
    replica_set_is_ready,
)


def mock_database(response: object) -> AsyncDatabase[MongoDocument]:
    database = MagicMock()
    if isinstance(response, Exception):
        database.command = AsyncMock(side_effect=response)
    else:
        database.command = AsyncMock(return_value=response)
    return cast(AsyncDatabase[MongoDocument], database)


def mock_client(response: object) -> AsyncMongoClient[MongoDocument]:
    client = MagicMock()
    if isinstance(response, Exception):
        client.admin.command = AsyncMock(side_effect=response)
    else:
        client.admin.command = AsyncMock(return_value=response)
    return cast(AsyncMongoClient[MongoDocument], client)


@pytest.mark.asyncio
async def test_database_availability_requires_successful_ping() -> None:
    assert await database_is_available(mock_database({"ok": 1})) is True
    assert await database_is_available(mock_database({"ok": 0})) is False
    assert await database_is_available(mock_database(ConnectionFailure())) is False


@pytest.mark.asyncio
async def test_replica_set_readiness_requires_expected_primary() -> None:
    assert await replica_set_is_ready(mock_client({"ok": 1, "set": "rs0", "myState": 1}), "rs0")
    assert not await replica_set_is_ready(
        mock_client({"ok": 1, "set": "other", "myState": 1}), "rs0"
    )
    assert not await replica_set_is_ready(mock_client({"ok": 1, "set": "rs0", "myState": 2}), "rs0")


@pytest.mark.asyncio
async def test_mongot_availability_uses_mongodb_coordinator_command() -> None:
    database = mock_database({"ok": 1})

    assert await mongot_is_available(database) is True
    database.command.assert_awaited_once_with(  # type: ignore[attr-defined]
        {
            "aggregate": MONGOT_PROBE_COLLECTION,
            "pipeline": [{"$listSearchIndexes": {}}],
            "cursor": {},
        }
    )
    assert await mongot_is_available(mock_database(OperationFailure("mongot unavailable"))) is False


@pytest.mark.asyncio
async def test_the_coordinator_probe_names_a_collection_that_is_made_to_exist() -> None:
    # The bug this replaced, measured against a stopped coordinator: listing
    # the search indexes of a collection MongoDB does not have is answered by
    # `mongod` alone, `ok: 1`, in ten milliseconds. The probe reported a
    # healthy search coordinator while `mongot` was stopped, and the platform
    # started up on the strength of it. Against a collection that exists the
    # same listing has to make the round trip and fails when it cannot.
    from app.db.collections import ensure_collections

    database = MagicMock()
    database.list_collection_names = AsyncMock(return_value=[])
    database.create_collection = AsyncMock()

    await ensure_collections(database)

    created = [call.args[0] for call in database.create_collection.await_args_list]
    assert MONGOT_PROBE_COLLECTION in created


@pytest.mark.asyncio
async def test_the_coordinator_probe_is_bounded_more_tightly_than_a_query() -> None:
    # It runs on the readiness path. Inheriting the client's operation timeout
    # made a health check take thirty seconds to say "no", which is thirty
    # seconds during which nothing watching it learns anything.
    from app.core.config import Settings

    assert MONGOT_PROBE_TIMEOUT_SECONDS <= 5.0
    assert (
        MONGOT_PROBE_TIMEOUT_SECONDS * 1_000 < Settings.model_fields["mongodb_timeout_ms"].default
    )


@pytest.mark.asyncio
async def test_health_does_not_claim_replica_ready_when_database_is_down() -> None:
    client = mock_client({"ok": 1, "set": "rs0", "myState": 1})
    health = await check_mongodb_health(
        client,
        mock_database(ConnectionFailure()),
        "rs0",
    )

    assert health.ready is False
    assert health.database_available is False
    assert health.replica_set_ready is False
    assert health.mongot_available is False
    client.admin.command.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_health_requires_mongot_after_database_and_replica_set() -> None:
    database = mock_database({"ok": 1})
    database.command = AsyncMock(  # type: ignore[method-assign]
        side_effect=[{"ok": 1}, OperationFailure("mongot unavailable")]
    )

    health = await check_mongodb_health(
        mock_client({"ok": 1, "set": "rs0", "myState": 1}),
        database,
        "rs0",
    )

    assert health.database_available is True
    assert health.replica_set_ready is True
    assert health.mongot_available is False
    assert health.mongodb_ready is True
    assert health.ready is False
