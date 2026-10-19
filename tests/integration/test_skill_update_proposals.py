"""Real-MongoDB V3 Phase 11 persistence and V1 skill non-mutation boundary test."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from bson import ObjectId
from v6_legacy_integration import initialize_database, seed_projects, seed_source

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.models.research import (
    CitationLocator,
    CitationRelation,
    ClaimState,
    ResearchAuthorization,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
)
from app.models.skill import SkillDocument
from app.repositories.research_repository import (
    MongoResearchCitationRepository,
    MongoResearchClaimRepository,
    MongoResearchProjectRepository,
    MongoSkillUpdateProposalRepository,
)
from app.repositories.skill_repository import MongoSkillRepository
from app.services.skill_update_proposal_service import SkillUpdateProposalService

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
async def test_generated_skill_proposal_persists_without_changing_v1_skill_document() -> None:
    suffix = str(ObjectId())
    research_id = f"phase11-retry-evidence-{suffix}"
    project_id = f"phase11-payments-{suffix}"
    skill_id = f"phase11-backend-guidance-{suffix}"
    grant = ResearchAuthorization(project_ids=[project_id], owner_key_id="writer")
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            await seed_projects(database, [project_id])
            await MongoResearchProjectRepository(database).create(
                ResearchProjectDocument(
                    research_project_id=research_id,
                    name="Phase 11 retry evidence",
                    question="How should canonical skill guidance describe retries?",
                    type="technical_research",
                    related_project_ids=[project_id],
                    authorization=grant,
                )
            )
            await MongoResearchClaimRepository(database, research_id).create(
                ResearchClaimDocument(
                    research_project_id=research_id,
                    claim_id="retry-key",
                    statement="Retryable provider writes require an idempotency key.",
                    status=ClaimState.SUPPORTED,
                    confidence=0.9,
                    confidence_rationale="Explicitly reviewed retained evidence.",
                    authorization=grant,
                )
            )
            await seed_source(
                database,
                research_project_id=research_id,
                source_id="provider-docs",
                snapshot_id="provider-v2",
                authorization=grant,
            )
            await MongoResearchCitationRepository(database, research_id).create(
                ResearchCitationDocument(
                    research_project_id=research_id,
                    claim_id="retry-key",
                    citation_id="provider-support",
                    source_id="provider-docs",
                    snapshot_id="provider-v2",
                    source_class="official_documentation",
                    locator=CitationLocator(url="https://docs.example.test/retries", line_start=12),
                    evidence_excerpt="Supply an idempotency key for retryable writes.",
                    relation=CitationRelation.SUPPORTS,
                    authorization=grant,
                )
            )
            skill = SkillDocument(
                skill_id=skill_id,
                name="Phase 11 backend guidance",
                version="1.2.3",
                description="A canonical Git-imported test skill.",
                category="backend",
                trust_level="trusted",
                source_path="skills/phase11-backend-guidance.md",
                content_hash="a" * 64,
            )
            skills = MongoSkillRepository(database)
            await skills.create(skill)
            before = await database["skills"].find_one({"skill_id": skill_id})
            skill_count = await database["skills"].count_documents({"skill_id": skill_id})

            service = SkillUpdateProposalService(
                MongoResearchProjectRepository(database),
                skills,
                lambda identifier: MongoResearchClaimRepository(database, identifier),
                lambda identifier: MongoResearchCitationRepository(database, identifier),
                lambda identifier: MongoSkillUpdateProposalRepository(database, identifier),
            )
            proposal = await service.create_proposal(
                research_project_id=research_id,
                proposal_id="idempotency-guidance",
                skill_id=skill_id,
                current_version="1.2.3",
                proposed_change="Clarify idempotency-key guidance from provider evidence.",
                proposed_patch=(
                    "--- a/skills/phase11-backend-guidance.md\n"
                    "+++ b/skills/phase11-backend-guidance.md"
                ),
                source_claim_ids=["retry-key"],
                source_citation_ids=["provider-support"],
                reason="The provider's documented retry requirement changed.",
                created_by="writer",
                allowed_project_ids=(project_id,),
            )
            persisted = await MongoSkillUpdateProposalRepository(database, research_id).get(
                proposal.proposal_id
            )
            indexes = await database["skill_update_proposals"].index_information()
            migration = await database["_schema_migrations"].find_one({"version": 19})
            after = await database["skills"].find_one({"skill_id": skill_id})

            assert persisted is not None
            assert persisted.trust_level == "generated"
            assert persisted.source_citation_ids == ["provider-support"]
            assert "tenant_skill_update_proposal_project_id_uq" in indexes
            assert migration is not None
            assert after == before
            assert await database["skills"].count_documents({"skill_id": skill_id}) == skill_count
    finally:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            await database["skill_update_proposals"].delete_many(
                {"research_project_id": research_id}
            )
            await database["research_citations"].delete_many({"research_project_id": research_id})
            await database["research_claims"].delete_many({"research_project_id": research_id})
            await database["research_projects"].delete_many({"research_project_id": research_id})
            await database["skills"].delete_many({"skill_id": skill_id})
