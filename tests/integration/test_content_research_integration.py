"""Real-MongoDB Phase 13 V2 brief persistence and V3 fact-review boundary."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from v6_legacy_integration import initialize_database

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.client_types import MongoDocument
from app.models.content import (
    ContentOrigin,
    ContentPlatform,
    ContentSourceReference,
    CreatorProfileDocument,
)
from app.models.project import ProjectDocument
from app.models.research import (
    CitationLocator,
    CitationRelation,
    ClaimState,
    ResearchAuthorization,
    ResearchBriefCitationReference,
    ResearchBriefClaimReference,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
    ResearchProjectType,
    ResearchSourceDocument,
    ResearchSourceType,
    SourceClass,
    SourceSnapshotDocument,
)
from app.repositories.content_repository import (
    MongoContentBriefRepository,
    MongoContentCampaignRepository,
    MongoContentIdeaRepository,
    MongoContentItemRepository,
    MongoContentLinkRepository,
    MongoContentVersionRepository,
    MongoCreatorProfileRepository,
)
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.research_repository import (
    MongoResearchChunkRepository,
    MongoResearchCitationRepository,
    MongoResearchClaimRepository,
    MongoResearchProjectRepository,
    MongoResearchSourceCatalogRepository,
    MongoResearchSourceRepository,
    MongoSourceSnapshotRepository,
)
from app.services.content_research_service import ContentResearchIntegrationService
from app.services.content_service import ContentService
from app.services.research_evidence_service import ResearchEvidenceService
from app.services.research_search_service import (
    ResearchRetrievalProfileName,
    ResearchSearchFilters,
    ResearchSearchMethod,
    ResearchSearchResult,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

WHEN = datetime(2026, 10, 1, tzinfo=UTC)


@asynccontextmanager
async def initialized_mongodb() -> AsyncIterator[MongoDBClient]:
    mongodb = MongoDBClient(get_settings())
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        yield mongodb
    finally:
        await mongodb.close()


class UnusedSearch:
    """Fact review reads authored claim evidence and never calls retrieval search."""

    async def search(
        self,
        *,
        project: ResearchProjectDocument,
        query: str,
        filters: ResearchSearchFilters | None = None,
        retrieval_profile: ResearchRetrievalProfileName = "research_general",
        method: ResearchSearchMethod = "hybrid",
        phrase: bool = False,
        limit: int = 10,
        token_budget: int | None = None,
        cursor: str | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
    ) -> ResearchSearchResult:
        del (
            project,
            query,
            filters,
            retrieval_profile,
            method,
            phrase,
            limit,
            token_budget,
            cursor,
            allowed_project_ids,
        )
        raise AssertionError("fact review must not run research retrieval")


def content_service(
    database: AsyncDatabase[MongoDocument], research: ContentResearchIntegrationService
) -> ContentService:
    return ContentService(
        MongoCreatorProfileRepository(database),
        lambda profile: MongoContentIdeaRepository(database, profile),
        lambda profile: MongoContentBriefRepository(database, profile),
        lambda profile: MongoContentItemRepository(database, profile),
        lambda profile: MongoContentVersionRepository(database, profile),
        lambda profile: MongoContentLinkRepository(database, profile),
        lambda profile: MongoContentCampaignRepository(database, profile),
        research=research,
    )


@pytest.mark.asyncio
async def test_research_backed_medium_brief_persists_canonical_evidence_and_reviews_draft() -> None:
    suffix = str(ObjectId())
    profile_id = f"phase13-profile-{suffix}"
    research_id = f"phase13-research-{suffix}"
    project_id = f"phase13-project-{suffix}"
    source_id = f"phase13-standard-{suffix}"
    snapshot_id = "phase13-v1"
    authorization = ResearchAuthorization(project_ids=[project_id], owner_key_id="writer")
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            await MongoProjectRepository(database).create(
                ProjectDocument(
                    project_id=project_id,
                    name="Fictional research source",
                    description="Synthetic regression fixture",
                    repository="https://example.invalid/fictional.git",
                    environment="test",
                )
            )
            await MongoResearchProjectRepository(database).create(
                ResearchProjectDocument(
                    research_project_id=research_id,
                    name="Phase 13 payment retry research",
                    question="Which claims can a Medium draft make about retries?",
                    type=ResearchProjectType.CONTENT_RESEARCH,
                    related_project_ids=[project_id],
                    authorization=authorization,
                )
            )
            await MongoResearchSourceRepository(database, research_id).create(
                ResearchSourceDocument(
                    research_project_id=research_id,
                    source_id=source_id,
                    source_type=ResearchSourceType.MANUAL,
                    uri=f"manual:phase13-{suffix}",
                    title="Phase 13 retry standard",
                    source_class=SourceClass.STANDARD,
                    retrieved_at=WHEN,
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
            claims = MongoResearchClaimRepository(database, research_id)
            claim = await claims.create(
                ResearchClaimDocument(
                    research_project_id=research_id,
                    claim_id="retry-key",
                    statement="Payment retries preserve the original idempotency key.",
                    status=ClaimState.SUPPORTED,
                    authorization=authorization,
                )
            )
            citations = MongoResearchCitationRepository(database, research_id)
            citation = await citations.create(
                ResearchCitationDocument(
                    research_project_id=research_id,
                    claim_id=claim.claim_id,
                    citation_id="retry-key-standard",
                    source_id=source_id,
                    snapshot_id=snapshot_id,
                    source_class=SourceClass.STANDARD,
                    locator=CitationLocator(
                        url="https://example.test/payments#idempotency", page=4
                    ),
                    evidence_excerpt="Payment retries preserve the original idempotency key.",
                    relation=CitationRelation.SUPPORTS,
                    authorization=authorization,
                )
            )
            evidence = ResearchEvidenceService(
                MongoResearchProjectRepository(database),
                MongoResearchSourceCatalogRepository(database),
                lambda identifier: MongoSourceSnapshotRepository(database, identifier),
                lambda identifier: MongoResearchChunkRepository(database, identifier),
                lambda identifier: MongoResearchClaimRepository(database, identifier),
                lambda identifier: MongoResearchCitationRepository(database, identifier),
                UnusedSearch(),
                token_budget=200,
                max_token_budget=200,
            )
            research = ContentResearchIntegrationService(
                MongoResearchProjectRepository(database),
                lambda identifier: MongoResearchClaimRepository(database, identifier),
                lambda identifier: MongoResearchCitationRepository(database, identifier),
                evidence,
            )
            await MongoCreatorProfileRepository(database).create(
                CreatorProfileDocument(profile_id=profile_id, name="Phase 13 creator")
            )
            service = content_service(database, research)
            idea = await service.create_idea(
                profile_id=profile_id,
                idea_id="payment-retries",
                title="Payment retry strategies",
                description="A manual Medium article idea.",
                pillar_id=None,
                platforms=[ContentPlatform.MEDIUM],
                origin=ContentOrigin(
                    type="research_project", project_id=project_id, source_ids=[research_id]
                ),
                tags=["payments"],
                priority=0.8,
            )
            brief = await service.create_brief(
                profile_id=profile_id,
                brief_id="payment-retries-medium",
                idea_id=idea.idea_id,
                platform=ContentPlatform.MEDIUM,
                objective="Explain safe payment retries.",
                audience=["backend engineers"],
                primary_message="Retries retain their idempotency key.",
                supporting_points=[],
                source_refs=[],
                research_project_id=research_id,
                claim_refs=[
                    ResearchBriefClaimReference(
                        claim_id=claim.claim_id,
                        statement="untrusted caller copy",
                        status=ClaimState.UNSUPPORTED,
                    )
                ],
                citation_refs=[
                    ResearchBriefCitationReference(
                        citation_id=citation.citation_id,
                        claim_id=claim.claim_id,
                        source_id="untrusted-source",
                        snapshot_id="untrusted-snapshot",
                        source_class=SourceClass.GENERATED,
                        locator=CitationLocator(url="https://example.test/untrusted"),
                        evidence_excerpt="untrusted excerpt",
                        relation=CitationRelation.CONTRADICTS,
                    )
                ],
                skills=["medium-writing"],
                avoid_repetition_with=[],
                tags=["payments"],
                allowed_project_ids=(project_id,),
            )
            draft = await service.create_content(
                profile_id=profile_id,
                content_id="payment-retries-article",
                brief_id=brief.brief_id,
                platform=ContentPlatform.MEDIUM,
                content_type="article",
                title="Payment retry strategies",
                body="Payment retries preserve the original idempotency key.",
                topic_tags=["payments"],
                pillar_id=None,
                project_ids=[project_id],
                source_refs=[
                    ContentSourceReference(
                        source_type="research_project",
                        source_id=research_id,
                        project_id=project_id,
                    )
                ],
                tags=["payments"],
                created_by="writer",
            )
            review = await research.review_content_facts(
                content=draft,
                research_project_id=research_id,
                claim_ids=(),
                claim_statements=(),
                allowed_project_ids=(project_id,),
            )
            persisted = await MongoContentBriefRepository(database, profile_id).get(brief.brief_id)
            migration = await database["_schema_migrations"].find_one({"version": 20})

            assert persisted is not None
            assert persisted.research_project_id == research_id
            assert persisted.claim_refs[0].statement == claim.statement
            assert persisted.citation_refs[0].source_id == source_id
            assert persisted.citation_refs[0].locator.page == 4
            assert persisted.source_refs[0].project_id == project_id
            assert review.supported == 1
            assert review.factual_review_status == "verified"
            citation_url = review.citation_refs[0].locator.url
            assert citation_url is not None and citation_url.endswith("#idempotency")
            assert draft.status == "drafting"
            assert migration is not None
    finally:
        async with initialized_mongodb() as mongodb:
            for collection_name in (
                "content_ideas",
                "content_briefs",
                "content_items",
                "content_versions",
                "creator_profiles",
            ):
                await mongodb.database[collection_name].delete_many({"profile_id": profile_id})
            for collection_name in (
                "research_citations",
                "research_claims",
                "source_snapshots",
                "research_sources",
                "research_projects",
            ):
                await mongodb.database[collection_name].delete_many(
                    {"research_project_id": research_id}
                )
