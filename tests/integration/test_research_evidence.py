"""Real-MongoDB V3 Phase 7 citation persistence, indexes, and packet boundary."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

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
    ResearchChunkDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
    ResearchSourceDocument,
    ResearchSourceType,
    SourceClass,
    SourceSnapshotDocument,
)
from app.repositories.research_repository import (
    MongoResearchChunkRepository,
    MongoResearchCitationRepository,
    MongoResearchClaimRepository,
    MongoResearchProjectRepository,
    MongoResearchSourceCatalogRepository,
    MongoResearchSourceRepository,
    MongoSourceSnapshotRepository,
)
from app.services.research_evidence_service import ResearchEvidenceService
from app.services.research_search_service import ResearchSearchItem, ResearchSearchResult

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


class Search:
    def __init__(self, research_id: str, source_id: str, snapshot_id: str, chunk_id: str) -> None:
        self._research_id = research_id
        self._source_id = source_id
        self._snapshot_id = snapshot_id
        self._chunk_id = chunk_id

    async def search(self, **kwargs: Any) -> ResearchSearchResult:
        budget = kwargs["token_budget"]
        return ResearchSearchResult(
            research_project_id=self._research_id,
            retrieval_profile="research_general",
            method="hybrid",
            items=(
                ResearchSearchItem(
                    kind="chunk",
                    identifier=self._chunk_id,
                    source="research_user_provided",
                    title="Retry evidence",
                    content="Retryable writes require an idempotency key.",
                    status="active",
                    source_id=self._source_id,
                    snapshot_id=self._snapshot_id,
                    document_id="retry-document",
                    source_type=ResearchSourceType.MANUAL,
                    source_class=SourceClass.USER_PROVIDED,
                    publisher=None,
                    domain=None,
                    published_at=None,
                    retrieved_at=WHEN,
                    locator={"path": "manual:retry-evidence", "line_start": 1},
                    related_project_ids=("integration-payments",),
                    retrieval_score=0.9,
                    methods=("lexical",),
                    tokens=10,
                    updated_at=WHEN,
                ),
            ),
            token_budget=budget,
            tokens_used=10,
            next_cursor=None,
            diagnostics={},
        )


@pytest.mark.asyncio
async def test_phase_seven_citations_persist_with_existing_indexes_and_packet_provenance() -> None:
    suffix = str(ObjectId())
    research_id = f"phase7-retry-evidence-{suffix}"
    source_id = f"phase7-manual-source-{suffix}"
    snapshot_id = "phase7-v1"
    chunk_id = "phase7-retry-chunk"
    project_id = "integration-payments"
    authorization = ResearchAuthorization(project_ids=[project_id], owner_key_id="writer")
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            await MongoResearchProjectRepository(database).create(
                ResearchProjectDocument(
                    research_project_id=research_id,
                    name="Phase 7 retry evidence",
                    question="Which retained source evidence constrains retries?",
                    type="technical_research",
                    related_project_ids=[project_id],
                    authorization=authorization,
                )
            )
            await MongoResearchSourceRepository(database, research_id).create(
                ResearchSourceDocument(
                    research_project_id=research_id,
                    source_id=source_id,
                    source_type=ResearchSourceType.MANUAL,
                    uri=f"manual:phase7-retries-{suffix}",
                    title="Phase 7 retry evidence",
                    source_class=SourceClass.USER_PROVIDED,
                    authorization=authorization,
                )
            )
            await MongoSourceSnapshotRepository(database, research_id).create(
                SourceSnapshotDocument(
                    research_project_id=research_id,
                    source_id=source_id,
                    snapshot_id=snapshot_id,
                    retrieved_at=WHEN,
                    content_hash="a" * 64,
                    authorization=authorization,
                )
            )
            await MongoResearchChunkRepository(database, research_id).create(
                ResearchChunkDocument(
                    research_project_id=research_id,
                    source_id=source_id,
                    snapshot_id=snapshot_id,
                    document_id="retry-document",
                    chunk_id=chunk_id,
                    chunk_index=0,
                    content="Retryable writes require an idempotency key.",
                    source_class=SourceClass.USER_PROVIDED,
                    locator=CitationLocator(path=f"manual:phase7-retries-{suffix}", line_start=1),
                    authorization=authorization,
                )
            )
            await MongoResearchClaimRepository(database, research_id).create(
                ResearchClaimDocument(
                    research_project_id=research_id,
                    claim_id="retry-key",
                    statement="Retryable writes require idempotency keys.",
                    status=ClaimState.UNREVIEWED,
                    authorization=authorization,
                )
            )
            service = ResearchEvidenceService(
                MongoResearchProjectRepository(database),
                MongoResearchSourceCatalogRepository(database),
                lambda identifier: MongoSourceSnapshotRepository(database, identifier),
                lambda identifier: MongoResearchChunkRepository(database, identifier),
                lambda identifier: MongoResearchClaimRepository(database, identifier),
                lambda identifier: MongoResearchCitationRepository(database, identifier),
                Search(research_id, source_id, snapshot_id, chunk_id),
                token_budget=200,
                max_token_budget=200,
            )

            citation = await service.create_citation(
                research_project_id=research_id,
                citation_id="manual-support",
                claim_id="retry-key",
                source_id=source_id,
                snapshot_id=snapshot_id,
                chunk_id=chunk_id,
                locator=CitationLocator(path=f"manual:phase7-retries-{suffix}"),
                evidence_excerpt="Retryable writes require an idempotency key.",
                relation=CitationRelation.SUPPORTS,
                created_by="writer",
                allowed_project_ids=(project_id,),
            )
            persisted = await MongoResearchCitationRepository(database, research_id).get(
                "retry-key", "manual-support"
            )
            packet = await service.build_evidence_packet(
                query="retry idempotency",
                research_project_id=research_id,
                project_id=project_id,
                token_budget=200,
                allowed_project_ids=(project_id,),
            )
            indexes = await database["research_citations"].index_information()

            assert persisted is not None
            assert persisted.id == citation.id
            assert persisted.locator.line_start == 1
            assert packet.supporting_evidence[0].citation.citation_id == "manual-support"
            assert packet.citation_locators[0].path == f"manual:phase7-retries-{suffix}"
            assert packet.tokens_used <= packet.token_budget
            assert "research_citation_claim_id_uq" in indexes
    finally:
        async with initialized_mongodb() as mongodb:
            for collection_name in (
                "research_citations",
                "research_chunks",
                "research_claims",
                "source_snapshots",
                "research_sources",
                "research_projects",
            ):
                await mongodb.database[collection_name].delete_many(
                    {"research_project_id": research_id}
                )
