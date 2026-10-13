"""Async MongoDB client lifecycle tests."""

from datetime import UTC
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.asynchronous.mongo_client import AsyncMongoClient

from app.core.config import Settings
from app.db.client import MongoDBClient, MongoDBNotReadyError, create_mongodb_client
from app.db.client_types import MongoDocument
from app.observability.mongo import CommandMetricsListener


def test_client_initialization_uses_application_credentials(settings: Settings) -> None:
    with patch("app.db.client.AsyncMongoClient") as constructor:
        created = create_mongodb_client(settings)

    assert created is constructor.return_value
    listeners = constructor.call_args.kwargs.pop("event_listeners")
    constructor.assert_called_once_with(
        settings.mongodb_uri,
        username=settings.mongodb_username,
        password="test-only-password",
        authSource=settings.mongodb_database,
        replicaSet="rs0",
        appname="agent-context-platform-tests",
        serverSelectionTimeoutMS=5_000,
        connectTimeoutMS=5_000,
        timeoutMS=15_000,
        maxPoolSize=50,
        uuidRepresentation="standard",
        tz_aware=True,
        tzinfo=UTC,
    )
    assert [type(listener) for listener in listeners] == [CommandMetricsListener]


def test_a_deployment_that_publishes_no_metrics_attaches_no_listener(settings: Settings) -> None:
    # The listener is the only part of observability with a cost on the
    # database's own path, so switching metrics off has to actually remove it
    # rather than leave it running into a registry nobody reads.
    quiet = settings.model_copy(update={"metrics_enabled": False})

    with patch("app.db.client.AsyncMongoClient") as constructor:
        create_mongodb_client(quiet)

    assert constructor.call_args.kwargs["event_listeners"] == []


def make_client_handles() -> tuple[
    AsyncMongoClient[MongoDocument], AsyncDatabase[MongoDocument], AsyncMock
]:
    """Build mock handles with async commands and shutdown."""
    database = MagicMock()
    database.command = AsyncMock(return_value={"ok": 1})
    client = MagicMock()
    client.__getitem__.return_value = database
    client.admin.command = AsyncMock(return_value={"ok": 1, "set": "rs0", "myState": 1})
    close = AsyncMock()
    client.close = close
    return (
        cast(AsyncMongoClient[MongoDocument], client),
        cast(AsyncDatabase[MongoDocument], database),
        close,
    )


@pytest.mark.asyncio
async def test_client_connects_once_and_closes(settings: Settings) -> None:
    client, database, close = make_client_handles()
    factory = MagicMock(return_value=client)
    manager = MongoDBClient(settings, client_factory=factory)

    await manager.connect()
    await manager.connect()

    assert manager.client is client
    assert manager.database is database
    factory.assert_called_once_with(settings)

    await manager.close()
    close.assert_awaited_once_with()
    with pytest.raises(RuntimeError, match="not been initialized"):
        _ = manager.database


@pytest.mark.asyncio
async def test_client_closes_failed_connection(settings: Settings) -> None:
    client, database, close = make_client_handles()
    database.command = AsyncMock(return_value={"ok": 0})  # type: ignore[method-assign]
    manager = MongoDBClient(settings, client_factory=MagicMock(return_value=client))

    with pytest.raises(MongoDBNotReadyError):
        await manager.connect()

    close.assert_awaited_once_with()
    with pytest.raises(RuntimeError, match="not been initialized"):
        _ = manager.client
