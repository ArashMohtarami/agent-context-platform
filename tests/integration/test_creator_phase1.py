"""Live MongoDB checks for V2 Phase 1 creator-profile services."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from bson import ObjectId

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.repositories.content_repository import (
    MongoContentPillarRepository,
    MongoCreatorProfileRepository,
)
from app.repositories.errors import ConcurrencyConflictError, ProjectIsolationError
from app.services.creator_service import CreatorService

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
async def test_creator_profiles_and_pillars_are_profile_isolated_and_revision_guarded() -> None:
    suffix = str(ObjectId())
    profile_id = f"phase1-profile-{suffix}"
    other_profile_id = f"phase1-other-{suffix}"
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            creator = CreatorService(
                MongoCreatorProfileRepository(database),
                lambda profile: MongoContentPillarRepository(database, profile),
            )
            profile = await creator.create_profile(
                profile_id=profile_id,
                name="Phase 1 Creator",
                status="active",
                audiences=["backend engineers"],
                domains=["payments"],
                voice={"tone": ["clear"], "avoid": ["hype"]},
                writing_preferences=["short paragraphs"],
                formatting_preferences=[],
                terminology_preferences=[],
                language_preferences=["English"],
                tone_preferences=["practical"],
                topics_of_interest=["idempotency"],
                topics_to_avoid=["fabricated stories"],
                content_objectives=["teach"],
                tags=["backend"],
            )
            await creator.create_profile(
                profile_id=other_profile_id,
                name="Other Creator",
                status="inactive",
                audiences=[],
                domains=[],
                voice={"tone": [], "avoid": []},
                writing_preferences=[],
                formatting_preferences=[],
                terminology_preferences=[],
                language_preferences=[],
                tone_preferences=[],
                topics_of_interest=[],
                topics_to_avoid=[],
                content_objectives=[],
                tags=[],
            )
            pillars = [
                await creator.create_pillar(
                    profile_id=profile_id,
                    pillar_id=f"pillar-{index}",
                    name=f"Pillar {index}",
                    description=f"Profile-owned pillar {index}.",
                    tags=["backend"],
                    priority=0.9 - index / 10,
                    status="active",
                )
                for index in range(3)
            ]

            first_page = await creator.list_pillars(profile_id, limit=2)
            assert len(first_page.items) == 2
            assert first_page.next_cursor is not None
            second_page = await creator.list_pillars(
                profile_id,
                limit=2,
                cursor=first_page.next_cursor,
            )
            assert {item.pillar_id for item in (*first_page.items, *second_page.items)} == {
                item.pillar_id for item in pillars
            }
            updated = await creator.update_profile(
                profile_id,
                expected_revision=profile.revision,
                changes={"status": "inactive"},
            )
            assert updated.status == "inactive"
            with pytest.raises(ConcurrencyConflictError):
                await creator.update_profile(
                    profile_id,
                    expected_revision=profile.revision,
                    changes={"name": "stale"},
                )
            other_pillars = MongoContentPillarRepository(database, other_profile_id)
            assert await other_pillars.get(pillars[0].pillar_id) is None
            with pytest.raises(ProjectIsolationError):
                await other_pillars.create(pillars[0])
    finally:
        async with initialized_mongodb() as mongodb:
            await mongodb.database["content_pillars"].delete_many(
                {"profile_id": {"$in": [profile_id, other_profile_id]}}
            )
            await mongodb.database["creator_profiles"].delete_many(
                {"profile_id": {"$in": [profile_id, other_profile_id]}}
            )
