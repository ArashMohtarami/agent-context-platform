"""Real-MongoDB V3 Phase 8 brief persistence and provenance checks."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.models.research import (
    CitationLocator,
    CitationRelation,
    ClaimState,
    ResearchAuthorization,
    ResearchBriefStatus,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
    ResearchSourceDocument,
    ResearchSourceType,
    SourceClass,
    SourceSnapshotDocument,
)
from app.repositories.research_repository import (
    MongoResearchBriefRepository,
    MongoResearchCitationRepository,
    MongoResearchClaimRepository,
    MongoResearchProjectRepository,
    MongoResearchSourceRepository,
    MongoSourceSnapshotRepository,
)
from app.services.research_brief_service import ResearchBriefService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

WHEN = datetime(2026, 9, 30, tzinfo=UTC)


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
async def test_phase_eight_brief_persists_structured_provenance_and_conflicts() -> None:
    suffix = str(ObjectId())
    research_id = f"phase8-retry-evidence-{suffix}"
    source_id = f"phase8-provider-docs-{suffix}"
    project_id = f"phase8-payments-{suffix}"
    snapshot_id = "phase8-v1"
    grant = ResearchAuthorization(project_ids=[project_id], owner_key_id="writer")
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            await MongoResearchProjectRepository(database).create(
                ResearchProjectDocument(
                    research_project_id=research_id,
                    name="Phase 8 retry evidence",
                    question="Which retained evidence constrains retries?",
                    type="technical_research",
                    related_project_ids=[project_id],
                    authorization=grant,
                )
            )
            await MongoResearchSourceRepository(database, research_id).create(
                ResearchSourceDocument(
                    research_project_id=research_id,
                    source_id=source_id,
                    source_type=ResearchSourceType.MANUAL,
                    uri=f"manual:phase8-retries-{suffix}",
                    title="Phase 8 retry evidence",
                    source_class=SourceClass.USER_PROVIDED,
                    retrieved_at=WHEN,
                    authorization=grant,
                )
            )
            await MongoSourceSnapshotRepository(database, research_id).create(
                SourceSnapshotDocument(
                    research_project_id=research_id,
                    source_id=source_id,
                    snapshot_id=snapshot_id,
                    retrieved_at=WHEN,
                    content_hash="a" * 64,
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
            await claims.create(
                ResearchClaimDocument(
                    research_project_id=research_id,
                    claim_id="legacy-retry",
                    statement="A legacy client retries without an idempotency key.",
                    status=ClaimState.CONTESTED,
                    confidence=0.5,
                    confidence_rationale="The retained evidence conflicts.",
                    authorization=grant,
                )
            )
            citations = MongoResearchCitationRepository(database, research_id)
            for citation_id, claim_id, relation, excerpt in (
                (
                    "retry-support",
                    "retry-key",
                    CitationRelation.SUPPORTS,
                    "Retryable writes require an idempotency key.",
                ),
                (
                    "legacy-support",
                    "legacy-retry",
                    CitationRelation.SUPPORTS,
                    "Current clients use an idempotency key.",
                ),
                (
                    "legacy-contradicts",
                    "legacy-retry",
                    CitationRelation.CONTRADICTS,
                    "A legacy client retries without an idempotency key.",
                ),
            ):
                await citations.create(
                    ResearchCitationDocument(
                        research_project_id=research_id,
                        claim_id=claim_id,
                        citation_id=citation_id,
                        source_id=source_id,
                        snapshot_id=snapshot_id,
                        source_class=SourceClass.USER_PROVIDED,
                        locator=CitationLocator(
                            path=f"manual:phase8-retries-{suffix}", line_start=1
                        ),
                        evidence_excerpt=excerpt,
                        relation=relation,
                        authorization=grant,
                    )
                )
            service = ResearchBriefService(
                MongoResearchProjectRepository(database),
                lambda identifier: MongoResearchClaimRepository(database, identifier),
                lambda identifier: MongoResearchCitationRepository(database, identifier),
                lambda identifier: MongoResearchSourceRepository(database, identifier),
                lambda identifier: MongoSourceSnapshotRepository(database, identifier),
                lambda identifier: MongoResearchBriefRepository(database, identifier),
            )

            brief = await service.build_brief(
                research_project_id=research_id,
                brief_id="retry-summary",
                scope="Manual retry evidence only.",
                additional_unknowns=(),
                next_questions=("Which SDK releases require the key?",),
                status=ResearchBriefStatus.READY,
                created_by="writer",
                allowed_project_ids=(project_id,),
            )
            persisted = await MongoResearchBriefRepository(database, research_id).get(
                brief.brief_id
            )
            indexes = await database["research_briefs"].index_information()
            migration = await database["_schema_migrations"].find_one({"version": 16})

            assert persisted is not None
            assert persisted.citation_refs[0].snapshot_id == snapshot_id
            assert persisted.citation_refs[0].locator.line_start == 1
            assert persisted.source_refs[0].source_id == source_id
            assert persisted.contradictions[0].claim_id == "legacy-retry"
            assert set(persisted.contradictions[0].contradicting_citation_ids) == {
                "legacy-contradicts"
            }
            assert any("does not claim completeness" in item for item in persisted.unknowns)
            assert "research_brief_project_id_uq" in indexes
            assert migration is not None
            assert (
                await database["knowledge_candidates"].count_documents(
                    {"research_project_id": research_id}
                )
                == 0
            )
            assert (
                await database["promotion_events"].count_documents(
                    {"research_project_id": research_id}
                )
                == 0
            )
    finally:
        async with initialized_mongodb() as mongodb:
            for collection_name in (
                "research_briefs",
                "research_citations",
                "research_claims",
                "source_snapshots",
                "research_sources",
                "research_projects",
            ):
                await mongodb.database[collection_name].delete_many(
                    {"research_project_id": research_id}
                )
