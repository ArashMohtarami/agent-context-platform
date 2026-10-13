"""Live unified knowledge search against mongod and self-managed mongot."""

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.core.config import Settings, get_settings
from app.db.client import MongoDBClient
from app.db.context_indexes import (
    describe_context_text_index,
    describe_context_vector_index,
    ensure_context_text_index,
    ensure_context_vector_index,
)
from app.db.initializer import initialize_database
from app.db.memory_indexes import (
    describe_memory_text_index,
    describe_memory_vector_index,
    ensure_memory_text_index,
    ensure_memory_vector_index,
)
from app.db.text_indexes import describe_text_index, ensure_text_indexes
from app.db.vector_indexes import describe_vector_index, ensure_vector_indexes
from app.embeddings.types import EmbeddingVector
from app.models.skill import SkillDocument
from app.models.skill_chunk import SkillChunkDocument
from app.repositories.context_repository import MongoProjectContextRepository
from app.repositories.memory_repository import MongoMemoryRepository
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.retrieval_repository import MongoRetrievalLogRepository
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.search.logging import CompositeRetrievalLogger
from app.services.context_service import ContextService
from app.services.memory_service import MemoryService
from app.services.project_service import ProjectService
from app.services.retrieval_log_service import PersistentRetrievalLogger
from app.services.retrieval_service import RetrievalService
from app.services.search_service import SearchService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

INDEX_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 0.1
CHUNK_TOKENS = 300

# Each source owns one axis and one vocabulary, so a result can only reach the
# top on the evidence the test intended it to be found by.
SKILL_AXIS = 0
CONTEXT_AXIS = 1
MEMORY_AXIS = 2

SKILL_CONTENT = (
    "Keycloak issues OAuth2 tokens for every caller. The wallet:read scope is "
    "required by GET /api/v1/payments, and payment-service answers a missing "
    "scope with INSUFFICIENT_SCOPE."
)
PAYMENT_CONTEXT = (
    "Every charge request carries a UUID4 idempotency key. "
    "PaymentIdempotencyService keeps each key for a day so a repeated attempt "
    "is answered from the first stored outcome."
)
WALLET_CONTEXT = (
    "Balances are eventually consistent and are recomputed from the ledger. "
    "Nothing downstream may cache one for longer than a single response."
)
GLOBAL_MEMORY = "Use strict typing in every Python project, checked with mypy in strict mode."
PAYMENT_MEMORY = "Retries stop after three attempts and raise the failure to a human operator."


class AxisEmbedder:
    """Embed documents and queries as fixed basis vectors.

    Stored text is embedded on the axis its source owns, and a query on
    whichever axis a test points it at, so cosine similarity is known before
    the search runs.
    """

    def __init__(self, settings: Settings, *, document_axis: int, query_axis: int) -> None:
        self._settings = settings
        self._document_axis = document_axis
        self._query_axis = query_axis

    def _vector(self, axis: int) -> EmbeddingVector:
        return EmbeddingVector(
            model=self._settings.embedding_model,
            dimensions=self._settings.embedding_dimensions,
            values=basis_vector(self._settings.embedding_dimensions, axis),
        )

    async def embed_query(self, text: str) -> EmbeddingVector:
        _ = text
        return self._vector(self._query_axis)

    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        return tuple(self._vector(self._document_axis) for _ in contents)


def basis_vector(dimensions: int, axis: int) -> tuple[float, ...]:
    """Return a unit vector pointing along one axis."""
    return tuple(1.0 if index == axis else 0.0 for index in range(dimensions))


@pytest.fixture
def run_id() -> str:
    """Return one identifier owned by this test's records."""
    return str(ObjectId())[-8:]


@asynccontextmanager
async def unified_database(
    *,
    project_ids: Sequence[str],
    skill_ids: Sequence[str],
    sources: Sequence[str],
) -> AsyncIterator[MongoDBClient]:
    """Connect, build all six indexes, and remove only this test's records."""
    settings = get_settings()
    mongodb = MongoDBClient(settings)
    await mongodb.connect()
    dimensions = settings.embedding_dimensions
    try:
        await initialize_database(mongodb.database)
        await ensure_vector_indexes(mongodb.database, dimensions=dimensions)
        await ensure_text_indexes(mongodb.database)
        await ensure_context_vector_index(mongodb.database, dimensions=dimensions)
        await ensure_context_text_index(mongodb.database)
        await ensure_memory_vector_index(mongodb.database, dimensions=dimensions)
        await ensure_memory_text_index(mongodb.database)
        await wait_for_queryable_indexes(mongodb)
        yield mongodb
    finally:
        owned = {"$in": list(project_ids)}
        await mongodb.database["skill_chunks"].delete_many({"skill_id": {"$in": list(skill_ids)}})
        await mongodb.database["skills"].delete_many({"skill_id": {"$in": list(skill_ids)}})
        await mongodb.database["project_context"].delete_many({"project_id": owned})
        await mongodb.database["memories"].delete_many({"source": {"$in": list(sources)}})
        await mongodb.database["retrieval_logs"].delete_many({"project_id": owned})
        await mongodb.database["projects"].delete_many({"project_id": owned})
        await mongodb.close()


async def wait_for_queryable_indexes(mongodb: MongoDBClient) -> None:
    """Wait until mongot reports every retrievable index as queryable."""
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    while True:
        described = [
            await describe_vector_index(mongodb.database),
            await describe_text_index(mongodb.database),
            await describe_context_vector_index(mongodb.database),
            await describe_context_text_index(mongodb.database),
            await describe_memory_vector_index(mongodb.database),
            await describe_memory_text_index(mongodb.database),
        ]
        if all(status.ready for status in described):
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("the six search indexes did not become queryable")
        await asyncio.sleep(POLL_SECONDS)


async def wait_for_indexed_knowledge(
    search: SearchService,
    project_id: str,
    skill_id: str,
) -> None:
    """Wait until every fixture source and the fixture skill are reachable.

    Each collection is indexed independently, so waiting on one would leave
    another half of the unified answer coming from an empty index.

    Waiting on the sources alone is not enough, and the difference is not
    cosmetic. A document reached by the lexical index and not yet by the vector
    one still appears, so every source can be present while the vector half is
    still catching up — and it scores differently once it arrives, because a
    vector search that misses a document contributes nothing for it rather than
    the 0.5 an orthogonal one earns. Two identical queries either side of that
    moment return the same documents with different scores. So this waits until
    both the fixture skill and the payment context are found by vector search.
    Unrelated global skills may legitimately use an older embedding model in a
    persistent integration database and must not make this fixture wait forever.
    """
    expected_skill = f"skill_chunk:{skill_id}:1.0.0:chunk-0000"
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    while True:
        result = await search.search_knowledge(
            "idempotency retries typing scope",
            project_id=project_id,
            limit=20,
        )
        items = result.context.items
        found = {item.source for item in items}
        fixture_skill = next((item for item in items if item.key == expected_skill), None)
        skill_is_ready = fixture_skill is not None and "vector" in fixture_skill.explanation.methods
        fixture_context = next(
            (
                item
                for item in items
                if item.source == "project_context" and item.title == "Payment idempotency strategy"
            ),
            None,
        )
        context_is_ready = (
            fixture_context is not None and "vector" in fixture_context.explanation.methods
        )
        sources_ready = found == {"project_context", "active_skill", "memory"}
        if sources_ready and skill_is_ready and context_is_ready:
            return
        if asyncio.get_running_loop().time() >= deadline:
            lagging = []
            if not skill_is_ready:
                lagging.append(expected_skill)
            if not context_is_ready:
                lagging.append("project_context:Payment idempotency strategy")
            raise AssertionError(
                f"indexing did not catch up: reached {sorted(found)}, "
                f"still not in the vector index: {sorted(lagging)}"
            )
        await asyncio.sleep(POLL_SECONDS)


def skill_document(skill_id: str, run_id: str) -> SkillDocument:
    """Build one active skill version owned by this test run."""
    return SkillDocument(
        skill_id=skill_id,
        name=f"Phase 11 {skill_id}",
        version="1.0.0",
        description="Unified search integration coverage.",
        category=f"{run_id}-security",
        tags=[run_id, "auth"],
        trust_level="trusted",
        status="active",
        source_path=f"skills/{skill_id}.md",
        content_hash="e" * 64,
    )


def chunk_document(settings: Settings, skill: SkillDocument) -> SkillChunkDocument:
    """Build one chunk whose vector points along the skill axis."""
    return SkillChunkDocument(
        skill_id=skill.skill_id,
        skill_version=skill.version,
        chunk_id="chunk-0000",
        content=SKILL_CONTENT,
        section="Rules",
        chunk_index=0,
        tokens=CHUNK_TOKENS,
        embedding=list(basis_vector(settings.embedding_dimensions, SKILL_AXIS)),
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
        embedding_generated_at=datetime.now(UTC),
    )


def build_search(
    mongodb: MongoDBClient,
    settings: Settings,
    *,
    query_axis: int,
    store_queries: bool = False,
) -> SearchService:
    """Compose the unified search over every live source."""
    embedder = AxisEmbedder(settings, document_axis=query_axis, query_axis=query_axis)
    retrieval = RetrievalService(
        MongoSkillRepository(mongodb.database),
        MongoSkillChunkRepository(mongodb.database),
        embedder,
        contexts=lambda project_id: MongoProjectContextRepository(mongodb.database, project_id),
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
        clock=lambda: datetime.now(UTC),
    )
    logger = (
        CompositeRetrievalLogger(
            PersistentRetrievalLogger(
                lambda project_id: MongoRetrievalLogRepository(
                    mongodb.database,
                    project_id=project_id,
                )
            )
        )
        if store_queries
        else None
    )
    return SearchService(retrieval, logger=logger)


@asynccontextmanager
async def seeded_knowledge(run_id: str) -> AsyncIterator[tuple[MongoDBClient, str, str]]:
    """Install one skill, two projects' context, and two scopes of memory."""
    settings = get_settings()
    payment_id = f"phase11-payment-{run_id}"
    wallet_id = f"phase11-wallet-{run_id}"
    skill_id = f"phase11-auth-{run_id}"
    source = f"phase11-{run_id}"

    async with unified_database(
        project_ids=[payment_id, wallet_id],
        skill_ids=[skill_id],
        sources=[source],
    ) as mongodb:
        skill = skill_document(skill_id, run_id)
        await MongoSkillRepository(mongodb.database).create(skill)
        await MongoSkillChunkRepository(mongodb.database).create(chunk_document(settings, skill))

        projects = ProjectService(MongoProjectRepository(mongodb.database))
        for project_id in (payment_id, wallet_id):
            await projects.create_project(
                project_id=project_id,
                name=project_id,
                description="Phase 11 unified search coverage",
                repository=f"git@example.invalid:sampleco/{project_id}.git",
                environment="production",
            )

        contexts = ContextService(
            MongoProjectRepository(mongodb.database),
            lambda project_id: MongoProjectContextRepository(mongodb.database, project_id),
            AxisEmbedder(settings, document_axis=CONTEXT_AXIS, query_axis=CONTEXT_AXIS),
        )
        await contexts.create_context(
            project_id=payment_id,
            context_type="architecture_decision",
            title="Payment idempotency strategy",
            content=PAYMENT_CONTEXT,
            importance=0.5,
            source=source,
        )
        await contexts.create_context(
            project_id=wallet_id,
            context_type="architecture_decision",
            title="Wallet balance consistency",
            content=WALLET_CONTEXT,
            importance=0.5,
            source=source,
        )

        memories = MemoryService(
            MongoProjectRepository(mongodb.database),
            lambda scope, project_id, client_id: MongoMemoryRepository(
                mongodb.database,
                scope=scope,
                project_id=project_id,
                client_id=client_id,
            ),
            AxisEmbedder(settings, document_axis=MEMORY_AXIS, query_axis=MEMORY_AXIS),
        )
        await memories.create_memory(
            memory_type="preference",
            content=GLOBAL_MEMORY,
            summary="Strict typing everywhere",
            importance=0.5,
            confidence=0.9,
            source=source,
        )
        await memories.create_memory(
            memory_type="convention",
            content=PAYMENT_MEMORY,
            importance=0.5,
            confidence=0.9,
            source=source,
            project_id=payment_id,
        )

        search = build_search(mongodb, settings, query_axis=CONTEXT_AXIS)
        await wait_for_indexed_knowledge(search, payment_id, skill_id)
        yield mongodb, payment_id, wallet_id


@pytest.mark.asyncio
async def test_one_query_answers_from_every_source_at_once(run_id: str) -> None:
    settings = get_settings()

    async with seeded_knowledge(run_id) as (mongodb, payment_id, _):
        search = build_search(mongodb, settings, query_axis=CONTEXT_AXIS)
        result = await search.search_knowledge(
            "idempotency retries typing scope",
            project_id=payment_id,
            limit=20,
        )

    sources = [item.source for item in result.context.items]
    assert set(sources) == {"project_context", "active_skill", "memory"}
    assert result.trace.searched_sources == ("project_context", "active_skill", "memory")
    assert result.trace.skipped_sources == ()
    assert result.trace.retrieval_method == "hybrid"
    # Ranked together, not concatenated per source.
    assert [item.score for item in result.context.items] == sorted(
        (item.score for item in result.context.items),
        reverse=True,
    )
    assert result.context.token_count <= result.context.token_budget


@pytest.mark.asyncio
async def test_a_semantic_query_reaches_what_no_word_of_it_matches(run_id: str) -> None:
    settings = get_settings()

    async with seeded_knowledge(run_id) as (mongodb, payment_id, _):
        search = build_search(mongodb, settings, query_axis=CONTEXT_AXIS)
        result = await search.search_knowledge(
            "preventing duplicated withdrawals",
            project_id=payment_id,
            limit=10,
        )

    top = result.context.items[0]
    # Not one of those words appears in the stored context, so lexical search
    # cannot reach it at all and the vector half is the only evidence there is.
    assert top.source == "project_context"
    assert top.title == "Payment idempotency strategy"
    assert top.explanation.methods == ("vector",)
    assert top.explanation.vector == 1.0
    assert top.explanation.lexical == 0.0


@pytest.mark.asyncio
async def test_an_exact_identifier_reaches_what_no_vector_points_at(run_id: str) -> None:
    settings = get_settings()

    async with seeded_knowledge(run_id) as (mongodb, payment_id, _):
        # The query vector points away from the skill chunk, so only the exact
        # identifier can lift it to the top.
        search = build_search(mongodb, settings, query_axis=CONTEXT_AXIS)
        results = [
            await search.search_knowledge(term, project_id=payment_id, limit=10)
            for term in ("INSUFFICIENT_SCOPE", "wallet:read", "/api/v1/payments", "OAuth2")
        ]

    for term, result in zip(
        ("INSUFFICIENT_SCOPE", "wallet:read", "/api/v1/payments", "OAuth2"),
        results,
        strict=True,
    ):
        top = result.context.items[0]
        assert top.source == "active_skill", f"{term} did not retrieve the skill chunk"
        assert top.explanation.lexical == 1.0
        assert term.casefold() in top.content.casefold()


@pytest.mark.asyncio
async def test_no_query_reaches_another_project(run_id: str) -> None:
    settings = get_settings()

    async with seeded_knowledge(run_id) as (mongodb, payment_id, wallet_id):
        search = build_search(mongodb, settings, query_axis=CONTEXT_AXIS)
        scoped = await search.search_knowledge(
            "balances ledger consistency idempotency retries",
            project_id=payment_id,
            limit=20,
        )
        unscoped = await search.search_knowledge(
            "balances ledger consistency idempotency retries",
            limit=20,
        )
        wallet = await search.search_knowledge(
            "balances ledger consistency idempotency retries",
            project_id=wallet_id,
            limit=20,
        )

    owners = {item.metadata.get("project_id") for item in scoped.context.items}
    assert owners <= {None, payment_id}
    assert WALLET_CONTEXT not in [item.content for item in scoped.context.items]

    # A request naming no project reaches global knowledge only.
    assert {item.metadata.get("project_id") for item in unscoped.context.items} == {None}
    assert unscoped.trace.skipped_sources == ("project_context",)

    # The wallet project reaches its own context, which proves the payment
    # request was missing it by rule rather than because it was unindexed.
    assert WALLET_CONTEXT in [item.content for item in wallet.context.items]


@pytest.mark.asyncio
async def test_the_token_budget_bounds_what_comes_back(run_id: str) -> None:
    settings = get_settings()

    async with seeded_knowledge(run_id) as (mongodb, payment_id, _):
        search = build_search(mongodb, settings, query_axis=CONTEXT_AXIS)
        generous = await search.search_knowledge(
            "idempotency retries typing scope",
            project_id=payment_id,
            limit=20,
        )
        tight = await search.search_knowledge(
            "idempotency retries typing scope",
            project_id=payment_id,
            limit=20,
            token_budget=100,
        )

    assert tight.context.token_budget == 100
    assert tight.context.token_count <= 100
    assert len(tight.context.items) < len(generous.context.items)
    assert tight.context.diagnostics.dropped_for_budget >= 1
    # Nothing is cut short to fit: a smaller budget removes whole results and
    # leaves the ones it keeps exactly as the larger budget returned them.
    kept = {item.key: item.content for item in generous.context.items}
    for item in tight.context.items:
        assert item.content == kept[item.key]


@pytest.mark.asyncio
async def test_sources_can_be_restricted_to_one_kind_of_knowledge(run_id: str) -> None:
    settings = get_settings()

    async with seeded_knowledge(run_id) as (mongodb, payment_id, _):
        search = build_search(mongodb, settings, query_axis=CONTEXT_AXIS)
        memories = await search.search_knowledge(
            "idempotency retries typing scope",
            project_id=payment_id,
            sources=["memory"],
            limit=20,
        )
        conversations = await search.search_knowledge(
            "idempotency retries typing scope",
            project_id=payment_id,
            sources=["conversation"],
            limit=20,
        )

    assert {item.source for item in memories.context.items} == {"memory"}
    assert memories.trace.searched_sources == ("memory",)
    # A conversation is stored but carries no index, so asking for one is
    # answered rather than silently returning nothing.
    assert conversations.context.items == ()
    assert conversations.trace.skipped_sources == ("conversation",)


@pytest.mark.asyncio
async def test_repeating_a_query_returns_identical_results(run_id: str) -> None:
    settings = get_settings()

    async with seeded_knowledge(run_id) as (mongodb, payment_id, _):
        search = build_search(mongodb, settings, query_axis=CONTEXT_AXIS)
        first = await search.search_knowledge(
            "idempotency retries typing scope",
            project_id=payment_id,
            limit=20,
        )
        second = await search.search_knowledge(
            "idempotency retries typing scope",
            project_id=payment_id,
            limit=20,
        )

    assert [(item.key, item.score) for item in first.context.items] == [
        (item.key, item.score) for item in second.context.items
    ]
    assert first.trace.query_id != second.trace.query_id


@pytest.mark.asyncio
async def test_an_opted_in_deployment_stores_what_each_search_did(run_id: str) -> None:
    settings = get_settings()

    async with seeded_knowledge(run_id) as (mongodb, payment_id, _):
        search = build_search(mongodb, settings, query_axis=CONTEXT_AXIS, store_queries=True)
        result = await search.search_knowledge(
            "idempotency retries typing scope",
            project_id=payment_id,
            limit=20,
        )
        stored = await MongoRetrievalLogRepository(
            mongodb.database,
            project_id=payment_id,
        ).list(limit=10)

    recorded = [item for item in stored.items if item.query == "idempotency retries typing scope"]
    assert len(recorded) == 1
    entry = recorded[0]
    assert entry.project_id == payment_id
    assert entry.retrieval_method == "hybrid"
    assert entry.result_count == len(result.context.items)
    assert entry.selected_result_ids == list(result.result_keys)
    assert entry.latency_ms > 0.0
