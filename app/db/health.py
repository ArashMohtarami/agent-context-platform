"""MongoDB availability and replica-set readiness checks."""

from dataclasses import dataclass
from typing import Final

import pymongo
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.asynchronous.mongo_client import AsyncMongoClient
from pymongo.errors import PyMongoError

from app.db.client_types import MongoDocument

# The collection the coordinator probe lists search indexes on. It has to
# exist, and that is the whole point: `$listSearchIndexes` against a collection
# MongoDB does not have is answered by `mongod` alone, with an empty cursor and
# `ok: 1`, without ever consulting `mongot`. A probe like that reports a
# healthy search coordinator while the coordinator is stopped — which is
# exactly what this one did until it was measured. `ensure_collections`
# materialises it so the listing has to make the round trip.
MONGOT_PROBE_COLLECTION: Final = "_mongot_health_probe"
# What the coordinator gets to answer a metadata listing in. Short on purpose:
# this runs on the readiness path, and a coordinator that cannot list indexes
# in a couple of seconds is not one that will serve a query. Without its own
# bound the probe inherits the client's operation timeout and a health check
# takes half a minute to say "no".
MONGOT_PROBE_TIMEOUT_SECONDS: Final = 2.0


@dataclass(frozen=True, slots=True)
class MongoDBHealth:
    """Safe, credential-free database and search-coordinator health details."""

    database_available: bool
    replica_set_ready: bool
    mongot_available: bool

    @property
    def mongodb_ready(self) -> bool:
        """Return whether the application database and replica set are ready."""
        return self.database_available and self.replica_set_ready

    @property
    def ready(self) -> bool:
        """Return whether all MongoDB and mongot readiness conditions pass."""
        return self.mongodb_ready and self.mongot_available


async def database_is_available(database: AsyncDatabase[MongoDocument]) -> bool:
    """Check that the configured application database accepts commands."""
    try:
        response = await database.command({"ping": 1})
    except PyMongoError:
        return False
    return response.get("ok") == 1


async def replica_set_is_ready(client: AsyncMongoClient[MongoDocument], expected_name: str) -> bool:
    """Check replica-set identity and require this single member to be primary."""
    try:
        status = await client.admin.command({"replSetGetStatus": 1})
    except PyMongoError:
        return False

    return (
        status.get("ok") == 1 and status.get("set") == expected_name and status.get("myState") == 1
    )


async def mongot_is_available(
    database: AsyncDatabase[MongoDocument],
    *,
    timeout_seconds: float = MONGOT_PROBE_TIMEOUT_SECONDS,
) -> bool:
    """Exercise the mongod-to-mongot coordinator path without creating an index.

    Listing the search indexes of an existing collection is the cheapest thing
    that cannot be answered without the coordinator: no index is created, no
    document is read, and a stopped `mongot` makes it fail rather than return
    nothing. It is given its own short timeout, because the answer this is
    asked for is needed now.
    """
    try:
        with pymongo.timeout(timeout_seconds):
            response = await database.command(
                {
                    "aggregate": MONGOT_PROBE_COLLECTION,
                    "pipeline": [{"$listSearchIndexes": {}}],
                    "cursor": {},
                }
            )
    except PyMongoError:
        return False
    return response.get("ok") == 1


async def check_mongodb_health(
    client: AsyncMongoClient[MongoDocument],
    database: AsyncDatabase[MongoDocument],
    expected_replica_set: str,
) -> MongoDBHealth:
    """Evaluate every MongoDB and indirect mongot application readiness condition."""
    database_available = await database_is_available(database)
    replica_set_ready = False
    mongot_available = False
    if database_available:
        replica_set_ready = await replica_set_is_ready(client, expected_replica_set)
    if replica_set_ready:
        mongot_available = await mongot_is_available(database)
    return MongoDBHealth(
        database_available=database_available,
        replica_set_ready=replica_set_ready,
        mongot_available=mongot_available,
    )
