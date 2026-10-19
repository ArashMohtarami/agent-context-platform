"""Real-MongoDB V3 Phase 9 candidate review and non-promotion boundary test."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from bson import ObjectId
from v6_legacy_integration import initialize_database, seed_source

from app.core.config import get_settings
from app.db.client import MongoDBClient
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
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.research_repository import (
    MongoKnowledgeCandidateRepository,
    MongoPromotionEventRepository,
    MongoResearchCitationRepository,
    MongoResearchClaimRepository,
    MongoResearchProjectRepository,
)
from app.services.knowledge_candidate_service import KnowledgeCandidateService
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
        yield mongodb
    finally:
        await mongodb.close()


@pytest.mark.asyncio
async def test_candidate_approval_persists_event_without_mutating_v1_memory_or_context() -> None:
    suffix = str(ObjectId())
    research_id = f"phase9-retry-evidence-{suffix}"
    project_id = f"phase9-payments-{suffix}"
    grant = ResearchAuthorization(project_ids=[project_id], owner_key_id="writer-key")
    memory_before = context_before = 0
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            await MongoProjectRepository(database).create(v1_project(project_id))
            research_projects = MongoResearchProjectRepository(database)
            await research_projects.create(
                ResearchProjectDocument(
                    research_project_id=research_id,
                    name="Phase 9 retry evidence",
                    question="What should a reviewer propose from retained retry evidence?",
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
                    confidence_rationale="A reviewer assessed retained evidence.",
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
                    evidence_excerpt="Retryable writes require an idempotency key.",
                    relation=CitationRelation.SUPPORTS,
                    authorization=grant,
                )
            )
            memory_before = await database["memories"].count_documents({"project_id": project_id})
            context_before = await database["project_context"].count_documents(
                {"project_id": project_id}
            )
            service = KnowledgeCandidateService(
                research_projects,
                MongoProjectRepository(database),
                lambda identifier: MongoResearchClaimRepository(database, identifier),
                lambda identifier: MongoResearchCitationRepository(database, identifier),
                lambda identifier: MongoKnowledgeCandidateRepository(database, identifier),
                lambda identifier: MongoPromotionEventRepository(database, identifier),
                clock=lambda: datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
            )

            proposed = await service.create_candidate(
                research_project_id=research_id,
                candidate_id="retry-context",
                candidate_type=CandidateType.PROJECT_CONTEXT,
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
            reviewed = await service.review_candidate(
                research_project_id=research_id,
                candidate_id=proposed.candidate_id,
                expected_revision=proposed.revision,
                decision=CandidateState.APPROVED,
                reviewer="reviewer-key",
                reason="The proposal is ready for a later promotion workflow.",
                allowed_project_ids=(project_id,),
            )

            assert proposed.status is CandidateState.PENDING_REVIEW
            assert reviewed.candidate.status is CandidateState.APPROVED
            assert reviewed.candidate.source_claim_ids == ["retry-key"]
            assert reviewed.candidate.source_citation_ids == ["provider-support"]
            assert reviewed.event.decision is CandidateState.APPROVED
            assert (
                await database["promotion_events"].count_documents(
                    {"research_project_id": research_id, "candidate_id": proposed.candidate_id}
                )
                == 1
            )
            memory_after = await database["memories"].count_documents({"project_id": project_id})
            assert memory_after == memory_before
            assert (
                await database["project_context"].count_documents({"project_id": project_id})
                == context_before
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
