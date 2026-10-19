"""Real-MongoDB Phase 12 repository persistence boundary coverage."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from bson import ObjectId
from v6_legacy_integration import initialize_database

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.models.common import Metadata
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
from app.research.repository import ExtractedRepository, RepositoryFile
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

COMMIT = "c" * 40
RETRIEVED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@asynccontextmanager
async def initialized_mongodb() -> AsyncIterator[MongoDBClient]:
    mongodb = MongoDBClient(get_settings())
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        yield mongodb
    finally:
        await mongodb.close()


@dataclass(frozen=True)
class FixtureRepositoryProvider:
    """Inert repository fixture; provider/network controls are unit-tested separately."""

    async def extract_repository(
        self, source: object, payload: IngestionPayload
    ) -> ExtractedRepository:
        assert payload.acquire_repository is True
        content = "def authenticate(token: str) -> bool:\n    return bool(token)"
        content_hash = "a" * 64
        metadata: Metadata = {
            "repository": "https://public.example/acme/payments.git",
            "branch": "main",
            "commit": COMMIT,
            "ingested_at": RETRIEVED.isoformat(),
            "files_seen": 1,
            "files_ingested": 1,
            "files_skipped_binary": 0,
            "files_skipped_secret": 0,
        }
        return ExtractedRepository(
            repository="https://public.example/acme/payments.git",
            branch="main",
            commit=COMMIT,
            tag=None,
            retrieved_at=RETRIEVED,
            source_hash="b" * 64,
            snapshot_metadata=metadata,
            files=(
                RepositoryFile(
                    path="src/auth.py",
                    language="python",
                    content_type="text/x-python",
                    content=content,
                    content_hash=content_hash,
                ),
            ),
        )


@pytest.mark.asyncio
async def test_repository_snapshot_documents_chunks_and_commit_locator_persist() -> None:
    suffix = str(ObjectId())
    project_id = f"phase12-project-{suffix}"
    research_id = f"phase12-research-{suffix}"
    source_id = f"phase12-repository-{suffix}"
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            v1_projects = MongoProjectRepository(database)
            await ProjectService(v1_projects).create_project(
                project_id=project_id,
                name="Phase 12 payments",
                description="V1 authorization boundary for a repository fixture.",
                repository="https://example.test/payments.git",
                environment="test",
            )
            projects = MongoResearchProjectRepository(database)
            await ResearchProjectService(projects, v1_projects).create_project(
                research_project_id=research_id,
                name="Repository evidence",
                question="Where is authentication implemented?",
                type="repository_analysis",
                related_project_ids=[project_id],
                status="active",
                owner_key_id="phase12-test-key",
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
                source_type=ResearchSourceType.GIT,
                uri="https://public.example/acme/payments.git",
                title="Payments repository",
                publisher=None,
                author=None,
                published_at=None,
                retrieved_at=None,
                source_class=SourceClass.SOURCE_CODE,
                trust_metadata={"branch": "main"},
                status=ResearchRecordStatus.ACTIVE,
                owner_key_id="phase12-test-key",
            )
            ingestion = ResearchIngestionService(
                lambda identifier: MongoSourceSnapshotRepository(database, identifier),
                lambda identifier: MongoResearchDocumentRepository(database, identifier),
                lambda identifier: MongoResearchChunkRepository(database, identifier),
                lambda identifier: MongoIngestionJobRepository(database, identifier),
                embeddings=None,
                git_provider=FixtureRepositoryProvider(),
            )
            payload = IngestionPayload(
                data=b"",
                content_type="application/octet-stream",
                title="Payments repository",
                language="und",
                acquire_repository=True,
            )
            first = await ingestion.ingest(
                source=registered.source, payload=payload, manually_provided=False
            )
            second = await ingestion.ingest(
                source=registered.source, payload=payload, manually_provided=False
            )
            documents = await MongoResearchDocumentRepository(database, research_id).list(
                source_id=source_id
            )
            chunks = await MongoResearchChunkRepository(database, research_id).list(
                document_id=documents.items[0].document_id
            )

            assert second.idempotent is True
            assert first.snapshot.version_identifier == COMMIT
            assert (
                first.snapshot.metadata["repository"] == "https://public.example/acme/payments.git"
            )
            assert first.snapshot.metadata["branch"] == "main"
            assert len(documents.items) == 1
            assert len(chunks.items) == 1
            chunk = chunks.items[0]
            assert chunk.locator.path == "src/auth.py"
            assert chunk.locator.commit == COMMIT
            assert (chunk.locator.line_start, chunk.locator.line_end) == (1, 2)
            assert chunk.metadata["symbol"] == "authenticate"
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
