"""Live project and context checks against mongod and self-managed mongot."""

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest
from bson import ObjectId
from v6_legacy_integration import initialize_database

from app.core.config import Settings, get_settings
from app.db.client import MongoDBClient
from app.db.context_indexes import (
    describe_context_text_index,
    describe_context_vector_index,
    ensure_context_text_index,
    ensure_context_vector_index,
)
from app.embeddings.types import EmbeddingVector
from app.projects.errors import ContextNotFoundError, ProjectAlreadyExistsError
from app.repositories.context_repository import MongoProjectContextRepository
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.services.context_service import ContextService
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

PAYMENT_CONTEXT = (
    "Payment requests carry a UUID4 idempotency key. PaymentIdempotencyService "
    "stores every key so a retried request is answered from the first result."
)
WALLET_CONTEXT = (
    "Wallet balances are eventually consistent. The wallet:read scope is "
    "required before any balance is returned to a caller."
)


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
async def context_database(project_ids: Sequence[str]) -> AsyncIterator[MongoDBClient]:
    """Connect, build both context indexes, and remove only this test's records."""
    settings = get_settings()
    mongodb = MongoDBClient(settings)
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        await ensure_context_vector_index(
            mongodb.database,
            dimensions=settings.embedding_dimensions,
        )
        await ensure_context_text_index(mongodb.database)
        await wait_for_queryable_indexes(mongodb)
        yield mongodb
    finally:
        await mongodb.database["project_context"].delete_many(
            {"project_id": {"$in": list(project_ids)}}
        )
        await mongodb.database["projects"].delete_many({"project_id": {"$in": list(project_ids)}})
        await mongodb.close()


async def wait_for_queryable_indexes(mongodb: MongoDBClient) -> None:
    """Wait until mongot reports both context indexes as queryable."""
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    while True:
        vector = await describe_context_vector_index(mongodb.database)
        text = await describe_context_text_index(mongodb.database)
        if vector.ready and text.ready:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("the project context indexes did not become queryable")
        await asyncio.sleep(POLL_SECONDS)


async def wait_for_indexed_context(
    retrieval: RetrievalService,
    project_id: str,
    *,
    expected: int,
) -> None:
    """Wait until mongot has indexed the context this test just stored."""
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    while True:
        results = await retrieval.context_search("idempotency key", project_id=project_id)
        if len(results) >= expected:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"expected {expected} indexed context items, found {len(results)}")
        await asyncio.sleep(POLL_SECONDS)


@pytest.fixture(scope="module")
def run_id() -> str:
    """Return one identifier owned by this test module's records."""
    return str(ObjectId())


@pytest.mark.asyncio
async def test_projects_and_context_are_created_read_and_updated(run_id: str) -> None:
    settings = get_settings()
    project_id = f"phase9-payment-{run_id}"

    async with context_database([project_id]) as mongodb:
        projects = ProjectService(MongoProjectRepository(mongodb.database))
        contexts = ContextService(
            MongoProjectRepository(mongodb.database),
            lambda scope: MongoProjectContextRepository(mongodb.database, scope),
            AxisEmbedder(settings),
        )

        project = await projects.create_project(
            project_id=project_id,
            name="Sample Company Payment Service",
            description="Payment orchestration",
            repository="git@example.invalid:sampleco/payment.git",
            environment="production",
            tags=["payments"],
        )
        assert project.project_id == project_id
        assert (await projects.get_project(project_id)).name == "Sample Company Payment Service"
        assert any(item.project_id == project_id for item in (await projects.list_projects()).items)

        with pytest.raises(ProjectAlreadyExistsError):
            await projects.create_project(
                project_id=project_id,
                name="Duplicate",
                description="Same identifier",
                repository="git@example.invalid:sampleco/other.git",
                environment="production",
            )

        stored = await contexts.create_context(
            project_id,
            context_type="architecture_decision",
            title="Payment idempotency strategy",
            content=PAYMENT_CONTEXT,
            importance=0.95,
            source="user",
            tags=["payments", "idempotency"],
        )
        assert stored.has_embedding is True
        assert stored.embedding_dimensions == settings.embedding_dimensions

        loaded = await contexts.get_context(project_id, stored.id)
        assert loaded.title == "Payment idempotency strategy"

        updated = await contexts.update_context(
            project_id,
            stored.id,
            importance=0.5,
            status="archived",
        )
        assert updated.importance == 0.5
        assert updated.status == "archived"
        assert updated.revision == stored.revision + 1

        active = await contexts.list_context(project_id)
        archived = await contexts.list_context(project_id, status="archived")
        assert active.items == ()
        assert [item.id for item in archived.items] == [stored.id]


@pytest.mark.asyncio
async def test_repeating_a_create_with_an_idempotency_key_stores_one_item(run_id: str) -> None:
    settings = get_settings()
    project_id = f"phase9-idempotent-{run_id}"

    async with context_database([project_id]) as mongodb:
        projects = ProjectService(MongoProjectRepository(mongodb.database))
        contexts = ContextService(
            MongoProjectRepository(mongodb.database),
            lambda scope: MongoProjectContextRepository(mongodb.database, scope),
            AxisEmbedder(settings),
        )
        await projects.create_project(
            project_id=project_id,
            name="Idempotent",
            description="Repeat-safe imports",
            repository="git@example.invalid:sampleco/idempotent.git",
            environment="staging",
        )

        first = await contexts.create_context(
            project_id,
            context_type="convention",
            title="Naming",
            content="Use snake_case for MongoDB fields.",
            importance=0.4,
            source="user",
            external_id="naming",
        )
        second = await contexts.create_context(
            project_id,
            context_type="convention",
            title="Naming",
            content="Something else entirely.",
            importance=0.4,
            source="user",
            external_id="naming",
        )

        page = await contexts.list_context(project_id)

    assert second.id == first.id
    assert second.content == "Use snake_case for MongoDB fields."
    assert len(page.items) == 1


@pytest.mark.asyncio
async def test_context_search_and_hybrid_retrieval_never_cross_projects(run_id: str) -> None:
    settings = get_settings()
    payment_id = f"phase9-payment-search-{run_id}"
    wallet_id = f"phase9-wallet-search-{run_id}"

    async with context_database([payment_id, wallet_id]) as mongodb:
        projects = ProjectService(MongoProjectRepository(mongodb.database))
        contexts = ContextService(
            MongoProjectRepository(mongodb.database),
            lambda scope: MongoProjectContextRepository(mongodb.database, scope),
            AxisEmbedder(settings),
        )
        retrieval = RetrievalService(
            MongoSkillRepository(mongodb.database),
            MongoSkillChunkRepository(mongodb.database),
            AxisEmbedder(settings),
            contexts=lambda scope: MongoProjectContextRepository(mongodb.database, scope),
        )

        for project_id, title, content in (
            (payment_id, "Payment idempotency strategy", PAYMENT_CONTEXT),
            (wallet_id, "Wallet consistency", WALLET_CONTEXT),
        ):
            await projects.create_project(
                project_id=project_id,
                name=project_id,
                description="Phase 9 isolation coverage",
                repository=f"git@example.invalid:sampleco/{project_id}.git",
                environment="production",
            )
            await contexts.create_context(
                project_id,
                context_type="architecture_decision",
                title=title,
                content=content,
                importance=0.9,
                source="user",
                tags=["phase9"],
            )

        await wait_for_indexed_context(retrieval, payment_id, expected=1)

        payment_results = await retrieval.context_search(
            "idempotency key",
            project_id=payment_id,
        )
        wallet_results = await retrieval.context_search(
            "idempotency key",
            project_id=wallet_id,
        )
        hybrid = await retrieval.hybrid_search("idempotency key", project_id=payment_id)
        unscoped = await retrieval.hybrid_search("idempotency key")

        # A context item is reachable only from inside its own project.
        stored = payment_results[0].context
        with pytest.raises(ContextNotFoundError):
            await contexts.get_context(wallet_id, stored.id)

    assert [result.context.project_id for result in payment_results] == [payment_id]
    # The wallet project's own search never returns the payment project's entry.
    assert all(result.context.project_id == wallet_id for result in wallet_results)
    assert payment_results[0].score > 0.0

    context_items = [item for item in hybrid.items if item.source == "project_context"]
    assert context_items
    assert all(item.metadata["project_id"] == payment_id for item in context_items)
    # Without a project, no project-owned context is reachable at all.
    assert all(item.source != "project_context" for item in unscoped.items)


@pytest.mark.asyncio
async def test_the_context_indexes_reconcile_idempotently() -> None:
    settings = get_settings()
    mongodb = MongoDBClient(settings)
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        first_vector = await ensure_context_vector_index(
            mongodb.database,
            dimensions=settings.embedding_dimensions,
        )
        repeated_vector = await ensure_context_vector_index(
            mongodb.database,
            dimensions=settings.embedding_dimensions,
        )
        first_text = await ensure_context_text_index(mongodb.database)
        repeated_text = await ensure_context_text_index(mongodb.database)
        vector_status = await describe_context_vector_index(mongodb.database)
        text_status = await describe_context_text_index(mongodb.database)
    finally:
        await mongodb.close()

    assert first_vector in {"created", "unchanged", "updated"}
    assert first_text in {"created", "unchanged", "updated"}
    assert (repeated_vector, repeated_text) == ("unchanged", "unchanged")
    assert vector_status.dimensions == settings.embedding_dimensions
    assert vector_status.similarity == "cosine"
    assert text_status.analyzer == "lucene.standard"
