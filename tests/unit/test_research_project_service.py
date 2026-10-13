"""V3 Phase 1 research-project business rules."""

import pytest

from app.models.project import ProjectDocument
from app.models.research import ResearchProjectStatus
from app.repositories.errors import ConcurrencyConflictError
from app.research.errors import (
    RelatedProjectNotFoundError,
    ResearchProjectAlreadyExistsError,
)
from app.services.research_project_service import ResearchProjectService
from tests.project_doubles import FakeProjectRepository, project
from tests.research_doubles import FakeResearchProjectRepository

PROJECT = "payments-api"
OTHER_PROJECT = "ledger-api"


def service(*, with_other: bool = True) -> ResearchProjectService:
    projects: list[ProjectDocument] = [project(PROJECT)]
    if with_other:
        projects.append(project(OTHER_PROJECT))
    return ResearchProjectService(FakeResearchProjectRepository(), FakeProjectRepository(projects))


@pytest.mark.asyncio
async def test_research_project_crud_preserves_owner_and_rebuilds_link_authorization() -> None:
    research = service()

    created = await research.create_project(
        research_project_id="mongot-tradeoffs",
        name="Mongot tradeoffs",
        question="What are the operational tradeoffs of self-managed mongot?",
        type="technical_research",
        related_project_ids=[PROJECT],
        status="active",
        owner_key_id="0123456789abcdef",
    )
    updated = await research.update_project(
        created.research_project_id,
        expected_revision=created.revision,
        changes={"status": "archived", "related_project_ids": [PROJECT, OTHER_PROJECT]},
    )

    assert created.authorization.project_ids == [PROJECT]
    assert updated.status is ResearchProjectStatus.ARCHIVED
    assert updated.authorization.project_ids == [PROJECT, OTHER_PROJECT]
    assert updated.authorization.owner_key_id == "0123456789abcdef"
    assert updated.revision == 2


@pytest.mark.asyncio
async def test_research_project_service_rejects_missing_links_duplicates_and_stale_updates() -> (
    None
):
    research = service(with_other=False)
    with pytest.raises(RelatedProjectNotFoundError):
        await research.create_project(
            research_project_id="missing-link",
            name="Missing link",
            question="Can a missing V1 project be linked?",
            type="technical_research",
            related_project_ids=[OTHER_PROJECT],
            status="active",
            owner_key_id=None,
        )

    created = await research.create_project(
        research_project_id="one-project",
        name="One project",
        question="Can revisions prevent silent overwrite?",
        type="technical_research",
        related_project_ids=[PROJECT],
        status="active",
        owner_key_id=None,
    )
    with pytest.raises(ResearchProjectAlreadyExistsError):
        await research.create_project(
            research_project_id="one-project",
            name="Duplicate",
            question="Does the deterministic identifier reject duplicates?",
            type="technical_research",
            related_project_ids=[PROJECT],
            status="active",
            owner_key_id=None,
        )
    with pytest.raises(ConcurrencyConflictError):
        await research.update_project(
            created.research_project_id,
            expected_revision=2,
            changes={"name": "stale"},
        )


@pytest.mark.asyncio
async def test_research_project_listing_passes_the_complete_v1_allow_list_to_repository() -> None:
    repository = FakeResearchProjectRepository()
    research = ResearchProjectService(repository, FakeProjectRepository([project(PROJECT)]))
    await research.create_project(
        research_project_id="global",
        name="Global research",
        question="What does the global research workspace contain?",
        type="learning",
        related_project_ids=[],
        status="active",
        owner_key_id=None,
    )
    await research.create_project(
        research_project_id="payments",
        name="Payments research",
        question="Which payment behavior needs investigation?",
        type="technical_research",
        related_project_ids=[PROJECT],
        status="active",
        owner_key_id=None,
    )

    page = await research.list_projects(allowed_project_ids=(PROJECT,), limit=20)

    assert {item.research_project_id for item in page.items} == {"global", "payments"}
