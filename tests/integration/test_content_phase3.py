"""Real-MongoDB checks for V2 Phase 3 content lifecycle persistence."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.content.errors import InvalidContentLifecycleTransitionError
from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.client_types import MongoDocument
from app.db.initializer import initialize_database
from app.models.common import utc_now
from app.models.content import (
    ContentOrigin,
    ContentPlatform,
    ContentPublicationProvider,
    ContentSourceReference,
    ContentStatus,
    CreatorProfileDocument,
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
from app.services.content_service import ContentService

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


def service_for(database: AsyncDatabase[MongoDocument]) -> ContentService:
    """Bind every content adapter to the same live MongoDB database."""
    return ContentService(
        MongoCreatorProfileRepository(database),
        lambda profile: MongoContentIdeaRepository(database, profile),
        lambda profile: MongoContentBriefRepository(database, profile),
        lambda profile: MongoContentItemRepository(database, profile),
        lambda profile: MongoContentVersionRepository(database, profile),
        lambda profile: MongoContentLinkRepository(database, profile),
        lambda profile: MongoContentCampaignRepository(database, profile),
    )


@pytest.mark.asyncio
async def test_content_storage_versions_rollback_and_profile_isolation() -> None:
    suffix = str(ObjectId())
    profile_id = f"phase3-profile-{suffix}"
    other_profile_id = f"phase3-other-{suffix}"
    project_id = f"phase3-project-{suffix}"
    collections = (
        "content_ideas",
        "content_briefs",
        "content_items",
        "content_versions",
        "content_links",
        "content_campaigns",
        "creator_profiles",
    )
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            profiles = MongoCreatorProfileRepository(database)
            await profiles.create(CreatorProfileDocument(profile_id=profile_id, name="Phase 3"))
            await profiles.create(CreatorProfileDocument(profile_id=other_profile_id, name="Other"))
            service = service_for(database)
            source = ContentSourceReference(
                source_type="project_context", source_id="retry-rule", project_id=project_id
            )
            origin = ContentOrigin(
                type="project_context", project_id=project_id, source_ids=["retry-rule"]
            )
            idea = await service.create_idea(
                profile_id=profile_id,
                idea_id="idempotency",
                title="Idempotency",
                description="A manually supplied explanation of safe retries.",
                pillar_id=None,
                platforms=[ContentPlatform.LINKEDIN],
                origin=origin,
                tags=["payments"],
                priority=0.9,
            )
            brief = await service.create_brief(
                profile_id=profile_id,
                brief_id="idempotency-linkedin",
                idea_id=idea.idea_id,
                platform=ContentPlatform.LINKEDIN,
                objective="Explain idempotent payment retries.",
                audience=["backend engineers"],
                primary_message="Idempotency makes retries safe.",
                supporting_points=["Store and replay the first result."],
                source_refs=[source],
                skills=["linkedin-writing"],
                avoid_repetition_with=[],
                tags=["payments"],
            )
            draft = await service.create_content(
                profile_id=profile_id,
                content_id="idempotency-post",
                brief_id=brief.brief_id,
                platform=ContentPlatform.LINKEDIN,
                content_type="post",
                title="Idempotency",
                body="Initial draft about safe payment retries.",
                topic_tags=["idempotency"],
                pillar_id=None,
                project_ids=[project_id],
                source_refs=[source],
                tags=["payments"],
                created_by="integration-test",
            )
            revised = await service.create_version(
                profile_id,
                draft.content_id,
                expected_revision=draft.revision,
                body="Second draft with an implementation detail.",
                change_reason="add concrete retry detail",
                created_by="integration-test",
            )
            restored = await service.rollback_content(
                profile_id,
                draft.content_id,
                expected_revision=revised.revision,
                rollback_to_version=1,
                change_reason="restore initial wording",
                created_by="integration-test",
            )

            assert restored.current_version == 3
            assert restored.body == draft.body
            assert restored.source_refs == [source]
            history = await service.list_versions(profile_id, draft.content_id)
            assert [version.version for version in history.items] == [1, 2, 3]
            assert history.items[1].body.startswith("Second")
            assert history.items[2].body == draft.body
            other_items = MongoContentItemRepository(database, other_profile_id)
            assert await other_items.get(draft.content_id) is None

            review = await service.update_content(
                profile_id,
                draft.content_id,
                expected_revision=restored.revision,
                changes={"status": ContentStatus.REVIEW},
            )
            approved = await service.update_content(
                profile_id,
                draft.content_id,
                expected_revision=review.revision,
                changes={"status": ContentStatus.APPROVED},
            )
            published = await service.mark_content_published(
                profile_id,
                draft.content_id,
                expected_revision=approved.revision,
                provider=ContentPublicationProvider.MANUAL,
                external_id=None,
                external_url=None,
                published_at=utc_now(),
                idempotency_key="integration-manual-publish",
            )
            with pytest.raises(InvalidContentLifecycleTransitionError):
                await service.update_content(
                    profile_id,
                    draft.content_id,
                    expected_revision=published.revision,
                    changes={"status": ContentStatus.DRAFTING},
                )
    finally:
        async with initialized_mongodb() as mongodb:
            for collection in collections:
                await mongodb.database[collection].delete_many(
                    {"profile_id": {"$in": [profile_id, other_profile_id]}}
                )
