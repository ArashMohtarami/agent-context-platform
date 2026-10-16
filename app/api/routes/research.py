"""V3 Phase 1 REST endpoints for research-project metadata only."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.concurrency import RevisionConflict
from app.api.dependencies import get_research_project_service
from app.api.security import authorize_research_project_links, record_research_write, requires
from app.models.common import Identifier
from app.models.research import ResearchProjectStatus
from app.repositories.errors import ConcurrencyConflictError
from app.research.errors import (
    RelatedProjectNotFoundError,
    ResearchProjectAlreadyExistsError,
    ResearchProjectNotFoundError,
)
from app.schemas.research import (
    ResearchProjectCreateRequest,
    ResearchProjectListResponse,
    ResearchProjectResponse,
    ResearchProjectUpdateRequest,
)
from app.security.principal import Principal
from app.services.research_project_service import ResearchProjectService

router = APIRouter(prefix="/api/v3/research/projects", tags=["research"])

ResearchProjectServiceDependency = Annotated[
    ResearchProjectService, Depends(get_research_project_service)
]
ResearchReader = Annotated[Principal, Depends(requires("research:read"))]
ResearchWriter = Annotated[Principal, Depends(requires("research:write"))]


@router.post(
    "",
    response_model=ResearchProjectResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "A related project was not found"},
        status.HTTP_409_CONFLICT: {"description": "Research project identifier is taken"},
    },
)
async def create_research_project(
    request: ResearchProjectCreateRequest,
    service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    http_request: Request,
) -> ResearchProjectResponse:
    """Create inert research metadata, after authorization for every V1 link."""
    await authorize_research_project_links(http_request, principal, request.related_project_ids)
    try:
        project = await service.create_project(
            research_project_id=request.research_project_id,
            name=request.name,
            question=request.question,
            type=request.type,
            related_project_ids=request.related_project_ids,
            status=ResearchProjectStatus(request.status),
            owner_key_id=principal.key_id if principal.authenticated else None,
        )
    except RelatedProjectNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ResearchProjectAlreadyExistsError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    await record_research_write(http_request, principal, project.research_project_id)
    return ResearchProjectResponse.from_document(project)


@router.get("", response_model=ResearchProjectListResponse)
async def list_research_projects(
    service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    status_filter: Annotated[
        str | None,
        Query(alias="status", pattern="^(active|archived)$"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> ResearchProjectListResponse:
    """Page research projects after filtering by the caller's full V1 project grant."""
    selected_status = ResearchProjectStatus(status_filter) if status_filter is not None else None
    page = await service.list_projects(
        allowed_project_ids=principal.project_selector(),
        status=selected_status,
        limit=limit,
        cursor=cursor,
    )
    return ResearchProjectListResponse(
        items=tuple(ResearchProjectResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{research_project_id}",
    response_model=ResearchProjectResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Research project not found"}},
)
async def get_research_project(
    research_project_id: Identifier,
    service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    http_request: Request,
) -> ResearchProjectResponse:
    """Read one research project only after every linked V1 project is allowed."""
    try:
        project = await service.get_project(research_project_id)
    except ResearchProjectNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await authorize_research_project_links(http_request, principal, project.related_project_ids)
    return ResearchProjectResponse.from_document(project)


@router.patch(
    "/{research_project_id}",
    response_model=ResearchProjectResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Research project or related project not found"},
        status.HTTP_409_CONFLICT: {"description": "Research project changed after it was read"},
    },
)
async def update_research_project(
    research_project_id: Identifier,
    request: ResearchProjectUpdateRequest,
    service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    http_request: Request,
) -> ResearchProjectResponse:
    """Revision-guard a metadata change without changing the project's owner."""
    try:
        current = await service.get_project(research_project_id)
    except ResearchProjectNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await authorize_research_project_links(http_request, principal, current.related_project_ids)
    if request.related_project_ids is not None:
        await authorize_research_project_links(http_request, principal, request.related_project_ids)
    changes = request.model_dump(exclude={"expected_revision"}, exclude_unset=True)
    if "status" in changes:
        changes["status"] = ResearchProjectStatus(str(changes["status"]))
    try:
        project = await service.update_project(
            research_project_id,
            expected_revision=request.expected_revision,
            changes=changes,
        )
    except (ResearchProjectNotFoundError, RelatedProjectNotFoundError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    await record_research_write(http_request, principal, project.research_project_id)
    return ResearchProjectResponse.from_document(project)
