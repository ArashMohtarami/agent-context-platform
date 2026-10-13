"""Live memory checks against mongod and self-managed mongot."""

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest
from bson import ObjectId

from app.core.config import Settings, get_settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.db.memory_indexes import (
    describe_memory_text_index,
    describe_memory_vector_index,
    ensure_memory_text_index,
    ensure_memory_vector_index,
)
from app.embeddings.types import EmbeddingVector
from app.memory.errors import MemoryNotFoundError, MemorySupersessionError
from app.repositories.memory_repository import MongoMemoryRepository
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.services.memory_service import MemoryService
from app.services.project_service import ProjectService
from app.services.retrieval_service import RetrievalService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

INDEX_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 0.1

GLOBAL_CONTENT = "Use strict typing in every Python project, checked with mypy in strict mode."
PAYMENT_CONTENT = "Payment retries stop after three attempts and then raise to an operator."
WALLET_CONTENT = "Wallet balances are eventually consistent and must never be cached client side."


class AxisEmbedder:
    """Embed text as a fixed basis vector so similarity is known in advance."""

    def __init__(self, settings: Settings, axis: int = 0) -> None:
        self._settings = settings
        self._axis = axis

    def _vector(self) -> EmbeddingVector:
        dimensions = self._settings.embedding_dimensions
        values = tuple(1.0 if index == self._axis else 0.0 for index in range(dimensions))
        return EmbeddingVector(
            model=self._settings.embedding_model,
            dimensions=dimensions,
            values=values,
        )

    async def embed_query(self, text: str) -> EmbeddingVector:
        _ = text
        return self._vector()

    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        return tuple(self._vector() for _ in contents)


@asynccontextmanager
async def memory_database(
    project_ids: Sequence[str],
    sources: Sequence[str],
) -> AsyncIterator[MongoDBClient]:
    """Connect, build both memory indexes, and remove only this test's records."""
    settings = get_settings()
    mongodb = MongoDBClient(settings)
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        await ensure_memory_vector_index(
            mongodb.database,
            dimensions=settings.embedding_dimensions,
        )
        await ensure_memory_text_index(mongodb.database)
        await wait_for_queryable_indexes(mongodb)
        yield mongodb
    finally:
        await mongodb.database["memories"].delete_many({"source": {"$in": list(sources)}})
        await mongodb.database["projects"].delete_many({"project_id": {"$in": list(project_ids)}})
        await mongodb.close()


async def wait_for_queryable_indexes(mongodb: MongoDBClient) -> None:
    """Wait until mongot reports both memory indexes as queryable."""
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    while True:
        vector = await describe_memory_vector_index(mongodb.database)
        text = await describe_memory_text_index(mongodb.database)
        if vector.ready and text.ready:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("the memory indexes did not become queryable")
        await asyncio.sleep(POLL_SECONDS)


async def wait_for_indexed_memory(
    retrieval: RetrievalService,
    *,
    expected: int,
    project_id: str | None = None,
    client_id: str | None = None,
) -> None:
    """Wait until mongot has indexed the memories this test just stored."""
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    while True:
        results = await retrieval.memory_search(
            "strict typing", project_id=project_id, client_id=client_id
        )
        if len(results) >= expected:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"expected {expected} indexed memories, found {len(results)}")
        await asyncio.sleep(POLL_SECONDS)


@pytest.fixture(scope="module")
def run_id() -> str:
    """Return one identifier owned by this test module's records."""
    return str(ObjectId())[-8:]


def build_services(
    mongodb: MongoDBClient,
    settings: Settings,
) -> tuple[ProjectService, MemoryService, RetrievalService]:
    """Compose the services one live check needs."""
    projects = ProjectService(MongoProjectRepository(mongodb.database))
    memories = MemoryService(
        MongoProjectRepository(mongodb.database),
        lambda scope, project_id, client_id: MongoMemoryRepository(
            mongodb.database,
            scope=scope,
            project_id=project_id,
            client_id=client_id,
        ),
        AxisEmbedder(settings),
    )
    retrieval = RetrievalService(
        MongoSkillRepository(mongodb.database),
        MongoSkillChunkRepository(mongodb.database),
        AxisEmbedder(settings),
        memories=lambda project_id, client_id: MongoMemoryRepository(
            mongodb.database,
            project_id=project_id,
            client_id=client_id,
            scope=(
                "project_agent"
                if project_id is not None and client_id is not None
                else "agent"
                if client_id is not None
                else "project"
                if project_id is not None
                else "global"
            ),
        ),
    )
    return projects, memories, retrieval


@pytest.mark.asyncio
async def test_memory_is_created_read_updated_and_archived(run_id: str) -> None:
    settings = get_settings()
    source = f"phase10-lifecycle-{run_id}"

    async with memory_database([], [source]) as mongodb:
        _, memories, _ = build_services(mongodb, settings)

        stored = await memories.create_memory(
            memory_type="preference",
            content=GLOBAL_CONTENT,
            summary="Strict typing everywhere",
            importance=0.8,
            confidence=0.95,
            source=source,
            tags=["python", "typing"],
        )
        assert stored.scope == "global"
        assert stored.project_id is None
        assert stored.has_embedding is True
        assert stored.embedding_dimensions == settings.embedding_dimensions

        loaded = await memories.get_memory(stored.id)
        assert loaded.summary == "Strict typing everywhere"

        updated = await memories.update_memory(stored.id, confidence=0.5, status="archived")
        assert updated.confidence == 0.5
        assert updated.status == "archived"
        assert updated.revision == stored.revision + 1

        active = await memories.list_memories()
        archived = await memories.list_memories(status="archived")
        assert all(item.id != stored.id for item in active.items)
        assert [item.id for item in archived.items] == [stored.id]

        # Lifecycle is reversible: archiving hides a memory, it does not end it.
        restored = await memories.update_memory(stored.id, status="active")
        assert restored.status == "active"


@pytest.mark.asyncio
async def test_conflicting_memory_supersedes_the_old_record(run_id: str) -> None:
    settings = get_settings()
    source = f"phase10-supersede-{run_id}"

    async with memory_database([], [source]) as mongodb:
        _, memories, _ = build_services(mongodb, settings)

        original = await memories.create_memory(
            memory_type="convention",
            content="Deploys happen on Fridays.",
            importance=0.7,
            confidence=0.8,
            source=source,
        )
        replacement = await memories.create_memory(
            memory_type="convention",
            content="Deploys never happen on Fridays.",
            importance=0.9,
            confidence=0.95,
            source=source,
            supersedes=original.id,
        )
        replaced = await memories.get_memory(original.id)

        assert replacement.supersedes == original.id
        assert replacement.status == "active"
        assert replaced.status == "superseded"
        assert replaced.superseded_by == replacement.id
        # The correction never destroys what it corrects.
        assert replaced.content == "Deploys happen on Fridays."

        with pytest.raises(MemorySupersessionError):
            await memories.create_memory(
                memory_type="convention",
                content="A second, competing correction.",
                importance=0.9,
                confidence=0.9,
                source=source,
                supersedes=original.id,
            )


@pytest.mark.asyncio
async def test_memory_search_and_hybrid_retrieval_respect_every_scope(run_id: str) -> None:
    settings = get_settings()
    payment_id = f"phase10-payment-{run_id}"
    wallet_id = f"phase10-wallet-{run_id}"
    source = f"phase10-scope-{run_id}"

    async with memory_database([payment_id, wallet_id], [source]) as mongodb:
        projects, memories, retrieval = build_services(mongodb, settings)

        for project_id in (payment_id, wallet_id):
            await projects.create_project(
                project_id=project_id,
                name=project_id,
                description="Phase 10 isolation coverage",
                repository=f"git@example.invalid:sampleco/{project_id}.git",
                environment="production",
            )

        global_memory = await memories.create_memory(
            memory_type="preference",
            content=GLOBAL_CONTENT,
            importance=0.8,
            confidence=0.9,
            source=source,
        )
        payment_memory = await memories.create_memory(
            memory_type="convention",
            content=PAYMENT_CONTENT,
            importance=0.9,
            confidence=0.9,
            source=source,
            project_id=payment_id,
        )
        await memories.create_memory(
            memory_type="convention",
            content=WALLET_CONTENT,
            importance=0.9,
            confidence=0.9,
            source=source,
            project_id=wallet_id,
        )

        await wait_for_indexed_memory(retrieval, expected=1)
        await wait_for_indexed_memory(retrieval, expected=2, project_id=payment_id)

        unscoped = await retrieval.memory_search("strict typing", limit=10)
        scoped = await retrieval.memory_search("strict typing", project_id=payment_id, limit=10)
        project_only = await retrieval.memory_search(
            "strict typing",
            project_id=payment_id,
            scopes=["project"],
            limit=10,
        )
        hybrid = await retrieval.hybrid_search("strict typing", project_id=payment_id)

        # A project memory is unreachable from another project's scope.
        with pytest.raises(MemoryNotFoundError):
            await memories.get_memory(payment_memory.id, project_id=wallet_id)
        with pytest.raises(MemoryNotFoundError):
            await memories.get_memory(payment_memory.id)

    unscoped_ids = {result.memory.id for result in unscoped}
    scoped_ids = {result.memory.id for result in scoped}
    project_ids = {result.memory.project_id for result in project_only}

    # Naming no project reaches global memory only.
    assert global_memory.id in unscoped_ids
    assert all(result.memory.scope == "global" for result in unscoped)
    # Naming a project reaches its memory and global memory, never another's.
    assert {global_memory.id, payment_memory.id} <= scoped_ids
    assert all(result.memory.project_id in (None, payment_id) for result in scoped)
    assert project_ids == {payment_id}

    memory_items = [item for item in hybrid.items if item.source == "memory"]
    assert memory_items
    assert all(item.metadata["project_id"] in (None, payment_id) for item in memory_items)
    assert all(item.explanation.source_priority == 0.80 for item in memory_items)


@pytest.mark.asyncio
async def test_agent_memory_shares_durable_knowledge_without_leaking_operational_state(
    run_id: str,
) -> None:
    settings = get_settings()
    project_id = f"phase27-payment-{run_id}"
    source = f"phase27-agent-memory-{run_id}"
    claude_id = f"claude-{run_id}"
    codex_id = f"codex-{run_id}"

    async with memory_database([project_id], [source]) as mongodb:
        projects, memories, retrieval = build_services(mongodb, settings)
        await projects.create_project(
            project_id=project_id,
            name=project_id,
            description="V4 agent memory integration coverage",
            repository=f"git@example.invalid:sampleco/{project_id}.git",
            environment="test",
        )
        global_memory = await memories.create_memory(
            memory_type="preference",
            content="Shared strict typing rule.",
            importance=0.8,
            confidence=0.9,
            source=source,
        )
        project_memory = await memories.create_memory(
            memory_type="architecture",
            content="Payment-service uses an idempotency boundary.",
            importance=0.9,
            confidence=0.95,
            source=source,
            project_id=project_id,
        )
        claude_agent = await memories.create_memory(
            memory_type="note",
            content="Claude is inspecting retry fixture failures.",
            importance=0.4,
            confidence=0.9,
            source=source,
            scope="agent",
            client_id=claude_id,
        )
        claude_project_agent = await memories.create_memory(
            memory_type="note",
            content="Claude checkpoint for payment-service.",
            importance=0.4,
            confidence=0.9,
            source=source,
            scope="project_agent",
            project_id=project_id,
            client_id=claude_id,
        )
        codex_agent = await memories.create_memory(
            memory_type="note",
            content="Codex has a separate migration checkpoint.",
            importance=0.4,
            confidence=0.9,
            source=source,
            scope="agent",
            client_id=codex_id,
        )

        await wait_for_indexed_memory(
            retrieval,
            expected=4,
            project_id=project_id,
            client_id=claude_id,
        )
        claude = await retrieval.memory_search(
            "strict typing", project_id=project_id, client_id=claude_id, limit=10
        )
        codex = await retrieval.memory_search(
            "strict typing", project_id=project_id, client_id=codex_id, limit=10
        )
        indexes = await mongodb.database["memories"].index_information()

        assert await mongodb.database["_schema_migrations"].count_documents({"version": 27}) == 1
        assert {
            "memory_scope_project_client_external_id_uq",
            "memory_scope_project_client_status_updated",
        }.issubset(indexes)
        claude_ids = {result.memory.id for result in claude}
        codex_ids = {result.memory.id for result in codex}
        assert {
            global_memory.id,
            project_memory.id,
            claude_agent.id,
            claude_project_agent.id,
        }.issubset(claude_ids)
        assert codex_agent.id not in claude_ids
        assert {global_memory.id, project_memory.id, codex_agent.id}.issubset(codex_ids)
        assert claude_agent.id not in codex_ids
        with pytest.raises(MemoryNotFoundError):
            await memories.get_memory(claude_agent.id, scope="agent", client_id=codex_id)


@pytest.mark.asyncio
async def test_the_memory_indexes_reconcile_idempotently() -> None:
    settings = get_settings()
    mongodb = MongoDBClient(settings)
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        first_vector = await ensure_memory_vector_index(
            mongodb.database,
            dimensions=settings.embedding_dimensions,
        )
        repeated_vector = await ensure_memory_vector_index(
            mongodb.database,
            dimensions=settings.embedding_dimensions,
        )
        first_text = await ensure_memory_text_index(mongodb.database)
        repeated_text = await ensure_memory_text_index(mongodb.database)
        vector_status = await describe_memory_vector_index(mongodb.database)
        text_status = await describe_memory_text_index(mongodb.database)
    finally:
        await mongodb.close()

    assert first_vector in {"created", "unchanged", "updated"}
    assert first_text in {"created", "unchanged", "updated"}
    assert (repeated_vector, repeated_text) == ("unchanged", "unchanged")
    assert vector_status.dimensions == settings.embedding_dimensions
    assert vector_status.similarity == "cosine"
    assert text_status.analyzer == "lucene.standard"
