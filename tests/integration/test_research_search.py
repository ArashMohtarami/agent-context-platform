"""Real MongoDB/mongot V3 Phase 5 lexical evidence-search checks."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from bson import ObjectId
from v6_legacy_integration import initialize_database

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.research_indexes import ensure_research_text_indexes
from app.models.research import (
    ResearchProjectStatus,
    ResearchProjectType,
    ResearchRecordStatus,
    ResearchSourceType,
    SourceClass,
)
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.research_repository import (
    MongoIngestionJobRepository,
    MongoResearchChunkRepository,
    MongoResearchClaimRepository,
    MongoResearchDocumentRepository,
    MongoResearchNoteRepository,
    MongoResearchProjectRepository,
    MongoResearchSourceCatalogRepository,
    MongoResearchSourceRepository,
    MongoSourceSnapshotRepository,
)
from app.research.providers import IngestionPayload
from app.retrieval.types import SourceMatches
from app.services.project_service import ProjectService
from app.services.research_ingestion_service import ResearchIngestionService
from app.services.research_project_service import ResearchProjectService
from app.services.research_search_service import (
    ResearchSearchFilters,
    ResearchSearchService,
    ResearchSearchUnavailableError,
)
from app.services.research_source_service import ResearchSourceService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

_INDEX_TIMEOUT_SECONDS = 120.0
_POLL_SECONDS = 0.1


class NoV1Retrieval:
    """Source-class filtering makes V1 composition inapplicable in this focused test."""

    async def gather_sources(self, *args: object, **kwargs: object) -> SourceMatches:
        return SourceMatches()


@asynccontextmanager
async def initialized_mongodb() -> AsyncIterator[MongoDBClient]:
    mongodb = MongoDBClient(get_settings())
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        await ensure_research_text_indexes(mongodb.database)
        yield mongodb
    finally:
        await mongodb.close()


@pytest.mark.asyncio
async def test_manual_evidence_is_lexically_searchable_with_provenance_and_source_diversity() -> (
    None
):
    suffix = str(ObjectId())
    project_id = f"phase5-payments-{suffix}"
    research_id = f"phase5-search-{suffix}"
    source_ids = (f"phase5-manual-a-{suffix}", f"phase5-manual-b-{suffix}")
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            v1_projects = MongoProjectRepository(database)
            await ProjectService(v1_projects).create_project(
                project_id=project_id,
                name="Phase 5 payments",
                description="V1 authorization boundary for research search integration coverage.",
                repository="https://example.test/payments.git",
                environment="test",
            )
            projects = MongoResearchProjectRepository(database)
            await ResearchProjectService(projects, v1_projects).create_project(
                research_project_id=research_id,
                name="Search evidence",
                question="Can retrieved research preserve provenance while searching?",
                type=ResearchProjectType.TECHNICAL_RESEARCH,
                related_project_ids=[project_id],
                status=ResearchProjectStatus.ACTIVE,
                owner_key_id="phase5-test-key",
            )
            source_service = ResearchSourceService(
                projects,
                MongoResearchSourceCatalogRepository(database),
                lambda identifier: MongoResearchSourceRepository(database, identifier),
                lambda identifier: MongoSourceSnapshotRepository(database, identifier),
            )
            ingestion = ResearchIngestionService(
                lambda identifier: MongoSourceSnapshotRepository(database, identifier),
                lambda identifier: MongoResearchDocumentRepository(database, identifier),
                lambda identifier: MongoResearchChunkRepository(database, identifier),
                lambda identifier: MongoIngestionJobRepository(database, identifier),
                embeddings=None,
            )
            snapshot_ids: set[str] = set()
            for source_id, content in zip(
                source_ids,
                (
                    b"# Retries\nPHASE5_UNIQUE_RETRY_TOKEN requires an idempotency key.",
                    b"# Backoff\nPHASE5_UNIQUE_RETRY_TOKEN requires bounded exponential backoff.",
                ),
                strict=True,
            ):
                registered = await source_service.create_source(
                    research_project_id=research_id,
                    source_id=source_id,
                    source_type=ResearchSourceType.MANUAL,
                    uri=f"manual:{source_id}",
                    title=f"{source_id} evidence",
                    publisher=None,
                    author=None,
                    published_at=None,
                    retrieved_at=None,
                    source_class=SourceClass.PRIMARY,
                    trust_metadata={"integration": "phase5"},
                    status=ResearchRecordStatus.ACTIVE,
                    owner_key_id="phase5-test-key",
                )
                ingested = await ingestion.ingest(
                    source=registered.source,
                    payload=IngestionPayload(
                        data=content,
                        content_type="text/markdown",
                        title=registered.source.title,
                        language="en",
                    ),
                    manually_provided=True,
                )
                snapshot_ids.add(ingested.snapshot.snapshot_id)

            search = ResearchSearchService(
                lambda identifier: MongoResearchChunkRepository(database, identifier),
                lambda identifier: MongoResearchNoteRepository(database, identifier),
                lambda identifier: MongoResearchClaimRepository(database, identifier),
                lambda identifier: MongoResearchSourceRepository(database, identifier),
                lambda identifier: MongoSourceSnapshotRepository(database, identifier),
                NoV1Retrieval(),  # type: ignore[arg-type]
                embeddings=None,
                token_budget=1_000,
            )
            project = await ResearchProjectService(projects, v1_projects).get_project(research_id)
            deadline = asyncio.get_running_loop().time() + _INDEX_TIMEOUT_SECONDS
            while True:
                try:
                    result = await search.search(
                        project=project,
                        query="PHASE5_UNIQUE_RETRY_TOKEN",
                        method="lexical",
                        filters=ResearchSearchFilters(source_classes=(SourceClass.PRIMARY,)),
                        limit=5,
                        allowed_project_ids=(project_id,),
                    )
                except ResearchSearchUnavailableError:
                    result = None
                if result is not None and len(result.items) == 2:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    found = 0 if result is None else len(result.items)
                    raise AssertionError(
                        f"research text index did not return two chunks; found {found}"
                    )
                await asyncio.sleep(_POLL_SECONDS)

            assert result is not None
            assert {item.source_id for item in result.items} == set(source_ids)
            assert {item.snapshot_id for item in result.items} == snapshot_ids
            assert all(item.locator is not None and item.locator["path"] for item in result.items)
            assert result.items[0].source_class is SourceClass.PRIMARY
            assert result.items[0].retrieval_score > 0.0
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
