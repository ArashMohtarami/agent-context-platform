"""Real-MongoDB checks for V2 Phase 0 collection and index initialization."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from bson import ObjectId
from pymongo.errors import WriteError
from v6_legacy_integration import initialize_database, seed_projects

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.migrations.v006_creator_content_schema import V2_CONTENT_COLLECTIONS
from app.db.tenant_schema import TENANT_INDEX_DEFINITIONS as INDEX_DEFINITIONS
from app.models.content import (
    ContentIdeaDocument,
    ContentItemDocument,
    ContentMetricDocument,
    CreatorProfileDocument,
)
from app.repositories.content_repository import (
    MongoContentIdeaRepository,
    MongoContentItemRepository,
    MongoContentMetricRepository,
    MongoCreatorProfileRepository,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]


@asynccontextmanager
async def initialized_mongodb() -> AsyncIterator[MongoDBClient]:
    """Yield an initialized MongoDB client and leave no V2 data behind."""
    mongodb = MongoDBClient(get_settings())
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        yield mongodb
    finally:
        await mongodb.close()


@pytest.mark.asyncio
async def test_v2_schema_migration_is_idempotent_and_enforces_lifecycle_values() -> None:
    """Create V2 persistence once, reconcile it twice, and test strict validation."""
    profile_id = f"phase0-profile-{ObjectId()}"
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            await seed_projects(database, ["payment-service"])
            await initialize_database(database)

            names = set(await database.list_collection_names())
            assert set(V2_CONTENT_COLLECTIONS).issubset(names)
            for collection_name in V2_CONTENT_COLLECTIONS:
                index_information = await database[collection_name].index_information()
                expected_names = {
                    definition.document["name"] for definition in INDEX_DEFINITIONS[collection_name]
                }
                assert expected_names.issubset(index_information)

            assert await database["_schema_migrations"].count_documents({"version": 6}) == 1
            assert await database["_schema_migrations"].count_documents({"version": 7}) == 1
            assert await database["_schema_migrations"].count_documents({"version": 8}) == 1
            assert await database["_schema_migrations"].count_documents({"version": 9}) == 1
            assert await database["_schema_migrations"].count_documents({"version": 10}) == 1
            assert await database["_schema_migrations"].count_documents({"version": 11}) == 1
            assert await database["_schema_migrations"].count_documents({"version": 12}) == 1

            profiles = MongoCreatorProfileRepository(database)
            profile = await profiles.create(
                CreatorProfileDocument(profile_id=profile_id, name="V2 Schema Test")
            )
            assert await profiles.get(profile_id) == profile

            ideas = MongoContentIdeaRepository(database, profile_id)
            idea = await ideas.create(
                ContentIdeaDocument(
                    profile_id=profile_id,
                    idea_id="idempotency",
                    title="Idempotency",
                    description="A stored V2 idea.",
                    platforms=["linkedin"],
                    origin={"type": "project_context", "project_id": "payment-service"},
                    priority=0.8,
                )
            )
            assert (await ideas.list()).items == (idea,)

            stored_item = await MongoContentItemRepository(database, profile_id).create(
                ContentItemDocument(
                    profile_id=profile_id,
                    content_id="schema-content",
                    platform="linkedin",
                    content_type="post",
                    title="Fictional schema content",
                    body="A retained fixture for manual descriptive metrics.",
                )
            )
            metrics = MongoContentMetricRepository(database, profile_id)
            metric = await metrics.create(
                ContentMetricDocument(
                    profile_id=profile_id,
                    metric_id="linkedin-capture",
                    content_id="schema-content",
                    platform="linkedin",
                    captured_at=datetime(2026, 9, 20, tzinfo=UTC),
                    metrics={"impressions": 100, "likes": 5},
                    source="manual",
                    provider_metadata={"period": "24h"},
                )
            )
            newer_metric = await metrics.create(
                ContentMetricDocument(
                    profile_id=profile_id,
                    metric_id="linkedin-capture-later",
                    content_id="schema-content",
                    platform="linkedin",
                    captured_at=datetime(2026, 9, 21, tzinfo=UTC),
                    metrics={"impressions": 140, "likes": 8},
                )
            )
            newest = await metrics.list_for_content(content_id="schema-content", limit=1)
            assert newest.items == (newer_metric,)
            assert newest.next_cursor is not None
            older = await metrics.list_for_content(
                content_id="schema-content", limit=1, cursor=newest.next_cursor
            )
            assert older.items == (metric,)
            invalid_metric = metric.model_dump(by_alias=True, mode="python")
            invalid_metric["_id"] = ObjectId()
            invalid_metric["metric_id"] = "invalid-observation"
            invalid_metric["metrics"] = {"views": -1}
            with pytest.raises(WriteError) as rejected:
                await database["content_metrics"].insert_one(invalid_metric)
            assert rejected.value.code == 121

            invalid = stored_item.model_dump(by_alias=True, mode="python")
            invalid["_id"] = ObjectId()
            invalid["content_id"] = "invalid-status"
            invalid["status"] = "not-a-content-status"
            with pytest.raises(WriteError) as rejected:
                await database["content_items"].insert_one(invalid)
            assert rejected.value.code == 121
    finally:
        async with initialized_mongodb() as mongodb:
            for collection_name in V2_CONTENT_COLLECTIONS:
                await mongodb.database[collection_name].delete_many({"profile_id": profile_id})
