"""Real-MongoDB V3 Phase 14 freshness and no-mutation boundary tests."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from bson import ObjectId
from v6_legacy_integration import initialize_database

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.models.freshness import FreshnessClass, RevalidationReason
from app.models.memory import MemoryDocument
from app.models.promotion import PromotionProvenance
from app.models.research import (
    CandidateState,
    CandidateType,
    CitationLocator,
    CitationRelation,
    KnowledgeCandidateDocument,
    PromotionEventDocument,
    ResearchAuthorization,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
    ResearchSourceDocument,
    ResearchSourceType,
    SourceClass,
    SourceSnapshotDocument,
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
    MongoResearchSourceRepository,
    MongoSourceSnapshotRepository,
)
from app.services.knowledge_candidate_service import KnowledgeCandidateService
from app.services.revalidation_service import RevalidationService
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
async def test_changed_snapshot_proposes_revalidation_without_mutating_promoted_memory() -> None:
    suffix = str(ObjectId())
    research_id = f"phase14-research-{suffix}"
    project_id = f"phase14-project-{suffix}"
    source_id = f"phase14-source-{suffix}"
    claim_id = f"phase14-claim-{suffix}"
    citation_id = f"phase14-citation-{suffix}"
    baseline_snapshot_id = f"phase14-snapshot-v1-{suffix}"
    changed_snapshot_id = f"phase14-snapshot-v2-{suffix}"
    candidate_id = f"phase14-knowledge-{suffix}"
    event_id = f"phase14-promotion-{suffix}"
    authorization = ResearchAuthorization(project_ids=[project_id], owner_key_id="writer")
    verified_at = datetime(2026, 10, 1, tzinfo=UTC)
    changed_at = datetime(2026, 10, 8, tzinfo=UTC)

    async with initialized_mongodb() as mongodb:
        database = mongodb.database
        await MongoProjectRepository(database).create(v1_project(project_id))
        await MongoResearchProjectRepository(database).create(
            ResearchProjectDocument(
                research_project_id=research_id,
                name="Phase 14 retry research",
                question="Which retry knowledge needs revalidation?",
                type="technical_research",
                related_project_ids=[project_id],
                authorization=authorization,
            )
        )
        sources = MongoResearchSourceRepository(database, research_id)
        await sources.create(
            ResearchSourceDocument(
                research_project_id=research_id,
                source_id=source_id,
                source_type=ResearchSourceType.MANUAL,
                uri=f"manual:phase14-{suffix}",
                title="Phase 14 retry standard",
                source_class=SourceClass.STANDARD,
                authorization=authorization,
            )
        )
        snapshots = MongoSourceSnapshotRepository(database, research_id)
        await snapshots.create(
            SourceSnapshotDocument(
                research_project_id=research_id,
                source_id=source_id,
                snapshot_id=baseline_snapshot_id,
                retrieved_at=verified_at,
                content_hash="a" * 64,
                version_identifier="1",
                metadata={"etag": "etag-v1", "last_modified": "Wed, 01 Oct 2026"},
                authorization=authorization,
            )
        )
        claims = MongoResearchClaimRepository(database, research_id)
        await claims.create(
            ResearchClaimDocument(
                research_project_id=research_id,
                claim_id=claim_id,
                statement="Payment retries preserve the original idempotency key.",
                status="supported",
                authorization=authorization,
            )
        )
        citations = MongoResearchCitationRepository(database, research_id)
        await citations.create(
            ResearchCitationDocument(
                research_project_id=research_id,
                claim_id=claim_id,
                citation_id=citation_id,
                source_id=source_id,
                snapshot_id=baseline_snapshot_id,
                source_class=SourceClass.STANDARD,
                locator=CitationLocator(url="https://example.test/retries"),
                evidence_excerpt="Payment retries preserve the original idempotency key.",
                relation=CitationRelation.SUPPORTS,
                authorization=authorization,
            )
        )
        candidates = MongoKnowledgeCandidateRepository(database, research_id)
        original = await candidates.create(
            KnowledgeCandidateDocument(
                research_project_id=research_id,
                candidate_id=candidate_id,
                candidate_type=CandidateType.MEMORY,
                target_project_id=project_id,
                title="Retry idempotency",
                content="Payment retries preserve the original idempotency key.",
                source_claim_ids=[claim_id],
                source_citation_ids=[citation_id],
                freshness_class=FreshnessClass.TIME_SENSITIVE,
                review_after=datetime(2026, 10, 6, tzinfo=UTC),
                status=CandidateState.PROMOTED,
                reviewer="reviewer",
                reviewed_at=verified_at,
                authorization=authorization,
            )
        )
        memories = MongoMemoryRepository(database, project_id=project_id)
        stored_memory = await memories.create(
            MemoryDocument(
                scope="project",
                project_id=project_id,
                type="research_finding",
                content=original.content,
                summary=original.title,
                importance=0.8,
                confidence=0.8,
                source="research-promotion",
                promotion_provenance=PromotionProvenance(
                    candidate_id=original.candidate_id,
                    research_project_id=research_id,
                    claim_ids=[claim_id],
                    citation_ids=[citation_id],
                    source_ids=[source_id],
                    source_snapshot_ids=[baseline_snapshot_id],
                    verified_at=verified_at,
                    review_after=datetime(2026, 10, 6, tzinfo=UTC),
                    freshness_class=FreshnessClass.TIME_SENSITIVE,
                ),
            )
        )
        events = MongoPromotionEventRepository(database, research_id)
        await events.create(
            PromotionEventDocument(
                research_project_id=research_id,
                candidate_id=original.candidate_id,
                event_id=event_id,
                target_type=CandidateType.MEMORY,
                target_id=str(stored_memory.id),
                reviewer="reviewer",
                decision=CandidateState.PROMOTED,
                decided_at=verified_at,
                authorization=authorization,
            )
        )
        await snapshots.create(
            SourceSnapshotDocument(
                research_project_id=research_id,
                source_id=source_id,
                snapshot_id=changed_snapshot_id,
                retrieved_at=changed_at,
                content_hash="b" * 64,
                version_identifier="2",
                metadata={"etag": "etag-v2", "last_modified": "Wed, 08 Oct 2026"},
                authorization=authorization,
            )
        )

        candidate_writer = KnowledgeCandidateService(
            MongoResearchProjectRepository(database),
            MongoProjectRepository(database),
            lambda _: MongoResearchClaimRepository(database, research_id),
            lambda _: MongoResearchCitationRepository(database, research_id),
            lambda _: MongoKnowledgeCandidateRepository(database, research_id),
            lambda _: MongoPromotionEventRepository(database, research_id),
        )
        revalidation = RevalidationService(
            MongoResearchProjectRepository(database),
            lambda _: MongoKnowledgeCandidateRepository(database, research_id),
            lambda _: MongoPromotionEventRepository(database, research_id),
            lambda _: MongoResearchCitationRepository(database, research_id),
            lambda _: MongoSourceSnapshotRepository(database, research_id),
            lambda project: MongoMemoryRepository(database, project_id=project),
            lambda project: MongoProjectContextRepository(database, project),
            candidate_writer,
            clock=lambda: changed_at,
        )
        before = stored_memory.model_dump(mode="json")

        stale = await revalidation.list_stale_knowledge(
            research_project_id=research_id,
            allowed_project_ids=(project_id,),
            as_of=changed_at,
        )
        proposal = await revalidation.propose_revalidation(
            research_project_id=research_id,
            candidate_id=original.candidate_id,
            created_by="reviewer",
            allowed_project_ids=(project_id,),
            as_of=changed_at,
        )
        after = await memories.get(stored_memory.id)

    assert RevalidationReason.REVIEW_DUE in stale.items[0].reasons
    assert RevalidationReason.CONTENT_HASH_CHANGED in stale.items[0].reasons
    assert proposal.candidate.status is CandidateState.PENDING_REVIEW
    assert proposal.candidate.revalidation_of_candidate_id == original.candidate_id
    assert after is not None
    assert after.model_dump(mode="json") == before

    # The integration service shares the local development database. Remove only
    # this test's uniquely prefixed fixture records after a successful assertion.
    async with initialized_mongodb() as mongodb:
        database = mongodb.database
        for collection in (
            "research_projects",
            "research_sources",
            "source_snapshots",
            "research_claims",
            "research_citations",
            "knowledge_candidates",
            "promotion_events",
        ):
            await database[collection].delete_many({"research_project_id": research_id})
        await database["projects"].delete_many({"project_id": project_id})
        await database["memories"].delete_many({"project_id": project_id})
        await database["project_context"].delete_many({"project_id": project_id})
