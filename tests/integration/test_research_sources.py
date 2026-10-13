"""Real-MongoDB V3 Phase 2 source registry and immutable snapshot checks."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.models.research import ResearchRecordStatus, ResearchSourceType, SourceClass
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.research_repository import (
    MongoResearchProjectRepository,
    MongoResearchSourceCatalogRepository,
    MongoResearchSourceRepository,
    MongoSourceSnapshotRepository,
)
from app.research.errors import ResearchSourceAlreadyExistsError, SourceSnapshotAlreadyExistsError
from app.services.project_service import ProjectService
from app.services.research_project_service import ResearchProjectService
from app.services.research_source_service import ResearchSourceService, SourceSnapshotRegistration

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

WHEN = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)


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
async def test_source_registry_persists_canonical_duplicate_checks_and_append_only_snapshots() -> (
    None
):
    suffix = str(ObjectId())
    project_id = f"phase2-payments-{suffix}"
    research_id = f"phase2-source-registry-{suffix}"
    source_id = f"phase2-provider-docs-{suffix}"
    manual_source_id = f"phase2-manual-runbook-{suffix}"
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            v1_projects = MongoProjectRepository(database)
            await ProjectService(v1_projects).create_project(
                project_id=project_id,
                name="Phase 2 payments",
                description="V1 project used for source authorization persistence checks.",
                repository="https://example.test/payments.git",
                environment="test",
            )
            projects = MongoResearchProjectRepository(database)
            await ResearchProjectService(projects, v1_projects).create_project(
                research_project_id=research_id,
                name="Source registry",
                question="Which supplied sources describe retry semantics?",
                type="technical_research",
                related_project_ids=[project_id],
                status="active",
                owner_key_id="phase2-test-key",
            )
            sources = ResearchSourceService(
                projects,
                MongoResearchSourceCatalogRepository(database),
                lambda identifier: MongoResearchSourceRepository(database, identifier),
                lambda identifier: MongoSourceSnapshotRepository(database, identifier),
            )
            first = await sources.create_source(
                research_project_id=research_id,
                source_id=source_id,
                source_type=ResearchSourceType.URL,
                uri="HTTPS://Example.TEST:443/retries#section",
                title="Provider retry guide",
                publisher="Example",
                author="Documentation team",
                published_at=WHEN,
                retrieved_at=WHEN,
                source_class=SourceClass.OFFICIAL_DOCUMENTATION,
                trust_metadata={"origin": "integration-test"},
                status=ResearchRecordStatus.ACTIVE,
                owner_key_id="phase2-test-key",
                snapshot=SourceSnapshotRegistration(
                    snapshot_id="snapshot-1",
                    retrieved_at=WHEN,
                    content_hash="a" * 64,
                    version_identifier="etag-1",
                    metadata={"etag": "etag-1"},
                ),
            )
            manual = await sources.create_source(
                research_project_id=research_id,
                source_id=manual_source_id,
                source_type=ResearchSourceType.MANUAL,
                uri=f"manual:retry-runbook-{suffix}",
                title="Operator retry runbook",
                publisher=None,
                author="Operations team",
                published_at=None,
                retrieved_at=WHEN,
                source_class=SourceClass.USER_PROVIDED,
                trust_metadata={"origin": "integration-test"},
                status=ResearchRecordStatus.ACTIVE,
                owner_key_id="phase2-test-key",
            )
            with pytest.raises(ResearchSourceAlreadyExistsError):
                await sources.create_source(
                    research_project_id=research_id,
                    source_id=f"phase2-duplicate-{suffix}",
                    source_type=ResearchSourceType.URL,
                    uri="https://example.test:443/retries",
                    title="Duplicate canonical URI",
                    publisher=None,
                    author=None,
                    published_at=None,
                    retrieved_at=WHEN,
                    source_class=SourceClass.UNKNOWN,
                    trust_metadata={},
                    status=ResearchRecordStatus.ACTIVE,
                    owner_key_id="phase2-test-key",
                )
            appended = await sources.create_snapshot(
                research_project_id=research_id,
                source_id=source_id,
                snapshot=SourceSnapshotRegistration(
                    snapshot_id="snapshot-2",
                    retrieved_at=WHEN,
                    content_hash="b" * 64,
                    version_identifier="etag-2",
                    metadata={"etag": "etag-2"},
                ),
            )
            with pytest.raises(SourceSnapshotAlreadyExistsError):
                await sources.create_snapshot(
                    research_project_id=research_id,
                    source_id=source_id,
                    snapshot=SourceSnapshotRegistration(
                        snapshot_id="snapshot-1",
                        retrieved_at=WHEN,
                        content_hash="c" * 64,
                        version_identifier="etag-rewrite",
                        metadata={},
                    ),
                )
            page = await sources.list_sources(
                research_project_id=research_id,
                allowed_project_ids=(project_id,),
            )
            snapshots = await sources.list_snapshots(
                research_project_id=research_id, source_id=source_id
            )

            assert first.source.canonical_uri == "https://example.test/retries"
            assert first.snapshot is not None
            assert first.snapshot.content_hash == "a" * 64
            assert manual.source.source_type is ResearchSourceType.MANUAL
            assert appended.snapshot_id == "snapshot-2"
            assert [item.snapshot_id for item in snapshots.items] == ["snapshot-1", "snapshot-2"]
            assert {item.source_id for item in page.items} == {source_id, manual_source_id}
    finally:
        async with initialized_mongodb() as mongodb:
            await mongodb.database["source_snapshots"].delete_many(
                {"research_project_id": research_id}
            )
            await mongodb.database["research_sources"].delete_many(
                {"research_project_id": research_id}
            )
            await mongodb.database["research_projects"].delete_many(
                {"research_project_id": research_id}
            )
            await mongodb.database["projects"].delete_many({"project_id": project_id})
