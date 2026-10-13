"""Project lifecycle service tests independent of MongoDB."""

import pytest

from app.projects.errors import ProjectAlreadyExistsError, ProjectNotFoundError
from app.services.project_service import ProjectService
from tests.project_doubles import FakeProjectRepository, project


def build_service(*documents: object) -> tuple[ProjectService, FakeProjectRepository]:
    """Compose the service over an in-memory catalogue."""
    repository = FakeProjectRepository(documents)  # type: ignore[arg-type]
    return ProjectService(repository), repository


@pytest.mark.asyncio
async def test_a_project_is_created_with_every_documented_field() -> None:
    service, repository = build_service()

    created = await service.create_project(
        project_id="sampleco-payment",
        name="Sample Company Payment Service",
        description="Payment orchestration",
        repository="git@example.invalid:sampleco/payment.git",
        environment="production",
        tags=["payments", "critical"],
    )

    assert created.project_id == "sampleco-payment"
    assert created.name == "Sample Company Payment Service"
    assert created.description == "Payment orchestration"
    assert created.repository == "git@example.invalid:sampleco/payment.git"
    assert created.environment == "production"
    assert created.tags == ["payments", "critical"]
    assert repository.documents["sampleco-payment"] == created


@pytest.mark.asyncio
async def test_a_project_identifier_cannot_be_taken_twice() -> None:
    service, _ = build_service(project())

    with pytest.raises(ProjectAlreadyExistsError, match="sampleco-payment"):
        await service.create_project(
            project_id="sampleco-payment",
            name="Another System",
            description="A different system entirely",
            repository="git@example.invalid:other/system.git",
            environment="staging",
        )


@pytest.mark.asyncio
async def test_a_project_is_loaded_by_its_identifier() -> None:
    service, _ = build_service(project())

    assert (await service.get_project("sampleco-payment")).name == "Sample Company Payment Service"


@pytest.mark.asyncio
async def test_an_unknown_project_is_reported_as_missing() -> None:
    service, _ = build_service(project())

    with pytest.raises(ProjectNotFoundError, match="sampleco-wallet"):
        await service.get_project("sampleco-wallet")


@pytest.mark.asyncio
async def test_projects_are_listed_with_a_bounded_page() -> None:
    service, _ = build_service(project(), project("sampleco-wallet"))

    page = await service.list_projects(limit=1)

    assert len(page.items) == 1


@pytest.mark.asyncio
async def test_an_invalid_project_identifier_is_rejected_before_storage() -> None:
    service, repository = build_service()

    with pytest.raises(ValueError, match="project_id"):
        await service.create_project(
            project_id="not a valid identifier",
            name="Broken",
            description="Invalid identifier",
            repository="git@example.invalid:broken/system.git",
            environment="production",
        )

    assert repository.documents == {}


@pytest.mark.asyncio
async def test_duplicate_tags_are_rejected_before_storage() -> None:
    service, repository = build_service()

    with pytest.raises(ValueError, match="tags"):
        await service.create_project(
            project_id="sampleco-payment",
            name="Sample Company Payment Service",
            description="Payment orchestration",
            repository="git@example.invalid:sampleco/payment.git",
            environment="production",
            tags=["payments", "payments"],
        )

    assert repository.documents == {}
