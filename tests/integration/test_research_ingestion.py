"""Real-MongoDB V3 Phase 3 manual ingestion, provenance, and idempotency checks."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from bson import ObjectId

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.models.research import ResearchRecordStatus, ResearchSourceType, SourceClass
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.research_repository import (
    MongoIngestionJobRepository,
    MongoResearchChunkRepository,
    MongoResearchDocumentRepository,
    MongoResearchProjectRepository,
    MongoResearchSourceCatalogRepository,
    MongoResearchSourceRepository,
    MongoSourceSnapshotRepository,
)
from app.research.providers import IngestionPayload
from app.services.project_service import ProjectService
from app.services.research_ingestion_service import ResearchIngestionService
from app.services.research_project_service import ResearchProjectService
from app.services.research_source_service import ResearchSourceService

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
async def test_manual_ingestion_persists_immutable_snapshot_document_chunks_and_job() -> None:
    suffix = str(ObjectId())
    project_id = f"phase3-payments-{suffix}"
    research_id = f"phase3-ingestion-{suffix}"
    source_id = f"phase3-manual-{suffix}"
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            v1_projects = MongoProjectRepository(database)
            await ProjectService(v1_projects).create_project(
                project_id=project_id,
                name="Phase 3 payments",
                description="V1 project used to preserve Phase 3 authorization metadata.",
                repository="https://example.test/payments.git",
                environment="test",
            )
            projects = MongoResearchProjectRepository(database)
            await ResearchProjectService(projects, v1_projects).create_project(
                research_project_id=research_id,
                name="Manual ingestion",
                question="How does manual evidence preserve citation locations?",
                type="technical_research",
                related_project_ids=[project_id],
                status="active",
                owner_key_id="phase3-test-key",
            )
            sources = ResearchSourceService(
                projects,
                MongoResearchSourceCatalogRepository(database),
                lambda identifier: MongoResearchSourceRepository(database, identifier),
                lambda identifier: MongoSourceSnapshotRepository(database, identifier),
            )
            registered = await sources.create_source(
                research_project_id=research_id,
                source_id=source_id,
                source_type=ResearchSourceType.MANUAL,
                uri=f"manual:retry-runbook-{suffix}",
                title="Retry runbook",
                publisher=None,
                author=None,
                published_at=None,
                retrieved_at=None,
                source_class=SourceClass.USER_PROVIDED,
                trust_metadata={"origin": "integration-test"},
                status=ResearchRecordStatus.ACTIVE,
                owner_key_id="phase3-test-key",
            )
            ingestion = ResearchIngestionService(
                lambda identifier: MongoSourceSnapshotRepository(database, identifier),
                lambda identifier: MongoResearchDocumentRepository(database, identifier),
                lambda identifier: MongoResearchChunkRepository(database, identifier),
                lambda identifier: MongoIngestionJobRepository(database, identifier),
                embeddings=None,
            )
            payload = IngestionPayload(
                data=(
                    b"# Retries\nUse a stable idempotency key.\n\n## Backoff\nWait before retrying."
                ),
                content_type="text/markdown",
                title="Retry runbook",
                language="en",
            )
            first = await ingestion.ingest(
                source=registered.source, payload=payload, manually_provided=True
            )
            second = await ingestion.ingest(
                source=registered.source, payload=payload, manually_provided=True
            )
            documents = await MongoResearchDocumentRepository(database, research_id).list(
                source_id=source_id
            )
            chunks = await MongoResearchChunkRepository(database, research_id).list(
                document_id=documents.items[0].document_id
            )
            snapshot = await MongoSourceSnapshotRepository(
                database, research_id
            ).find_by_source_and_content_hash(source_id, payload.source_hash)
            job = await MongoIngestionJobRepository(database, research_id).get(first.job.job_id)

            assert first.job.status.value == "completed"
            assert second.idempotent is True
            assert first.snapshot.snapshot_id == second.snapshot.snapshot_id
            assert snapshot is not None and snapshot.snapshot_id == first.snapshot.snapshot_id
            assert len(documents.items) == 1
            assert len(chunks.items) == 2
            assert chunks.items[0].locator.path == registered.source.uri
            assert chunks.items[0].metadata["heading_path"] == ["Retries"]
            assert job is not None and job.chunks_created == 2
    finally:
        async with initialized_mongodb() as mongodb:
            for collection in (
                "ingestion_jobs",
                "research_chunks",
                "research_documents",
                "source_snapshots",
                "research_sources",
                "research_projects",
            ):
                await mongodb.database[collection].delete_many({"research_project_id": research_id})
            await mongodb.database["projects"].delete_many({"project_id": project_id})
