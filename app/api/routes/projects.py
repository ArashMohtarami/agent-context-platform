"""REST endpoints for project boundaries."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import get_project_service
from app.api.security import authorize_project, requires
from app.models.common import Identifier
from app.projects.errors import ProjectAlreadyExistsError, ProjectNotFoundError
from app.schemas.project import ProjectCreateRequest, ProjectListResponse, ProjectResponse
from app.security.principal import Principal
from app.services.project_service import ProjectService

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


ProjectServiceDependency = Annotated[ProjectService, Depends(get_project_service)]
# Creating a boundary is not something a read scope should permit, so it has
# its own; reading the directory is the ordinary grant.
ProjectReader = Annotated[Principal, Depends(requires("projects:read"))]
ProjectWriter = Annotated[Principal, Depends(requires("projects:write"))]


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_409_CONFLICT: {"description": "Project identifier is taken"}},
)
async def create_project(
    request: ProjectCreateRequest,
    service: ProjectServiceDependency,
    principal: ProjectWriter,
    http_request: Request,
) -> ProjectResponse:
    """Create one project boundary."""
    # A key narrowed to some projects cannot create a different one, which is
    # what stops a narrow grant from widening itself.
    await authorize_project(http_request, principal, request.project_id)
    try:
        project = await service.create_project(
            project_id=request.project_id,
            name=request.name,
            description=request.description,
            repository=request.repository,
            environment=request.environment,
            tags=request.tags,
        )
    except ProjectAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="project already exists",
        ) from error
    return ProjectResponse.from_document(project)


@router.get(
    "",
    response_model=ProjectListResponse,
    dependencies=[Depends(requires("projects:read"))],
)
async def list_projects(
    service: ProjectServiceDependency,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> ProjectListResponse:
    """List projects using bounded cursor pagination."""
    page = await service.list_projects(limit=limit, cursor=cursor)
    return ProjectListResponse(
        items=tuple(ProjectResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Project not found"}},
)
async def get_project(
    project_id: Identifier,
    service: ProjectServiceDependency,
    principal: ProjectReader,
    http_request: Request,
) -> ProjectResponse:
    """Load one project boundary."""
    await authorize_project(http_request, principal, project_id)
    try:
        project = await service.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project was not found",
        ) from error
    return ProjectResponse.from_document(project)
