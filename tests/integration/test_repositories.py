"""Real MongoDB Phase 3 repository, validator, migration, and index checks."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from bson import ObjectId
from pymongo.errors import WriteError
from v6_legacy_integration import initialize_database

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.tenant_schema import TENANT_INDEX_DEFINITIONS as INDEX_DEFINITIONS
from app.models import (
    ConversationDocument,
    MemoryDocument,
    ProjectContextDocument,
    ProjectDocument,
    RetrievalLogDocument,
    SkillChunkDocument,
    SkillDocument,
)
from app.repositories.context_repository import MongoProjectContextRepository
from app.repositories.conversation_repository import MongoConversationRepository
from app.repositories.errors import (
    ConcurrencyConflictError,
    DuplicateDocumentError,
    ProjectIsolationError,
)
from app.repositories.memory_repository import MongoMemoryRepository
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.retrieval_repository import MongoRetrievalLogRepository
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]


@asynccontextmanager
async def initialized_mongodb(run_id: str | None = None) -> AsyncIterator[MongoDBClient]:
    """Yield a connected client after idempotent Phase 3 initialization."""
    mongodb = MongoDBClient(get_settings())
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        yield mongodb
    finally:
        if run_id is not None:
            project_ids = [f"phase3-a-{run_id}", f"phase3-b-{run_id}"]
            await mongodb.database["skill_chunks"].delete_many(
                {"skill_id": f"phase3-skill-{run_id}"}
            )
            await mongodb.database["skills"].delete_many({"skill_id": f"phase3-skill-{run_id}"})
            for collection_name in (
                "project_context",
                "memories",
                "conversations",
                "retrieval_logs",
                "projects",
            ):
                await mongodb.database[collection_name].delete_many(
                    {"project_id": {"$in": project_ids}}
                )
        await mongodb.close()


@pytest.mark.asyncio
async def test_initialization_is_idempotent_and_mongodb_rejects_invalid_documents() -> None:
    async with initialized_mongodb() as mongodb:
        await initialize_database(mongodb.database)
        collection_names = set(await mongodb.database.list_collection_names())
        assert set(INDEX_DEFINITIONS).issubset(collection_names)

        for collection_name, definitions in INDEX_DEFINITIONS.items():
            index_information = await mongodb.database[collection_name].index_information()
            expected_names = {definition.document["name"] for definition in definitions}
            assert expected_names.issubset(index_information)

        assert await mongodb.database["_schema_migrations"].count_documents({"version": 1}) == 1
        with pytest.raises(WriteError):
            await mongodb.database["projects"].insert_one({"invalid": True})


@pytest.mark.asyncio
async def test_repository_crud_pagination_updates_and_project_isolation() -> None:
    run_id = str(ObjectId())
    project_a_id = f"phase3-a-{run_id}"
    project_b_id = f"phase3-b-{run_id}"
    skill_id = f"phase3-skill-{run_id}"

    async with initialized_mongodb(run_id) as mongodb:
        database = mongodb.database
        projects = MongoProjectRepository(database)
        skills = MongoSkillRepository(database)
        chunks = MongoSkillChunkRepository(database)
        context_a = MongoProjectContextRepository(database, project_a_id)
        context_b = MongoProjectContextRepository(database, project_b_id)
        memories_a = MongoMemoryRepository(database, project_id=project_a_id)
        memories_b = MongoMemoryRepository(database, project_id=project_b_id)
        global_memories = MongoMemoryRepository(database, project_id=None)
        conversations = MongoConversationRepository(database, project_a_id)
        retrieval_logs = MongoRetrievalLogRepository(database, project_id=project_a_id)

        project_a = await projects.create(
            ProjectDocument(
                project_id=project_a_id,
                name="Phase 3 A",
                description="First isolated project",
                repository="ssh://git/phase3-a.git",
                environment="test",
            )
        )
        await projects.create(
            ProjectDocument(
                project_id=project_b_id,
                name="Phase 3 B",
                description="Second isolated project",
                repository="ssh://git/phase3-b.git",
                environment="test",
            )
        )
        assert await projects.get(project_a_id) == project_a
        updated_project = await projects.update(
            project_a.model_copy(update={"description": "Updated project"}),
            expected_revision=1,
        )
        assert updated_project.revision == 2

        await skills.create(
            SkillDocument(
                skill_id=skill_id,
                name="Phase 3 Skill",
                version="1.0.0",
                description="Repository test skill",
                category="test",
                trust_level="generated",
                source_path="tests/phase3.md",
                content_hash="a" * 64,
            )
        )
        # The unique partial index is reported as a typed repository error
        # rather than a driver exception, so services can act on it.
        with pytest.raises(DuplicateDocumentError):
            await skills.create(
                SkillDocument(
                    skill_id=skill_id,
                    name="Phase 3 Skill",
                    version="2.0.0",
                    description="Only one version can be active",
                    category="test",
                    trust_level="generated",
                    source_path="tests/phase3.md",
                    content_hash="b" * 64,
                )
            )
        archived_skill = await skills.set_status(
            skill_id,
            "1.0.0",
            "archived",
            expected_revision=1,
        )
        assert archived_skill.revision == 2
        assert (await skills.get(skill_id, "1.0.0")) == archived_skill

        chunk = await chunks.create(
            SkillChunkDocument(
                skill_id=skill_id,
                skill_version="1.0.0",
                chunk_id="chunk-0",
                content="Original chunk",
                section="Test",
                chunk_index=0,
                tokens=2,
            )
        )
        updated_chunk = await chunks.update(
            chunk.model_copy(update={"content": "Corrected runtime chunk"}),
            expected_revision=1,
        )
        assert updated_chunk.revision == 2

        context_documents = [
            await context_a.create(
                ProjectContextDocument(
                    project_id=project_a_id,
                    type="architecture",
                    title=f"Context {index}",
                    content=f"Private A content {index}",
                    importance=0.8,
                    source="user",
                    external_id=f"context-{index}",
                )
            )
            for index in range(3)
        ]
        other_context = await context_b.create(
            ProjectContextDocument(
                project_id=project_b_id,
                type="security",
                title="Project B private context",
                content="Never visible through project A",
                importance=1.0,
                source="user",
            )
        )
        assert await context_a.get(other_context.id) is None
        with pytest.raises(ProjectIsolationError):
            await context_a.create(other_context)

        first_page = await context_a.list(limit=2)
        assert len(first_page.items) == 2
        assert first_page.next_cursor is not None
        second_page = await context_a.list(limit=2, cursor=first_page.next_cursor)
        assert len(second_page.items) == 1
        assert {item.id for item in (*first_page.items, *second_page.items)} == {
            item.id for item in context_documents
        }

        updated_context = await context_a.update(
            context_documents[0].model_copy(update={"content": "Updated A content"}),
            expected_revision=1,
        )
        assert updated_context.revision == 2
        with pytest.raises(ConcurrencyConflictError):
            await context_a.update(context_documents[0], expected_revision=1)

        memory_a = await memories_a.create(
            MemoryDocument(
                scope="project",
                project_id=project_a_id,
                type="preference",
                content="Project A memory",
                importance=0.8,
                confidence=0.9,
                source="user",
                external_id="memory-a",
            )
        )
        memory_b = await memories_b.create(
            MemoryDocument(
                scope="project",
                project_id=project_b_id,
                type="preference",
                content="Project B memory",
                importance=0.8,
                confidence=0.9,
                source="user",
            )
        )
        global_memory = await global_memories.create(
            MemoryDocument(
                scope="global",
                type="preference",
                content="Global memory",
                importance=0.7,
                confidence=0.9,
                source="user",
            )
        )
        assert [item.id for item in (await memories_a.list()).items] == [memory_a.id]
        assert await memories_a.get(memory_b.id) is None
        with pytest.raises(ProjectIsolationError):
            await memories_a.create(memory_b)
        updated_memory = await memories_a.update(
            memory_a.model_copy(update={"status": "archived"}),
            expected_revision=1,
        )
        assert updated_memory.revision == 2

        conversation = await conversations.create(
            ConversationDocument(
                project_id=project_a_id,
                session_id="session-1",
                role="user",
                content="Original message",
            )
        )
        updated_conversation = await conversations.update(
            conversation.model_copy(update={"content": "Corrected message"}),
            expected_revision=1,
        )
        assert updated_conversation.revision == 2

        retrieval_log = await retrieval_logs.create(
            RetrievalLogDocument(
                query="database boundaries",
                project_id=project_a_id,
                requested_sources=["project_context"],
                result_count=1,
                selected_result_ids=[str(updated_context.id)],
                latency_ms=3.5,
                retrieval_method="lexical",
            )
        )
        assert (await retrieval_logs.list()).items == (retrieval_log,)

        assert await retrieval_logs.delete(retrieval_log.id, expected_revision=1)
        assert await conversations.delete(updated_conversation.id, expected_revision=2)
        assert await global_memories.delete(global_memory.id, expected_revision=1)
        assert await memories_b.delete(memory_b.id, expected_revision=1)
        assert await memories_a.delete(updated_memory.id, expected_revision=2)
        assert await context_a.delete(updated_context.id, expected_revision=2)
        for context_document in context_documents[1:]:
            assert await context_a.delete(context_document.id, expected_revision=1)
        assert await context_b.delete(other_context.id, expected_revision=1)
        assert await chunks.delete(
            skill_id,
            "1.0.0",
            "chunk-0",
            expected_revision=2,
        )
        assert await skills.delete(skill_id, "1.0.0", expected_revision=2)
        assert await projects.delete(project_a_id, expected_revision=2)
        assert await projects.delete(project_b_id, expected_revision=1)
