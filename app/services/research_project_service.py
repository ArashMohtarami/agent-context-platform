"""Business rules for V3 Phase 1 research-project boundaries.

This service owns only durable project metadata and V1 project-link validity.
It deliberately does not register sources, acquire content, retrieve evidence,
evaluate claims, or promote any research result.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from app.models.common import Page
from app.models.project import ProjectDocument
from app.models.research import (
    ResearchAuthorization,
    ResearchProjectDocument,
    ResearchProjectStatus,
    ResearchProjectType,
)
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError
from app.research.errors import (
    RelatedProjectNotFoundError,
    ResearchProjectAlreadyExistsError,
    ResearchProjectNotFoundError,
)


class ResearchProjectRepository(Protocol):
    """Persistence the Phase 1 research-project service requires."""

    async def create(self, document: ResearchProjectDocument) -> ResearchProjectDocument: ...

    async def get(self, research_project_id: str) -> ResearchProjectDocument | None: ...

    async def list(
        self,
        *,
        status: ResearchProjectStatus | None = ...,
        allowed_project_ids: tuple[str, ...] | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[ResearchProjectDocument]: ...

    async def update(
        self, document: ResearchProjectDocument, *, expected_revision: int
    ) -> ResearchProjectDocument: ...


class ProjectLookup(Protocol):
    """The V1 project lookup needed to prevent dangling private links."""

    async def get(self, project_id: str) -> ProjectDocument | None: ...


class ResearchProjectService:
    """Create, read, list, and revision-guard research project metadata."""

    def __init__(self, projects: ResearchProjectRepository, v1_projects: ProjectLookup) -> None:
        self._projects = projects
        self._v1_projects = v1_projects

    async def create_project(
        self,
        *,
        research_project_id: str,
        name: str,
        question: str,
        type: ResearchProjectType,
        related_project_ids: Sequence[str],
        status: ResearchProjectStatus,
        owner_key_id: str | None,
    ) -> ResearchProjectDocument:
        """Create a project only after every V1 relationship is real."""
        related = list(related_project_ids)
        await self._require_related_projects(related)
        document = ResearchProjectDocument(
            research_project_id=research_project_id,
            name=name,
            question=question,
            type=type,
            related_project_ids=related,
            status=status,
            authorization=ResearchAuthorization(
                project_ids=related,
                owner_key_id=owner_key_id,
            ),
        )
        try:
            return await self._projects.create(document)
        except DuplicateDocumentError as error:
            raise ResearchProjectAlreadyExistsError("research project already exists") from error

    async def get_project(self, research_project_id: str) -> ResearchProjectDocument:
        """Load one metadata boundary without making an access decision."""
        project = await self._projects.get(research_project_id)
        if project is None:
            raise ResearchProjectNotFoundError("research project was not found")
        return project

    async def list_projects(
        self,
        *,
        allowed_project_ids: tuple[str, ...] | None,
        status: ResearchProjectStatus | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ResearchProjectDocument]:
        """Return only projects whose complete V1 link set is authorized."""
        return await self._projects.list(
            status=status,
            allowed_project_ids=allowed_project_ids,
            limit=limit,
            cursor=cursor,
        )

    async def update_project(
        self,
        research_project_id: str,
        *,
        expected_revision: int,
        changes: Mapping[str, object],
    ) -> ResearchProjectDocument:
        """Apply a validated partial update while retaining ownership metadata."""
        current = await self.get_project(research_project_id)
        if current.revision != expected_revision:
            raise ConcurrencyConflictError("research project changed after it was read")
        related = changes.get("related_project_ids", current.related_project_ids)
        if not isinstance(related, list):
            raise ValueError("related_project_ids must be a list")
        await self._require_related_projects(related)
        payload = current.model_dump(by_alias=True, mode="python")
        payload.update(changes)
        payload["authorization"] = ResearchAuthorization(
            project_ids=related,
            owner_key_id=current.authorization.owner_key_id,
        )
        updated = ResearchProjectDocument.model_validate(payload, context={"from_storage": True})
        return await self._projects.update(updated, expected_revision=expected_revision)

    async def _require_related_projects(self, related_project_ids: Sequence[str]) -> None:
        """Reject dangling links after caller authorization has already run."""
        for project_id in related_project_ids:
            if await self._v1_projects.get(project_id) is None:
                raise RelatedProjectNotFoundError("a related project was not found")
