"""Current tenant-aware initializer for admitted V1-V5 integration workflows."""

from app.db.initializer import initialize_database as initialize_current_database
from app.security.tenant import require_tenant


async def initialize_database(database):
    # Old test modules retain their original domain operations and assertions,
    # but may not start an old worker against the adopted fixture database.
    require_tenant()
    await initialize_current_database(database, tenant_foundation=True, tenant_isolation=True)


async def seed_projects(database, project_ids):
    """Provision explicitly named fictional fixture projects before their references."""
    from app.models.project import ProjectDocument
    from app.repositories.project_repository import MongoProjectRepository

    for identifier in project_ids:
        await MongoProjectRepository(database).create(
            ProjectDocument(
                project_id=identifier,
                name="Fictional regression project",
                description="Synthetic regression fixture",
                repository="https://example.invalid/fictional.git",
                environment="test",
            )
        )


async def seed_source(
    database,
    *,
    research_project_id,
    source_id,
    snapshot_id,
    authorization,
    document_id=None,
    chunk_id=None,
):
    """Create declared provenance using real models/repositories, without fetching a URL."""
    from app.models.common import utc_now
    from app.models.research import (
        CitationLocator,
        ResearchChunkDocument,
        ResearchDocumentDocument,
        ResearchSourceDocument,
        SourceSnapshotDocument,
    )
    from app.repositories.research_repository import (
        MongoResearchChunkRepository,
        MongoResearchDocumentRepository,
        MongoResearchSourceRepository,
        MongoSourceSnapshotRepository,
    )

    shared = {"research_project_id": research_project_id, "authorization": authorization}
    await MongoResearchSourceRepository(database, research_project_id).create(
        ResearchSourceDocument(
            **shared,
            source_id=source_id,
            source_type="manual",
            uri="manual:" + source_id,
            title="Fictional retained retry evidence",
            source_class="official_documentation",
        )
    )
    await MongoSourceSnapshotRepository(database, research_project_id).create(
        SourceSnapshotDocument(
            **shared,
            source_id=source_id,
            snapshot_id=snapshot_id,
            retrieved_at=utc_now(),
            content_hash="a" * 64,
        )
    )
    if document_id is not None:
        await MongoResearchDocumentRepository(database, research_project_id).create(
            ResearchDocumentDocument(
                **shared,
                source_id=source_id,
                snapshot_id=snapshot_id,
                document_id=document_id,
                title="Fictional retry document",
                content_type="text/plain",
                content="Retryable writes require an idempotency key.",
                content_hash="a" * 64,
                source_class="official_documentation",
            )
        )
    if chunk_id is not None:
        assert document_id is not None
        await MongoResearchChunkRepository(database, research_project_id).create(
            ResearchChunkDocument(
                **shared,
                source_id=source_id,
                snapshot_id=snapshot_id,
                document_id=document_id,
                chunk_id=chunk_id,
                chunk_index=0,
                content="Retryable writes require an idempotency key.",
                source_class="official_documentation",
                locator=CitationLocator(url="https://docs.example.test/retries", page=2),
            )
        )


async def unified_search_ready(database, dimensions):
    """Rebuild the real derived indexes in a newly adopted, empty fixture database."""
    from app.db.context_indexes import ensure_context_text_index, ensure_context_vector_index
    from app.db.memory_indexes import ensure_memory_text_index, ensure_memory_vector_index
    from app.db.text_indexes import ensure_text_indexes
    from app.db.vector_indexes import ensure_vector_indexes

    await ensure_text_indexes(database)
    await ensure_context_text_index(database)
    await ensure_memory_text_index(database)
    await ensure_vector_indexes(database, dimensions=dimensions)
    await ensure_context_vector_index(database, dimensions=dimensions)
    await ensure_memory_vector_index(database, dimensions=dimensions)
    await search_indexes_ready(database, ("skill_chunks", "project_context", "memories"), 6)


async def search_indexes_ready(database, collections, expected):
    """Wait for index readiness before testing asynchronous document visibility."""
    import asyncio

    for _ in range(240):
        indexes = [
            index
            for name in collections
            for index in await (await database[name].list_search_indexes()).to_list(None)
        ]
        if len(indexes) == expected and all(
            index.get("queryable") and index.get("status") == "READY" for index in indexes
        ):
            return
        await asyncio.sleep(0.5)
    raise AssertionError("fixture search indexes did not become queryable")
