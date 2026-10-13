"""Live MongoDB Search checks through mongod and self-managed mongot."""

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest
from bson import ObjectId

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.db.text_indexes import SKILL_CHUNK_TEXT_INDEX, describe_text_index, ensure_text_indexes
from app.models.skill import LifecycleStatus, SkillDocument, SkillTrustLevel
from app.models.skill_chunk import SkillChunkDocument
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.services.retrieval_service import RetrievalService
from app.skills.types import SkillChunkSearchResult

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

INDEX_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 0.1

SECURITY_CONTENT = (
    "Keycloak issues OAuth2 tokens for every caller. The wallet:read scope is "
    "required by GET /api/v1/payments, and payment-service answers a missing "
    "scope with INSUFFICIENT_SCOPE."
)
DATA_CONTENT = (
    "PostgreSQL stores the outbox table that the Saga coordinator drains. Redis "
    "caches idempotency keys, and PaymentIdempotencyService rejects replays."
)
RUNTIME_CONTENT = (
    "FastAPI declares dependencies per route. Set MONGODB_REPLICA_SET before "
    "the application starts so the driver can discover the primary."
)


@pytest.fixture(scope="module")
def run_id() -> str:
    """Return one identifier owned by this test module's records."""
    return str(ObjectId())


def skill_document(
    skill_id: str,
    *,
    version: str = "1.0.0",
    category: str,
    tags: list[str],
    trust_level: SkillTrustLevel,
    status: LifecycleStatus = "active",
) -> SkillDocument:
    """Build one skill version owned by this test run."""
    return SkillDocument(
        skill_id=skill_id,
        name=f"Phase 7 {skill_id}",
        version=version,
        description="Lexical search integration coverage.",
        category=category,
        tags=tags,
        trust_level=trust_level,
        status=status,
        source_path=f"skills/{skill_id}.md",
        content_hash="c" * 64,
    )


def chunk_document(skill: SkillDocument, content: str) -> SkillChunkDocument:
    """Build one chunk with no vector, which lexical search does not need."""
    return SkillChunkDocument(
        skill_id=skill.skill_id,
        skill_version=skill.version,
        chunk_id="chunk-0000",
        content=content,
        section="Rules",
        chunk_index=0,
        tokens=32,
    )


@asynccontextmanager
async def lexical_database(skill_ids: Sequence[str]) -> AsyncIterator[MongoDBClient]:
    """Connect, build the Search index, and remove only this test's records."""
    mongodb = MongoDBClient(get_settings())
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        await ensure_text_indexes(mongodb.database)
        await wait_for_queryable_index(mongodb)
        yield mongodb
    finally:
        await mongodb.database["skill_chunks"].delete_many({"skill_id": {"$in": list(skill_ids)}})
        await mongodb.database["skills"].delete_many({"skill_id": {"$in": list(skill_ids)}})
        await mongodb.close()


async def wait_for_queryable_index(mongodb: MongoDBClient) -> None:
    """Wait until mongot reports the Search index as queryable."""
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    while True:
        status = await describe_text_index(mongodb.database)
        if status.ready:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"{SKILL_CHUNK_TEXT_INDEX} did not become queryable")
        await asyncio.sleep(POLL_SECONDS)


async def wait_for_indexed_chunks(
    service: RetrievalService,
    query: str,
    *,
    expected: int,
) -> tuple[SkillChunkSearchResult, ...]:
    """Wait until mongot has indexed the chunks this test just inserted."""
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    while True:
        results = await service.lexical_search(query, limit=10)
        if len(results) >= expected:
            return results
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"expected {expected} results for {query!r}, found {len(results)}")
        await asyncio.sleep(POLL_SECONDS)


@asynccontextmanager
async def lexical_service(run_id: str) -> AsyncIterator[RetrievalService]:
    """Install three skills covering the documented technical vocabulary."""
    security_id = f"phase7-security-{run_id}"
    data_id = f"phase7-data-{run_id}"
    runtime_id = f"phase7-runtime-{run_id}"
    security = skill_document(
        security_id,
        category="phase7-security",
        tags=["phase7", "auth"],
        trust_level="trusted",
    )
    data = skill_document(
        data_id,
        category="phase7-data",
        tags=["phase7", "data"],
        trust_level="external",
    )
    runtime = skill_document(
        runtime_id,
        category="phase7-backend",
        tags=["phase7", "backend"],
        trust_level="trusted",
    )

    async with lexical_database([security_id, data_id, runtime_id]) as mongodb:
        skills = MongoSkillRepository(mongodb.database)
        chunks = MongoSkillChunkRepository(mongodb.database)
        for document, content in (
            (security, SECURITY_CONTENT),
            (data, DATA_CONTENT),
            (runtime, RUNTIME_CONTENT),
        ):
            await skills.create(document)
            await chunks.create(chunk_document(document, content))
        # Lexical retrieval needs no embedding model.
        service = RetrievalService(skills, chunks)
        await wait_for_indexed_chunks(service, "Keycloak", expected=1)
        yield service


@pytest.mark.asyncio
async def test_exact_technical_identifiers_are_retrievable(run_id: str) -> None:
    expectations = {
        "Keycloak": "security",
        "OAuth2": "security",
        "wallet:read": "security",
        "INSUFFICIENT_SCOPE": "security",
        "/api/v1/payments": "security",
        "payment-service": "security",
        "PostgreSQL": "data",
        "Redis": "data",
        "Saga": "data",
        "Outbox": "data",
        "PaymentIdempotencyService": "data",
        "FastAPI": "runtime",
        "MONGODB_REPLICA_SET": "runtime",
    }

    async with lexical_service(run_id) as service:
        for query, owner in expectations.items():
            results = await service.lexical_search(query, limit=5)
            assert results, f"{query!r} returned no results"
            top = results[0]
            assert top.skill.skill_id == f"phase7-{owner}-{run_id}", query
            assert top.score > 0.0, query
            assert top.method == "lexical"


@pytest.mark.asyncio
async def test_identifier_matching_ignores_case_but_keeps_structure(run_id: str) -> None:
    async with lexical_service(run_id) as service:
        lowercase = await service.lexical_search("keycloak", limit=5)
        uppercase = await service.lexical_search("KEYCLOAK", limit=5)
        scope = await service.lexical_search("WALLET:READ", limit=5)
        unrelated = await service.lexical_search("wallet:write", limit=5)

    assert lowercase and uppercase
    assert lowercase[0].skill.skill_id == uppercase[0].skill.skill_id
    assert scope and "wallet:read" in scope[0].chunk.content
    assert all("wallet:write" not in result.chunk.content for result in unrelated)


@pytest.mark.asyncio
async def test_phrase_search_requires_the_words_in_order(run_id: str) -> None:
    async with lexical_service(run_id) as service:
        ordered = await service.lexical_search("outbox table", limit=5, phrase=True)
        reversed_words = await service.lexical_search("table outbox", limit=5, phrase=True)
        as_terms = await service.lexical_search("table outbox", limit=5)

    assert [result.skill.category for result in ordered] == ["phase7-data"]
    assert reversed_words == ()
    assert as_terms, "without the phrase flag the same words still match"


@pytest.mark.asyncio
async def test_metadata_filters_scope_lexical_results(run_id: str) -> None:
    security_id = f"phase7-security-{run_id}"
    data_id = f"phase7-data-{run_id}"

    async with lexical_service(run_id) as service:
        by_category = await service.lexical_search("scope", category="phase7-security")
        by_tag = await service.lexical_search("idempotency keys", tags=["data"])
        by_trust = await service.lexical_search(
            "Keycloak OAuth2 PostgreSQL", trust_level="external"
        )
        by_skill = await service.lexical_search("OAuth2", skill_id=security_id)
        by_version = await service.lexical_search(
            "OAuth2",
            skill_id=security_id,
            skill_version="1.0.0",
        )
        excluded = await service.lexical_search("Keycloak", category="phase7-data")
        absent_version = await service.lexical_search(
            "OAuth2",
            skill_id=security_id,
            skill_version="9.9.9",
        )

    assert [result.skill.skill_id for result in by_category] == [security_id]
    assert [result.skill.skill_id for result in by_tag] == [data_id]
    assert [result.skill.skill_id for result in by_trust] == [data_id]
    assert [result.skill.skill_id for result in by_skill] == [security_id]
    assert [result.skill.skill_id for result in by_version] == [security_id]
    assert excluded == ()
    assert absent_version == ()


@pytest.mark.asyncio
async def test_queries_that_match_nothing_return_no_results(run_id: str) -> None:
    async with lexical_service(run_id) as service:
        missing = await service.lexical_search("Kubernetes", limit=5)
        punctuation = await service.lexical_search("!!! ???", limit=5)

    assert missing == ()
    assert punctuation == ()


@pytest.mark.asyncio
async def test_results_are_scored_bounded_and_ordered(run_id: str) -> None:
    async with lexical_service(run_id) as service:
        results = await service.lexical_search("Keycloak OAuth2 PostgreSQL Redis FastAPI", limit=2)
        scores = [result.score for result in results]

    assert len(results) == 2
    assert scores == sorted(scores, reverse=True)
    assert all(score > 0.0 for score in scores)


@pytest.mark.asyncio
async def test_the_search_index_reconciles_idempotently() -> None:
    mongodb = MongoDBClient(get_settings())
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        first = await ensure_text_indexes(mongodb.database)
        repeated = await ensure_text_indexes(mongodb.database)
        status = await describe_text_index(mongodb.database)
    finally:
        await mongodb.close()

    assert first in {"created", "unchanged", "updated"}
    assert repeated == "unchanged"
    assert status.exists is True
    assert status.analyzer == "lucene.standard"
