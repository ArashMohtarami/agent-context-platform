"""Real MongoDB and mongot infrastructure integration checks."""

import os

import pytest

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.health import check_mongodb_health

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]


@pytest.mark.asyncio
async def test_authenticated_application_database_and_replica_set() -> None:
    settings = get_settings()
    mongodb = MongoDBClient(settings)
    await mongodb.connect()
    try:
        health = await check_mongodb_health(
            mongodb.client,
            mongodb.database,
            settings.mongodb_replica_set,
        )
        assert health.database_available is True
        assert health.replica_set_ready is True
        assert health.mongot_available is True

        probe = mongodb.database["_phase1_integration_probe"]
        result = await probe.insert_one({"phase": 1})
        assert result.acknowledged is True
        assert await probe.count_documents({"_id": result.inserted_id}) == 1
        await probe.drop()
    finally:
        await mongodb.close()
