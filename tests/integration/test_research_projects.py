"""Real-MongoDB V3 Phase 1 project linking, pagination, and visibility checks."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from bson import ObjectId

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.repositories.project_repository import MongoProjectRepository
from app.repositories.research_repository import MongoResearchProjectRepository
from app.services.project_service import ProjectService
from app.services.research_project_service import ResearchProjectService

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
async def test_research_projects_link_v1_projects_and_filter_by_complete_authorized_set() -> None:
    suffix = str(ObjectId())
    project_id = f"phase1-payments-{suffix}"
    other_project_id = f"phase1-ledger-{suffix}"
    research_ids = (
        f"phase1-global-{suffix}",
        f"phase1-payments-research-{suffix}",
        f"phase1-multi-project-{suffix}",
    )
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            v1_projects = MongoProjectRepository(database)
            projects = ProjectService(v1_projects)
            await projects.create_project(
                project_id=project_id,
                name="Phase 1 Payments",
                description="V1 project used to test research links.",
                repository="https://example.test/payments.git",
                environment="test",
            )
            await projects.create_project(
                project_id=other_project_id,
                name="Phase 1 Ledger",
                description="Private V1 project used to test complete authorization.",
                repository="https://example.test/ledger.git",
                environment="test",
            )
            research = ResearchProjectService(MongoResearchProjectRepository(database), v1_projects)
            global_project = await research.create_project(
                research_project_id=research_ids[0],
                name="Global technical research",
                question="What operational constraints should local search respect?",
                type="technical_research",
                related_project_ids=[],
                status="active",
                owner_key_id="phase1-test-key",
            )
            linked = await research.create_project(
                research_project_id=research_ids[1],
                name="Mongot tradeoffs",
                question="What are the operational tradeoffs of self-managed mongot?",
                type="technical_research",
                related_project_ids=[project_id],
                status="active",
                owner_key_id="phase1-test-key",
            )
            await research.create_project(
                research_project_id=research_ids[2],
                name="Multi-project research",
                question="Which constraints are shared by payment and ledger systems?",
                type="architecture_evaluation",
                related_project_ids=[project_id, other_project_id],
                status="active",
                owner_key_id="phase1-test-key",
            )

            visible = await research.list_projects(
                allowed_project_ids=(project_id,),
                limit=20,
            )
            page = await research.list_projects(allowed_project_ids=None, limit=2)
            all_pages = [page]
            while page.next_cursor is not None:
                page = await research.list_projects(
                    allowed_project_ids=None,
                    limit=2,
                    cursor=page.next_cursor,
                )
                all_pages.append(page)
            archived = await research.update_project(
                linked.research_project_id,
                expected_revision=linked.revision,
                changes={"status": "archived"},
            )

            assert global_project.authorization.project_ids == []
            assert linked.related_project_ids == [project_id]
            visible_ids = {item.research_project_id for item in visible.items}
            assert {research_ids[0], research_ids[1]} <= visible_ids
            assert research_ids[2] not in visible_ids
            assert all_pages[0].next_cursor is not None
            paged_ids = {
                item.research_project_id for result_page in all_pages for item in result_page.items
            }
            assert set(research_ids) <= paged_ids
            assert sum(len(result_page.items) for result_page in all_pages) == len(paged_ids)
            assert archived.status == "archived"
            assert archived.revision == 2
    finally:
        async with initialized_mongodb() as mongodb:
            await mongodb.database["research_projects"].delete_many(
                {"research_project_id": {"$in": list(research_ids)}}
            )
            await mongodb.database["projects"].delete_many(
                {"project_id": {"$in": [project_id, other_project_id]}}
            )
