"""Real-MongoDB V3 Phase 10 controlled-promotion persistence boundary tests."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from bson import ObjectId
from v6_legacy_integration import initialize_database, search_indexes_ready, seed_source

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.context_indexes import ensure_context_text_index
from app.db.memory_indexes import ensure_memory_text_index
from app.models.research import (
    CandidateState,
    CandidateType,
    CitationLocator,
    CitationRelation,
    ClaimState,
    ResearchAuthorization,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
)
from app.repositories.context_repository import MongoProjectContextRepository
from app.repositories.memory_repository import MongoMemoryRepository
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.research_repository import (
    MongoKnowledgeCandidateRepository,
    MongoPromotionEventRepository,
    MongoResearchCitationRepository,
    MongoResearchClaimRepository,
    MongoResearchProjectRepository,
)
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.services.context_service import ContextService
from app.services.knowledge_candidate_service import KnowledgeCandidateService
from app.services.memory_service import MemoryService
from app.services.retrieval_service import RetrievalService
from tests.project_doubles import project as v1_project

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]


@asynccontextmanager
async def initialized_mongodb() -> AsyncIterator[MongoDBClient]:
    mongodb = MongoDBClient(get_settings())
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        await ensure_context_text_index(mongodb.database)
        await ensure_memory_text_index(mongodb.database)
        await search_indexes_ready(mongodb.database, ("project_context", "memories"), 2)
        yield mongodb
    finally:
        await mongodb.close()


async def wait_for_promoted_v1_search(
    retrieval: RetrievalService,
    *,
    project_id: str,
    memory_id: ObjectId,
    context_id: ObjectId,
) -> None:
    """Wait for mongot's asynchronous lexical index to observe the V1 writes."""
    deadline = asyncio.get_running_loop().time() + 15.0
    while True:
        memory_results = await retrieval.memory_search("idempotency", project_id=project_id)
        context_results = await retrieval.context_search("idempotency", project_id=project_id)
        if any(result.memory.id == memory_id for result in memory_results) and any(
            result.context.id == context_id for result in context_results
        ):
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("promoted V1 memory/context were not searchable before timeout")
        await asyncio.sleep(0.25)


@pytest.mark.asyncio
async def test_approved_candidates_promote_to_v1_memory_and_context_with_provenance() -> None:
    suffix = str(ObjectId())
    research_id = f"phase10-evidence-{suffix}"
    project_id = f"phase10-payments-{suffix}"
    grant = ResearchAuthorization(project_ids=[project_id], owner_key_id="writer-key")
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            projects = MongoProjectRepository(database)
            await projects.create(v1_project(project_id))
            research_projects = MongoResearchProjectRepository(database)
            await research_projects.create(
                ResearchProjectDocument(
                    research_project_id=research_id,
                    name="Phase 10 retry evidence",
                    question="What approved retry evidence can become V1 knowledge?",
                    type="technical_research",
                    related_project_ids=[project_id],
                    authorization=grant,
                )
            )
            claims = MongoResearchClaimRepository(database, research_id)
            await claims.create(
                ResearchClaimDocument(
                    research_project_id=research_id,
                    claim_id="retry-key",
                    statement="Retryable writes require an idempotency key.",
                    status=ClaimState.SUPPORTED,
                    confidence=0.9,
                    confidence_rationale="An explicit reviewer assessed retained evidence.",
                    authorization=grant,
                )
            )
            await seed_source(
                database,
                research_project_id=research_id,
                source_id="provider-docs",
                snapshot_id="provider-v1",
                authorization=grant,
                document_id="provider-document",
                chunk_id="provider-chunk",
            )
            citations = MongoResearchCitationRepository(database, research_id)
            await citations.create(
                ResearchCitationDocument(
                    research_project_id=research_id,
                    claim_id="retry-key",
                    citation_id="provider-support",
                    source_id="provider-docs",
                    snapshot_id="provider-v1",
                    chunk_id="provider-chunk",
                    source_class="official_documentation",
                    locator=CitationLocator(url="https://docs.example.test/retries", page=2),
                    evidence_excerpt="Retries require an idempotency key.",
                    relation=CitationRelation.SUPPORTS,
                    authorization=grant,
                )
            )
            memories = MemoryService(
                projects,
                lambda scope, project_id, client_id: MongoMemoryRepository(
                    database,
                    scope=scope,
                    project_id=project_id,
                    client_id=client_id,
                ),
                None,
            )
            contexts = ContextService(
                projects, lambda scope: MongoProjectContextRepository(database, scope), None
            )
            old_context = await contexts.create_context(
                project_id,
                context_type="implementation",
                title="Legacy retry note",
                content="The previous retry guidance was incomplete.",
                importance=0.4,
                source="user",
            )
            service = KnowledgeCandidateService(
                research_projects,
                projects,
                lambda identifier: MongoResearchClaimRepository(database, identifier),
                lambda identifier: MongoResearchCitationRepository(database, identifier),
                lambda identifier: MongoKnowledgeCandidateRepository(database, identifier),
                lambda identifier: MongoPromotionEventRepository(database, identifier),
                memories,
                contexts,
                clock=lambda: datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
            )

            async def approve(candidate_id: str, candidate_type: CandidateType):
                proposed = await service.create_candidate(
                    research_project_id=research_id,
                    candidate_id=candidate_id,
                    candidate_type=candidate_type,
                    target_project_id=project_id,
                    target_skill_id=None,
                    current_skill_version=None,
                    title="Retry idempotency requirement",
                    content="Use an idempotency key for retryable provider writes.",
                    source_claim_ids=["retry-key"],
                    source_citation_ids=["provider-support"],
                    confidence=0.9,
                    confidence_rationale="The source claim is explicitly supported.",
                    created_by="writer-key",
                    allowed_project_ids=(project_id,),
                )
                return (
                    await service.review_candidate(
                        research_project_id=research_id,
                        candidate_id=proposed.candidate_id,
                        expected_revision=proposed.revision,
                        decision=CandidateState.APPROVED,
                        reviewer="reviewer-key",
                        reason="Evidence reviewed.",
                        allowed_project_ids=(project_id,),
                    )
                ).candidate

            memory_candidate = await approve("retry-memory", CandidateType.MEMORY)
            context_candidate = await approve("retry-context", CandidateType.PROJECT_CONTEXT)
            memory_promotion = await service.promote_candidate(
                research_project_id=research_id,
                candidate_id=memory_candidate.candidate_id,
                expected_revision=memory_candidate.revision,
                reviewer="promoter-key",
                allowed_project_ids=(project_id,),
            )
            context_promotion = await service.promote_candidate(
                research_project_id=research_id,
                candidate_id=context_candidate.candidate_id,
                expected_revision=context_candidate.revision,
                reviewer="promoter-key",
                allowed_project_ids=(project_id,),
                supersedes_context_id=str(old_context.id),
            )

            stored_memory = await memories.get_memory(
                ObjectId(memory_promotion.target_id), project_id=project_id
            )
            stored_context = await contexts.get_context(
                project_id, ObjectId(context_promotion.target_id)
            )
            superseded_context = await contexts.get_context(project_id, old_context.id)
            assert stored_memory.promotion_provenance is not None
            assert stored_context.promotion_provenance is not None
            assert stored_memory.promotion_provenance.source_ids == ["provider-docs"]
            assert stored_context.promotion_provenance.citation_ids == ["provider-support"]
            assert superseded_context.status == "superseded"
            retrieval = RetrievalService(
                MongoSkillRepository(database),
                MongoSkillChunkRepository(database),
                None,
                contexts=lambda scope: MongoProjectContextRepository(database, scope),
                memories=lambda project_id, client_id: MongoMemoryRepository(
                    database,
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
            await wait_for_promoted_v1_search(
                retrieval,
                project_id=project_id,
                memory_id=stored_memory.id,
                context_id=stored_context.id,
            )
            assert memory_promotion.event.decision is CandidateState.PROMOTED
            assert context_promotion.event.decision is CandidateState.PROMOTED
            assert (
                await database["promotion_events"].count_documents(
                    {"research_project_id": research_id}
                )
                == 4
            )
    finally:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            for collection_name in (
                "promotion_events",
                "knowledge_candidates",
                "research_citations",
                "research_claims",
                "research_projects",
            ):
                await database[collection_name].delete_many({"research_project_id": research_id})
            await database["memories"].delete_many({"project_id": project_id})
            await database["project_context"].delete_many({"project_id": project_id})
            await database["projects"].delete_many({"project_id": project_id})
