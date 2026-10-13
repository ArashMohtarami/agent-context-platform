"""Real-MongoDB V3 Phase 6 note/claim persistence and authorization checks."""

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
    ClaimState,
    ResearchAuthorization,
    ResearchClaimDocument,
    ResearchNoteStatus,
    ResearchProjectDocument,
    ResearchSourceDocument,
    ResearchSourceReference,
    ResearchSourceType,
    SourceClass,
    SourceSnapshotDocument,
)
from app.repositories.research_repository import (
    MongoResearchCitationRepository,
    MongoResearchClaimRepository,
    MongoResearchNoteRepository,
    MongoResearchProjectRepository,
    MongoResearchSourceCatalogRepository,
    MongoResearchSourceRepository,
    MongoSourceSnapshotRepository,
)
from app.services.research_notes_claims_service import ResearchNotesClaimsService

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
async def test_phase_six_artifacts_persist_and_filter_authorization() -> None:
    suffix = str(ObjectId())
    research_id = f"phase6-retry-evidence-{suffix}"
    source_id = f"phase6-provider-docs-{suffix}"
    project_id = f"phase6-payments-{suffix}"
    private_project_id = f"phase6-private-{suffix}"
    source_authorization = ResearchAuthorization(project_ids=[project_id], owner_key_id="writer")
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            projects = MongoResearchProjectRepository(database)
            await projects.create(
                ResearchProjectDocument(
                    research_project_id=research_id,
                    name="Phase 6 retry evidence",
                    question="What does the retained evidence say about retry semantics?",
                    type="technical_research",
                    related_project_ids=[project_id],
                    authorization=source_authorization,
                )
            )
            source = await MongoResearchSourceRepository(database, research_id).create(
                ResearchSourceDocument(
                    research_project_id=research_id,
                    source_id=source_id,
                    source_type=ResearchSourceType.MANUAL,
                    uri=f"manual:phase6-retries-{suffix}",
                    title="Phase 6 retry guide",
                    source_class=SourceClass.USER_PROVIDED,
                    authorization=source_authorization,
                )
            )
            await MongoSourceSnapshotRepository(database, research_id).create(
                SourceSnapshotDocument(
                    research_project_id=research_id,
                    source_id=source_id,
                    snapshot_id="phase6-v1",
                    retrieved_at=datetime(2026, 9, 30, tzinfo=UTC),
                    content_hash="a" * 64,
                    authorization=source_authorization,
                )
            )
            service = ResearchNotesClaimsService(
                projects,
                MongoResearchSourceCatalogRepository(database),
                lambda identifier: MongoSourceSnapshotRepository(database, identifier),
                lambda identifier: MongoResearchNoteRepository(database, identifier),
                lambda identifier: MongoResearchClaimRepository(database, identifier),
                lambda identifier: MongoResearchCitationRepository(database, identifier),
            )

            note = await service.create_note(
                research_project_id=research_id,
                note_id="retry-observation",
                title="Retry observation",
                body="The retained guide describes idempotency requirements.",
                source_refs=[
                    ResearchSourceReference(source_id=source.source_id, snapshot_id="phase6-v1")
                ],
                status=ResearchNoteStatus.WORKING,
                created_by="writer",
                allowed_project_ids=(project_id,),
            )
            updated_note = await service.update_note(
                research_project_id=research_id,
                note_id=note.note_id,
                expected_revision=note.revision,
                changes={"status": ResearchNoteStatus.REVIEWED},
                allowed_project_ids=(project_id,),
            )
            claim = await service.create_claim(
                research_project_id=research_id,
                claim_id="retry-key",
                statement="Retryable writes require an idempotency key.",
                status=ClaimState.SUPPORTED,
                confidence=0.9,
                confidence_rationale="A reviewer assessed the retained guide.",
                supporting_citation_ids=[],
                contradicting_citation_ids=[],
                created_by="writer",
                allowed_project_ids=(project_id,),
            )
            private_claim = await MongoResearchClaimRepository(database, research_id).create(
                ResearchClaimDocument(
                    research_project_id=research_id,
                    claim_id="private-claim",
                    statement="This historical claim remains private.",
                    status=ClaimState.UNREVIEWED,
                    authorization=ResearchAuthorization(project_ids=[private_project_id]),
                )
            )
            visible = await service.list_claims(
                research_project_id=research_id,
                status=None,
                allowed_project_ids=(project_id,),
                limit=20,
                cursor=None,
            )

            assert updated_note.status is ResearchNoteStatus.REVIEWED
            assert updated_note.revision == 2
            assert updated_note.source_refs[0].snapshot_id == "phase6-v1"
            assert claim.status is ClaimState.SUPPORTED
            assert claim.confidence == 0.9
            assert [item.claim_id for item in visible.items] == [claim.claim_id]
            assert private_claim.claim_id not in {item.claim_id for item in visible.items}
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
                "research_notes",
                "research_claims",
                "research_citations",
                "source_snapshots",
                "research_sources",
                "research_projects",
                "knowledge_candidates",
                "promotion_events",
            ):
                await mongodb.database[collection_name].delete_many(
                    {"research_project_id": research_id}
                )
