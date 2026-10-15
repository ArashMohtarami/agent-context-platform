"""Business rules for project boundaries."""

from collections.abc import Sequence
from typing import Protocol

from app.models.common import Page
from app.models.project import ProjectDocument
from app.projects.errors import ProjectAlreadyExistsError, ProjectNotFoundError
from app.repositories.errors import DuplicateDocumentError
from app.security.tenant_service import tenant_service

DEFAULT_PAGE_SIZE = 20


class ProjectRepository(Protocol):
    """Persistence required by the project service."""

    async def create(self, document: ProjectDocument) -> ProjectDocument: ...

    async def get(self, project_id: str) -> ProjectDocument | None: ...

    async def list(
        self,
        *,
        limit: int = ...,
        cursor: str | None = None,
    ) -> Page[ProjectDocument]: ...


@tenant_service
class ProjectService:
    """Own project identity, which every piece of context depends on."""

    def __init__(self, projects: ProjectRepository) -> None:
        self._projects = projects

    async def create_project(
        self,
        *,
        project_id: str,
        name: str,
        description: str,
        repository: str,
        environment: str,
        tags: Sequence[str] = (),
    ) -> ProjectDocument:
        """Create one project, refusing to take an identifier twice.

        The identifier is the boundary every context item is filed under, so
        reusing one would silently merge two systems' knowledge.
        """
        document = ProjectDocument(
            project_id=project_id,
            name=name,
            description=description,
            repository=repository,
            environment=environment,
            tags=list(tags),
        )
        try:
            return await self._projects.create(document)
        except DuplicateDocumentError as error:
            raise ProjectAlreadyExistsError(f"project {project_id} already exists") from error

    async def get_project(self, project_id: str) -> ProjectDocument:
        """Load one project or report that it does not exist."""
        project = await self._projects.get(project_id)
        if project is None:
            raise ProjectNotFoundError(f"project {project_id} was not found")
        return project

    async def list_projects(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ProjectDocument]:
        """List projects with bounded cursor pagination."""
        return await self._projects.list(limit=limit, cursor=cursor)
