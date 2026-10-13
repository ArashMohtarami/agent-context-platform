"""Real-MongoDB checks for V3 Phase 0 collection and index initialization."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from bson import ObjectId
from pymongo.errors import WriteError

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.indexes import INDEX_DEFINITIONS
from app.db.initializer import initialize_database
from app.db.migrations.v001_initial_schema import V1_COLLECTIONS
from app.db.migrations.v006_creator_content_schema import V2_CONTENT_COLLECTIONS
from app.db.migrations.v013_research_schema import V3_RESEARCH_COLLECTIONS
from app.models.research import (
    ResearchChunkDocument,
    ResearchDocumentDocument,
    ResearchProjectDocument,
    ResearchSourceDocument,
    SourceClass,
    SourceSnapshotDocument,
)
from app.repositories.research_repository import (
    MongoResearchChunkRepository,
    MongoResearchDocumentRepository,
    MongoResearchProjectRepository,
    MongoResearchSourceRepository,
    MongoSourceSnapshotRepository,
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
    mongodb = MongoDBClient(get_settings())
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        yield mongodb
    finally:
        await mongodb.close()


@pytest.mark.asyncio
async def test_v3_schema_migration_is_idempotent_and_preserves_v1_v2_collections() -> None:
    suffix = str(ObjectId())
    research_project_id = f"v3-schema-{suffix}"
    source_id = f"provider-docs-{suffix}"
    authorization = {"project_ids": ["payments-api"], "owner_key_id": "v3-schema-key"}
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            await initialize_database(database)

            names = set(await database.list_collection_names())
            assert set(V1_COLLECTIONS).issubset(names)
            assert set(V2_CONTENT_COLLECTIONS).issubset(names)
            assert set(V3_RESEARCH_COLLECTIONS).issubset(names)
            for collection_name in V3_RESEARCH_COLLECTIONS:
                index_information = await database[collection_name].index_information()
                expected_names = {
                    definition.document["name"] for definition in INDEX_DEFINITIONS[collection_name]
                }
                assert expected_names.issubset(index_information)

            assert await database["_schema_migrations"].count_documents({"version": 13}) == 1

            projects = MongoResearchProjectRepository(database)
            project = await projects.create(
                ResearchProjectDocument(
                    research_project_id=research_project_id,
                    name="V3 schema persistence",
                    question="Does the migration preserve evidence provenance?",
                    type="technical_research",
                    related_project_ids=["payments-api"],
                    authorization=authorization,
                )
            )
            assert await projects.get(research_project_id) == project

            sources = MongoResearchSourceRepository(database, research_project_id)
            source = await sources.create(
                ResearchSourceDocument(
                    research_project_id=research_project_id,
                    source_id=source_id,
                    source_type="url",
                    uri="https://docs.example.test/retry",
                    canonical_uri="https://docs.example.test/retry",
                    title="Provider retry documentation",
                    source_class=SourceClass.OFFICIAL_DOCUMENTATION,
                    authorization=authorization,
                )
            )
            # Repository scope blocks a source from being read through a different
            # research project before later V3 services add Principal enforcement.
            assert (
                await MongoResearchSourceRepository(database, "unrelated-project").get(source_id)
                is None
            )
            second = await sources.create(
                ResearchSourceDocument(
                    research_project_id=research_project_id,
                    source_id=f"community-post-{suffix}",
                    source_type="manual",
                    uri=f"manual:community-{suffix}",
                    title="Community discussion",
                    source_class=SourceClass.COMMUNITY,
                    authorization=authorization,
                )
            )
            page = await sources.list(limit=1)
            assert len(page.items) == 1
            assert page.next_cursor is not None
            second_page = await sources.list(limit=1, cursor=page.next_cursor)
            assert {item.source_id for item in (*page.items, *second_page.items)} == {
                source.source_id,
                second.source_id,
            }

            snapshots = MongoSourceSnapshotRepository(database, research_project_id)
            snapshot = await snapshots.create(
                SourceSnapshotDocument(
                    research_project_id=research_project_id,
                    source_id=source_id,
                    snapshot_id="retry-20260928",
                    retrieved_at=datetime(2026, 9, 28, tzinfo=UTC),
                    content_hash="a" * 64,
                    version_identifier="etag:abc",
                    authorization=authorization,
                )
            )
            assert await snapshots.get(source_id, "retry-20260928") == snapshot

            documents = MongoResearchDocumentRepository(database, research_project_id)
            document = await documents.create(
                ResearchDocumentDocument(
                    research_project_id=research_project_id,
                    source_id=source_id,
                    snapshot_id="retry-20260928",
                    document_id="retry-guide",
                    title="Provider retry documentation",
                    content_type="text/markdown",
                    content="Retry requests require an idempotency key.",
                    content_hash="a" * 64,
                    source_class=SourceClass.OFFICIAL_DOCUMENTATION,
                    authorization=authorization,
                )
            )
            chunks = MongoResearchChunkRepository(database, research_project_id)
            chunk = await chunks.create(
                ResearchChunkDocument(
                    research_project_id=research_project_id,
                    source_id=source_id,
                    snapshot_id="retry-20260928",
                    document_id="retry-guide",
                    chunk_id="idempotency-requirement",
                    chunk_index=0,
                    content="Retry requests require an idempotency key.",
                    source_class=SourceClass.OFFICIAL_DOCUMENTATION,
                    locator={"url": "https://docs.example.test/retry", "anchor": "retries"},
                    authorization=authorization,
                )
            )
            assert await documents.get(source_id, "retry-20260928", "retry-guide") == document
            assert await chunks.get("retry-guide", "idempotency-requirement") == chunk

            invalid = source.model_dump(by_alias=True, mode="python")
            invalid["_id"] = ObjectId()
            invalid["source_id"] = f"invalid-source-{suffix}"
            invalid["canonical_uri"] = f"https://docs.example.test/invalid-{suffix}"
            invalid["source_class"] = "untrusted-proof"
            with pytest.raises(WriteError):
                await database["research_sources"].insert_one(invalid)
    finally:
        async with initialized_mongodb() as mongodb:
            for collection_name in V3_RESEARCH_COLLECTIONS:
                await mongodb.database[collection_name].delete_many(
                    {"research_project_id": research_project_id}
                )
