"""Asynchronous PyMongo client construction and lifecycle."""

from collections.abc import Callable
from datetime import UTC

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import Settings
from app.db.client_types import MongoDocument
from app.db.health import check_mongodb_health
from app.observability.mongo import CommandMetricsListener

ClientFactory = Callable[[Settings], AsyncMongoClient[MongoDocument]]


class MongoDBNotReadyError(RuntimeError):
    """Raised when MongoDB fails a required startup check."""


def create_mongodb_client(settings: Settings) -> AsyncMongoClient[MongoDocument]:
    """Create a lazy async client authenticated as the application user.

    The command listener is how MongoDB latency is measured. Attaching it here
    covers every command the driver sends — including the ones inside it — and
    means no repository has to remember to time a query. A deployment that
    publishes no metrics attaches none, so it pays nothing for them.
    """
    listeners = [CommandMetricsListener()] if settings.metrics_enabled else []
    client: AsyncMongoClient[MongoDocument] = AsyncMongoClient(
        settings.mongodb_uri,
        username=settings.mongodb_username,
        password=settings.mongodb_password_value(),
        authSource=settings.mongodb_database,
        replicaSet=settings.mongodb_replica_set,
        appname=settings.mongodb_app_name,
        serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms,
        connectTimeoutMS=settings.mongodb_connect_timeout_ms,
        # Connecting was already bounded; what happened afterwards was not.
        # A command the server holds open has no natural end, so without this
        # a single degraded dependency turns every request behind it into a
        # request that never returns.
        timeoutMS=settings.mongodb_timeout_ms,
        maxPoolSize=settings.mongodb_max_pool_size,
        uuidRepresentation="standard",
        tz_aware=True,
        tzinfo=UTC,
        event_listeners=listeners,
    )
    return client


class MongoDBClient:
    """Own the application's MongoDB client and database handles."""

    def __init__(
        self,
        settings: Settings,
        client_factory: ClientFactory = create_mongodb_client,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._client: AsyncMongoClient[MongoDocument] | None = None
        self._database: AsyncDatabase[MongoDocument] | None = None

    @property
    def client(self) -> AsyncMongoClient[MongoDocument]:
        """Return the initialized client or reject use before startup."""
        if self._client is None:
            raise RuntimeError("MongoDB client has not been initialized")
        return self._client

    @property
    def database(self) -> AsyncDatabase[MongoDocument]:
        """Return the configured database or reject use before startup."""
        if self._database is None:
            raise RuntimeError("MongoDB client has not been initialized")
        return self._database

    async def connect(self, *, require_search: bool = True) -> None:
        """Initialize the client and require database, replica-set, and mongot readiness.

        `require_search=False` drops the mongot condition, for the operations
        that do not use search. Issuing an API key is the case that matters:
        requiring the search coordinator to mint a credential would mean an
        operator could not bootstrap authentication on a platform whose search
        is degraded — which is when they are most likely to need to.
        """
        if self._client is not None:
            return

        client = self._client_factory(self._settings)
        database = client[self._settings.mongodb_database]
        health = await check_mongodb_health(
            client,
            database,
            self._settings.mongodb_replica_set,
        )
        if not (health.ready if require_search else health.mongodb_ready):
            await client.close()
            raise MongoDBNotReadyError("MongoDB failed application startup readiness checks")

        self._client = client
        self._database = database

    async def close(self) -> None:
        """Close sockets and clear handles during application shutdown."""
        client = self._client
        self._client = None
        self._database = None
        if client is not None:
            await client.close()
