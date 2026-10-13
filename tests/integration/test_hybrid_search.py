"""Live hybrid retrieval checks against mongod and self-managed mongot."""

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.core.config import Settings, get_settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.db.text_indexes import describe_text_index, ensure_text_indexes
from app.db.vector_indexes import describe_vector_index, ensure_vector_indexes
from app.embeddings.types import EmbeddingVector
from app.models.skill import SkillDocument, SkillTrustLevel
from app.models.skill_chunk import SkillChunkDocument
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository
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
CHUNK_TOKENS = 300
SKILL_COUNT = 3

PAYMENT_CONTENT = (
    "Payment idempotency keys stop duplicate charges. PaymentIdempotencyService "
    "stores every key in the outbox table so a retried request is answered from "
    "the first result instead of charging the caller twice."
)
AUTH_CONTENT = (
    "Keycloak issues OAuth2 tokens for every caller. The wallet:read scope is "
    "required by GET /api/v1/payments, and payment-service answers a missing "
    "scope with INSUFFICIENT_SCOPE."
)
FRONTEND_CONTENT = (
    "Button spacing follows an eight point grid. Dialog headings use sentence "
    "case, and destructive actions always sit on the right of the footer row."
)


class AxisEmbedder:
    """Embed queries as fixed basis vectors so similarity is known in advance."""

    def __init__(self, settings: Settings, axis: int) -> None:
        self._settings = settings
        self._axis = axis

    async def embed_query(self, text: str) -> EmbeddingVector:
        _ = text
        return EmbeddingVector(
            model=self._settings.embedding_model,
            dimensions=self._settings.embedding_dimensions,
            values=basis_vector(self._settings.embedding_dimensions, self._axis),
        )


def basis_vector(dimensions: int, axis: int) -> tuple[float, ...]:
    """Return a unit vector pointing along one axis."""
    return tuple(1.0 if index == axis else 0.0 for index in range(dimensions))


def skill_document(
    skill_id: str,
    *,
    category: str,
    tags: list[str],
    trust_level: SkillTrustLevel = "trusted",
) -> SkillDocument:
    """Build one active skill version owned by this test run."""
    return SkillDocument(
        skill_id=skill_id,
        name=f"Phase 8 {skill_id}",
        version="1.0.0",
        description="Hybrid retrieval integration coverage.",
        category=category,
        tags=tags,
        trust_level=trust_level,
        status="active",
        source_path=f"skills/{skill_id}.md",
        content_hash="d" * 64,
    )


def chunk_document(
    settings: Settings,
    skill: SkillDocument,
    *,
    axis: int,
    content: str,
) -> SkillChunkDocument:
    """Build one chunk whose vector points along a known axis."""
    return SkillChunkDocument(
        skill_id=skill.skill_id,
        skill_version=skill.version,
        chunk_id="chunk-0000",
        content=content,
        section="Rules",
        chunk_index=0,
        tokens=CHUNK_TOKENS,
        embedding=list(basis_vector(settings.embedding_dimensions, axis)),
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
        embedding_generated_at=datetime.now(UTC),
    )


@asynccontextmanager
async def hybrid_database(skill_ids: Sequence[str]) -> AsyncIterator[MongoDBClient]:
    """Connect, build both indexes, and remove only this test's records."""
    settings = get_settings()
    mongodb = MongoDBClient(settings)
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        await ensure_vector_indexes(mongodb.database, dimensions=settings.embedding_dimensions)
        await ensure_text_indexes(mongodb.database)
        await wait_for_queryable_indexes(mongodb)
        yield mongodb
    finally:
        await mongodb.database["skill_chunks"].delete_many({"skill_id": {"$in": list(skill_ids)}})
        await mongodb.database["skills"].delete_many({"skill_id": {"$in": list(skill_ids)}})
        await mongodb.close()


async def wait_for_queryable_indexes(mongodb: MongoDBClient) -> None:
    """Wait until mongot reports both indexes as queryable."""
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    while True:
        vector = await describe_vector_index(mongodb.database)
        text = await describe_text_index(mongodb.database)
        if vector.ready and text.ready:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("the vector and Search indexes did not become queryable")
        await asyncio.sleep(POLL_SECONDS)


async def wait_for_indexed_chunks(service: RetrievalService, run_tag: str) -> None:
    """Wait until both indexes have caught up with the inserted chunks.

    Vector and Search indexing complete independently, so waiting on one would
    leave the other half of a hybrid query answering from an empty index.
    """
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    lexical_queries = ("INSUFFICIENT_SCOPE", "wallet:read", "/api/v1/payments")
    while True:
        vector = await service.vector_search("payment idempotency", tags=[run_tag], limit=10)
        lexical = await asyncio.gather(
            *(service.lexical_search(query, tags=[run_tag], limit=10) for query in lexical_queries)
        )
        if len(vector) >= SKILL_COUNT and all(matches for matches in lexical):
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(
                "indexing did not catch up: "
                f"{len(vector)} vector, "
                f"{[len(matches) for matches in lexical]} lexical"
            )
        await asyncio.sleep(POLL_SECONDS)


@asynccontextmanager
async def hybrid_service(run_tag: str) -> AsyncIterator[RetrievalService]:
    """Install three skills whose vectors and vocabulary are known."""
    settings = get_settings()
    payment_id = f"{run_tag}-payment"
    auth_id = f"{run_tag}-auth"
    frontend_id = f"{run_tag}-frontend"
    payment = skill_document(payment_id, category=f"{run_tag}-backend", tags=[run_tag, "payments"])
    auth = skill_document(
        auth_id,
        category=f"{run_tag}-security",
        tags=[run_tag, "auth"],
        trust_level="external",
    )
    frontend = skill_document(frontend_id, category=f"{run_tag}-frontend", tags=[run_tag, "ui"])

    async with hybrid_database([payment_id, auth_id, frontend_id]) as mongodb:
        skills = MongoSkillRepository(mongodb.database)
        chunks = MongoSkillChunkRepository(mongodb.database)
        for document, axis, content in (
            (payment, 0, PAYMENT_CONTENT),
            (auth, 1, AUTH_CONTENT),
            (frontend, 2, FRONTEND_CONTENT),
        ):
            await skills.create(document)
            await chunks.create(chunk_document(settings, document, axis=axis, content=content))
        # The query vector always points at the payment chunk's axis, so the
        # other two chunks can only be retrieved on lexical evidence.
        service = RetrievalService(skills, chunks, AxisEmbedder(settings, axis=0))
        await wait_for_indexed_chunks(service, run_tag)
        yield service


@pytest.fixture(scope="module")
def run_tag() -> str:
    """Return the tag that scopes every record this module creates."""
    return f"phase8-{ObjectId()}"


@pytest.mark.asyncio
async def test_hybrid_retrieval_ranks_agreement_above_a_single_signal(run_tag: str) -> None:
    async with hybrid_service(run_tag) as service:
        context = await service.hybrid_search(
            "payment idempotency outbox",
            tags=[run_tag],
            limit=10,
        )

    keys = [str(item.metadata["skill_id"]) for item in context.items]
    scores = [item.score for item in context.items]

    # Both searches agree on the payment chunk: the query vector points at its
    # axis and its content carries the query's vocabulary.
    assert keys[0] == f"{run_tag}-payment"
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= score <= 1.0 for score in scores)
    assert context.items[0].explanation.methods == ("vector", "lexical")
    assert context.items[0].explanation.vector > 0.9
    assert context.items[0].explanation.lexical == 1.0
    assert context.diagnostics.methods == ("vector", "lexical")
    assert context.diagnostics.weights["vector"] == pytest.approx(0.45)


@pytest.mark.asyncio
async def test_exact_identifiers_outrank_a_closer_vector_match(run_tag: str) -> None:
    async with hybrid_service(run_tag) as service:
        scope = await service.hybrid_search("wallet:read", tags=[run_tag], limit=10)
        error = await service.hybrid_search("INSUFFICIENT_SCOPE", tags=[run_tag], limit=10)
        path = await service.hybrid_search("/api/v1/payments", tags=[run_tag], limit=10)

    # The query vector points at the payment chunk every time, so only lexical
    # evidence can lift the auth chunk above it.
    for context in (scope, error, path):
        assert context.items
        assert str(context.items[0].metadata["skill_id"]) == f"{run_tag}-auth"
        assert context.items[0].explanation.lexical == 1.0
        assert context.items[0].explanation.methods == ("vector", "lexical")


@pytest.mark.asyncio
async def test_metadata_filters_scope_hybrid_results(run_tag: str) -> None:
    payment_id = f"{run_tag}-payment"
    async with hybrid_service(run_tag) as service:
        by_category = await service.hybrid_search("payment", category=f"{run_tag}-backend")
        by_tag = await service.hybrid_search("payment idempotency", tags=[run_tag, "payments"])
        by_trust = await service.hybrid_search(
            "Keycloak OAuth2 payment",
            tags=[run_tag],
            trust_level="external",
        )
        by_skill = await service.hybrid_search("payment idempotency", skill_id=payment_id)
        excluded = await service.hybrid_search(
            "payment idempotency",
            category=f"{run_tag}-backend",
            trust_level="external",
        )

    assert [item.metadata["skill_id"] for item in by_category.items] == [payment_id]
    assert [item.metadata["skill_id"] for item in by_tag.items] == [payment_id]
    assert [item.metadata["skill_id"] for item in by_trust.items] == [f"{run_tag}-auth"]
    assert [item.metadata["skill_id"] for item in by_skill.items] == [payment_id]
    assert excluded.items == ()
    assert excluded.token_count == 0


@pytest.mark.asyncio
async def test_the_token_budget_bounds_the_returned_context(run_tag: str) -> None:
    async with hybrid_service(run_tag) as service:
        generous = await service.hybrid_search("payment Keycloak button", tags=[run_tag], limit=10)
        tight = await service.hybrid_search(
            "payment Keycloak button",
            tags=[run_tag],
            limit=10,
            token_budget=CHUNK_TOKENS + 1,
        )

    assert len(generous.items) == SKILL_COUNT
    assert generous.token_count == CHUNK_TOKENS * SKILL_COUNT
    assert generous.token_count <= generous.token_budget
    assert len(tight.items) == 1
    assert tight.token_count <= tight.token_budget
    assert tight.diagnostics.dropped_for_budget == SKILL_COUNT - 1


@pytest.mark.asyncio
async def test_project_scoped_retrieval_returns_global_skills_only(run_tag: str) -> None:
    async with hybrid_service(run_tag) as service:
        scoped = await service.hybrid_search(
            "payment idempotency",
            tags=[run_tag],
            project_id="sampleco-payment",
        )
        other = await service.hybrid_search(
            "payment idempotency",
            tags=[run_tag],
            project_id="sampleco-wallet",
        )

    assert scoped.project_id == "sampleco-payment"
    assert scoped.items
    # Skills carry no project, so neither request can reach the other's context.
    assert [item.metadata for item in scoped.items] == [item.metadata for item in other.items]
    assert all("project_id" not in item.metadata for item in scoped.items)


@pytest.mark.asyncio
async def test_hybrid_retrieval_is_deterministic_against_real_indexes(run_tag: str) -> None:
    async with hybrid_service(run_tag) as service:
        first = await service.hybrid_search("payment idempotency outbox", tags=[run_tag], limit=10)
        second = await service.hybrid_search("payment idempotency outbox", tags=[run_tag], limit=10)

    assert [item.score for item in first.items] == [item.score for item in second.items]
    assert [item.metadata for item in first.items] == [item.metadata for item in second.items]


@pytest.mark.asyncio
async def test_filters_matching_no_skill_return_an_empty_context(run_tag: str) -> None:
    async with hybrid_service(run_tag) as service:
        context = await service.hybrid_search("Kubernetes", category=f"{run_tag}-absent")

    assert context.items == ()
    assert context.token_count == 0
    assert context.diagnostics.merged == 0
