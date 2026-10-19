"""Real-MongoDB Phase 5 discovery using the checked-in payment-service fixture."""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from bson import ObjectId
from v6_legacy_integration import initialize_database

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.models.content import (
    ContentIdeaDocument,
    ContentOrigin,
    ContentPillarDocument,
    ContentPlatform,
    CreatorProfileDocument,
)
from app.models.project import ProjectDocument
from app.models.project_context import ProjectContextDocument
from app.repositories.content_repository import (
    MongoContentIdeaRepository,
    MongoContentItemRepository,
    MongoContentPillarRepository,
    MongoCreatorProfileRepository,
)
from app.repositories.context_repository import MongoProjectContextRepository
from app.repositories.memory_repository import MongoMemoryRepository
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.services.content_discovery_service import ContentDiscoveryService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

_FIXTURE = Path(__file__).parents[2] / "examples" / "payment-service" / "knowledge.json"


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
async def test_payment_service_fixture_discovers_linkedin_and_prepares_two_platform_briefs() -> (
    None
):
    """One source is discoverable and remains cited by LinkedIn and Medium briefs."""
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    suffix = str(ObjectId())
    profile_id = f"phase5-profile-{suffix}"
    project_id = f"payment-service-phase5-{suffix}"
    collections = (
        "creator_profiles",
        "content_pillars",
        "content_ideas",
        "content_items",
        "projects",
        "project_context",
        "memories",
    )
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            expected_indexes = {
                "skills": "tenant_skill_status_updated",
                "project_context": "tenant_context_project_status_updated",
                "memories": "tenant_memory_scope_project_status_updated",
                "content_ideas": "tenant_content_idea_profile_updated",
                "content_items": "tenant_content_item_profile_status_updated",
            }
            for collection, index_name in expected_indexes.items():
                cursor = await database[collection].list_indexes()
                index_names = {item["name"] async for item in cursor}
                assert index_name in index_names
            project_data = fixture["project"]
            project = ProjectDocument(
                project_id=project_id,
                name=project_data["name"],
                description=project_data["description"],
                repository=project_data["repository"],
                environment=project_data["environment"],
                tags=project_data["tags"],
            )
            await MongoProjectRepository(database).create(project)
            profile = CreatorProfileDocument(
                profile_id=profile_id,
                name="Payment Service Technical Brand",
                audiences=["backend engineers"],
                domains=["payments"],
                topics_of_interest=["idempotency", "payment architecture"],
                content_objectives=["teach practical engineering lessons"],
            )
            await MongoCreatorProfileRepository(database).create(profile)
            pillar = ContentPillarDocument(
                profile_id=profile_id,
                pillar_id="payments",
                name="Payments",
                description="Practical payment-system engineering.",
                tags=["payments", "idempotency"],
                priority=0.9,
            )
            await MongoContentPillarRepository(database, profile_id).create(pillar)
            source_data = next(
                item
                for item in fixture["context"]
                if item["external_id"] == "payment-service-idempotency"
            )
            source = ProjectContextDocument(
                project_id=project_id,
                type=source_data["type"],
                title=source_data["title"],
                content=source_data["content"],
                importance=source_data["importance"],
                source="payment-service-fixture",
                external_id=f"phase5-{source_data['external_id']}",
                tags=source_data["tags"],
            )
            source = await MongoProjectContextRepository(database, project_id).create(source)
            idea = ContentIdeaDocument(
                profile_id=profile_id,
                idea_id="payment-retry-lesson",
                title="Why retries need a stored response",
                description=(
                    "Explain the payment-service idempotency decision from its source record."
                ),
                pillar_id="payments",
                platforms=[ContentPlatform.LINKEDIN, ContentPlatform.MEDIUM],
                origin=ContentOrigin(
                    type="project_context", project_id=project_id, source_ids=[str(source.id)]
                ),
                tags=["payments", "idempotency"],
                priority=0.9,
            )
            await MongoContentIdeaRepository(database, profile_id).create(idea)
            discovery = ContentDiscoveryService(
                MongoCreatorProfileRepository(database),
                MongoProjectRepository(database),
                lambda profile: MongoContentPillarRepository(database, profile),
                lambda profile: MongoContentIdeaRepository(database, profile),
                lambda profile: MongoContentItemRepository(database, profile),
                lambda scope: MongoProjectContextRepository(database, scope),
                lambda scope: MongoMemoryRepository(database, project_id=scope),
                MongoSkillRepository(database),
            )

            discovered = await discovery.discover_content_ideas(
                profile_id=profile_id,
                project_id=project_id,
                pillar_id="payments",
                platform=ContentPlatform.LINKEDIN,
            )
            linkedin = await discovery.prepare_content_brief(
                profile_id=profile_id,
                idea_id=idea.idea_id,
                platform=ContentPlatform.LINKEDIN,
            )
            medium = await discovery.prepare_content_brief(
                profile_id=profile_id,
                idea_id=idea.idea_id,
                platform=ContentPlatform.MEDIUM,
            )

            assert any(
                candidate.topic == "Idempotency rules" for candidate in discovered.candidates
            )
            assert all(
                candidate.suggested_platforms == (ContentPlatform.LINKEDIN,)
                for candidate in discovered.candidates
            )
            assert linkedin.platform is ContentPlatform.LINKEDIN
            assert medium.platform is ContentPlatform.MEDIUM
            assert linkedin.source_refs == medium.source_refs
            assert linkedin.source_refs[0].source_id == str(source.id)
            assert linkedin.source_refs[0].project_id == project_id
            assert "do not fabricate project experiences" in linkedin.content_constraints
    finally:
        async with initialized_mongodb() as mongodb:
            for collection in collections:
                if collection in {
                    "creator_profiles",
                    "content_pillars",
                    "content_ideas",
                    "content_items",
                }:
                    await mongodb.database[collection].delete_many({"profile_id": profile_id})
                elif collection == "projects":
                    await mongodb.database[collection].delete_many({"project_id": project_id})
                else:
                    await mongodb.database[collection].delete_many({"project_id": project_id})
