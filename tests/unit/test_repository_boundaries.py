"""Repository isolation, pagination, validation, and concurrency tests."""

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.models import MemoryDocument, ProjectContextDocument, ProjectDocument
from app.models.common import InvalidCursorError
from app.models.content import ContentIdeaDocument
from app.repositories.content_repository import MongoContentIdeaRepository
from app.repositories.context_repository import MongoProjectContextRepository
from app.repositories.errors import ConcurrencyConflictError, ProjectIsolationError
from app.repositories.memory_repository import MongoMemoryRepository
from app.repositories.project_repository import MongoProjectRepository


def mock_database() -> tuple[AsyncDatabase[MongoDocument], MagicMock]:
    collection = MagicMock()
    collection.insert_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)
    collection.find_one_and_update = AsyncMock()
    collection.delete_one = AsyncMock()
    database = MagicMock()
    database.__getitem__.return_value = collection
    return cast(AsyncDatabase[MongoDocument], database), collection


@pytest.mark.asyncio
async def test_project_bound_repositories_reject_cross_project_writes_and_reads() -> None:
    database, collection = mock_database()
    repository = MongoProjectContextRepository(database, "project-a")
    other_context = ProjectContextDocument(
        project_id="project-b",
        type="security",
        title="Private",
        content="Project B only",
        importance=1.0,
        source="user",
    )

    with pytest.raises(ProjectIsolationError):
        await repository.create(other_context)
    collection.insert_one.assert_not_awaited()

    collection.find_one.return_value = None
    assert await repository.get(other_context.id) is None
    collection.find_one.assert_awaited_once_with(
        {"_id": other_context.id, "project_id": "project-a", "org_id": "org-test"}
    )


@pytest.mark.asyncio
async def test_memory_repository_enforces_global_or_exact_project_scope() -> None:
    database, collection = mock_database()
    global_repository = MongoMemoryRepository(database, project_id=None)
    project_memory = MemoryDocument(
        scope="project",
        project_id="project-a",
        type="decision",
        content="Private decision",
        importance=0.9,
        confidence=0.9,
        source="user",
    )

    with pytest.raises(ProjectIsolationError):
        await global_repository.create(project_memory)
    collection.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_keyset_pagination_is_bounded_and_produces_a_cursor() -> None:
    database, collection = mock_database()
    repository = MongoProjectRepository(database)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    projects = [
        ProjectDocument(
            project_id=f"project-{index}",
            name=f"Project {index}",
            description="Description",
            repository=f"ssh://git/project-{index}.git",
            environment="test",
            created_at=start + timedelta(seconds=index),
            updated_at=start + timedelta(seconds=index),
        )
        for index in range(3)
    ]
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(
        return_value=[project.model_dump(by_alias=True, mode="python") for project in projects]
    )
    collection.find.return_value = cursor

    page = await repository.list(limit=2)

    assert [project.project_id for project in page.items] == ["project-0", "project-1"]
    assert page.next_cursor is not None
    cursor.limit.assert_called_once_with(3)

    with pytest.raises(ValueError, match="between"):
        await repository.list(limit=51)
    with pytest.raises(InvalidCursorError):
        await repository.list(cursor="not-a-valid-cursor")


@pytest.mark.asyncio
async def test_updates_are_revision_guarded() -> None:
    database, collection = mock_database()
    repository = MongoProjectRepository(database)
    project = ProjectDocument(
        project_id="project-a",
        name="Project A",
        description="Original",
        repository="ssh://git/project-a.git",
        environment="test",
    )
    collection.find_one_and_update.return_value = None

    with pytest.raises(ConcurrencyConflictError):
        await repository.update(project, expected_revision=1)

    query = collection.find_one_and_update.await_args.args[0]
    update = collection.find_one_and_update.await_args.args[1]
    assert query == {
        "_id": project.id,
        "revision": 1,
        "org_id": "org-test",
        "identity_version": 2,
        "legacy_adoption_id": None,
    }
    assert update["$inc"] == {"revision": 1}
    assert "updated_at" in update["$set"]


@pytest.mark.asyncio
async def test_invalid_database_documents_fail_validation_on_read() -> None:
    database, collection = mock_database()
    repository = MongoProjectRepository(database)
    collection.find_one.return_value = {"_id": "invalid", "org_id": "org-test"}

    with pytest.raises(ValidationError):
        await repository.get("project-a")


@pytest.mark.asyncio
async def test_content_repositories_bind_every_write_read_and_update_to_a_profile() -> None:
    database, collection = mock_database()
    repository = MongoContentIdeaRepository(database, "profile-a")
    other_idea = ContentIdeaDocument(
        profile_id="profile-b",
        idea_id="private-idea",
        title="Private creator idea",
        description="This creator's records must remain isolated.",
        platforms=["linkedin"],
        origin={"type": "project_context", "project_id": "project-b"},
        priority=0.8,
    )

    with pytest.raises(ProjectIsolationError):
        await repository.create(other_idea)
    collection.insert_one.assert_not_awaited()

    collection.find_one.return_value = None
    assert await repository.get(other_idea.idea_id) is None
    collection.find_one.assert_awaited_once_with(
        {
            "idea_id": other_idea.idea_id,
            "org_id": "org-test",
            "profile_id": "profile-a",
        }
    )

    collection.find_one_and_update.return_value = None
    own_idea = ContentIdeaDocument(
        profile_id="profile-a",
        idea_id="own-idea",
        title="Own creator idea",
        description="This update is guarded by its current revision.",
        platforms=["linkedin"],
        origin={"type": "project_context", "project_id": "project-a"},
        priority=0.8,
    )
    with pytest.raises(ConcurrencyConflictError):
        await repository.update(own_idea, expected_revision=1)
    query = collection.find_one_and_update.await_args.args[0]
    assert query == {
        "_id": own_idea.id,
        "revision": 1,
        "profile_id": "profile-a",
        "org_id": "org-test",
        "identity_version": 2,
        "legacy_adoption_id": None,
    }


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
