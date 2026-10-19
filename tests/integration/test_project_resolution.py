"""Live MongoDB coverage for V4 Phase 4 explicit project resolution."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from bson import ObjectId
from v6_legacy_integration import initialize_database

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.models.agent import ProjectBindingType, ProjectResolutionStatus
from app.models.project import ProjectDocument
from app.repositories.agent_repository import MongoProjectBindingRepository
from app.repositories.project_repository import MongoProjectRepository
from app.services.project_binding_service import ProjectBindingService

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
async def test_project_binding_resolves_ssh_and_https_without_filesystem_access() -> None:
    suffix = str(ObjectId())[-8:]
    project_id = f"payment-service-{suffix}"
    binding_id: str | None = None
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            projects = MongoProjectRepository(database)
            bindings = MongoProjectBindingRepository(database)
            await projects.create(
                ProjectDocument(
                    project_id=project_id,
                    name="Payment Service",
                    description="V4 project-resolution fixture",
                    repository="https://github.com/example/payment-service.git",
                    environment="test",
                )
            )
            service = ProjectBindingService(projects, bindings)
            binding = await service.create_binding(
                project_id=project_id,
                binding_type=ProjectBindingType.GIT_REMOTE,
                value="git@github.com:example/payment-service.git",
            )
            binding_id = binding.binding_id

            ssh = await service.resolve_project(
                git_remote="git@github.com:example/payment-service.git"
            )
            https = await service.resolve_project(
                repository_url="https://github.com/example/payment-service.git"
            )

            assert binding.value == "github.com/example/payment-service"
            assert ssh.status == ProjectResolutionStatus.RESOLVED
            assert ssh.project_id == project_id
            assert https.status == ProjectResolutionStatus.RESOLVED
            assert https.project_id == project_id
            assert await database["_schema_migrations"].count_documents({"version": 26}) == 1
            assert (
                "tenant_project_binding_repository_locator_uq"
                in await database["project_bindings"].index_information()
            )
    finally:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            if binding_id is not None:
                await database["project_bindings"].delete_many({"binding_id": binding_id})
            await database["projects"].delete_many({"project_id": project_id})
