"""Authorized administration and resolution of explicit V1 project bindings."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.concurrency import RevisionConflict
from app.api.dependencies import get_project_binding_service
from app.api.security import authorize_project, requires
from app.models.agent import ProjectBindingType
from app.models.common import Identifier
from app.repositories.errors import ConcurrencyConflictError
from app.schemas.project_binding import (
    ProjectBindingCreateRequest,
    ProjectBindingListResponse,
    ProjectBindingResponse,
    ProjectBindingUpdateRequest,
    ProjectResolutionRequest,
    ProjectResolutionResponse,
)
from app.security.principal import Principal
from app.services.project_binding_service import (
    ProjectBindingConflictError,
    ProjectBindingDuplicateError,
    ProjectBindingNotFoundError,
    ProjectBindingProjectNotFoundError,
    ProjectBindingService,
)

router = APIRouter(prefix="/api/v4/project-bindings", tags=["project-bindings"])

ProjectBindingServiceDependency = Annotated[
    ProjectBindingService, Depends(get_project_binding_service)
]
ProjectBindingReader = Annotated[Principal, Depends(requires("projects:read"))]
ProjectBindingWriter = Annotated[Principal, Depends(requires("projects:write"))]


@router.post("", response_model=ProjectBindingResponse, status_code=status.HTTP_201_CREATED)
async def create_binding(
    body: ProjectBindingCreateRequest,
    service: ProjectBindingServiceDependency,
    principal: ProjectBindingWriter,
    request: Request,
) -> ProjectBindingResponse:
    """Create one conflict-free binding inside a project the caller may manage."""
    await authorize_project(request, principal, body.project_id)
    try:
        binding = await service.create_binding(**body.model_dump())
    except ProjectBindingProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project was not found",
        ) from error
    except ProjectBindingDuplicateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ProjectBindingConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    return ProjectBindingResponse.from_document(binding)


@router.get("", response_model=ProjectBindingListResponse)
async def list_bindings(
    service: ProjectBindingServiceDependency,
    principal: ProjectBindingReader,
    request: Request,
    project_id: Identifier | None = None,
    binding_type: ProjectBindingType | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> ProjectBindingListResponse:
    """List bindings without exposing projects outside the caller's allow-list."""
    if project_id is not None:
        await authorize_project(request, principal, project_id)
    page = await service.list_bindings(
        project_id=project_id,
        allowed_project_ids=None if project_id is not None else principal.projects,
        binding_type=binding_type,
        limit=limit,
        cursor=cursor,
    )
    return ProjectBindingListResponse(
        items=tuple(ProjectBindingResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.post("/resolve", response_model=ProjectResolutionResponse)
async def resolve_project(
    body: ProjectResolutionRequest,
    service: ProjectBindingServiceDependency,
    principal: ProjectBindingReader,
    request: Request,
) -> ProjectResolutionResponse:
    """Resolve supplied client metadata only through exact configured bindings."""
    if body.project_id is not None:
        # An explicit identifier is a declared project operation, so it gets
        # the existing V1 project authorization error rather than a leak-prone
        # candidate response.
        await authorize_project(request, principal, body.project_id)
    try:
        payload = body.model_dump(exclude={"workspace_alias"})
        if body.workspace_alias is not None:
            payload["workspace_hint"] = body.workspace_alias
        resolution = await service.resolve_project(
            **payload, allowed_project_ids=principal.projects
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    return ProjectResolutionResponse.from_resolution(resolution)


@router.patch("/{binding_id}", response_model=ProjectBindingResponse)
async def update_binding(
    binding_id: Identifier,
    body: ProjectBindingUpdateRequest,
    service: ProjectBindingServiceDependency,
    principal: ProjectBindingWriter,
    request: Request,
) -> ProjectBindingResponse:
    """Change a reviewed binding without moving it outside the caller's grant."""
    try:
        current = await service.get_binding(binding_id)
    except ProjectBindingNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project binding was not found",
        ) from error
    await authorize_project(request, principal, current.project_id)
    if body.project_id is not None:
        await authorize_project(request, principal, body.project_id)
    try:
        binding = await service.update_binding(binding_id, **body.model_dump())
    except ProjectBindingProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project was not found",
        ) from error
    except (ProjectBindingDuplicateError, ProjectBindingConflictError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    return ProjectBindingResponse.from_document(binding)


@router.delete("/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_binding(
    binding_id: Identifier,
    service: ProjectBindingServiceDependency,
    principal: ProjectBindingWriter,
    request: Request,
    expected_revision: Annotated[int, Query(ge=1)],
) -> None:
    """Delete a binding only at the reviewed revision for the requested CLI workflow."""
    try:
        current = await service.get_binding(binding_id)
    except ProjectBindingNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project binding was not found",
        ) from error
    await authorize_project(request, principal, current.project_id)
    try:
        await service.delete_binding(binding_id, expected_revision=expected_revision)
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
