"""V5 administrative endpoints for generic MCP client profiles."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.concurrency import RevisionConflict
from app.api.security import requires
from app.models.common import Identifier
from app.repositories.errors import ConcurrencyConflictError
from app.schemas.client_profile import (
    ClientProfileAssignmentRequest,
    ClientProfileAssignmentResponse,
    ClientProfileCreateRequest,
    ClientProfileListResponse,
    ClientProfileResponse,
    ClientProfileUpdateRequest,
)
from app.services.client_profile_service import (
    ClientNotFoundForProfileError,
    ClientProfileExistsError,
    ClientProfileNotFoundError,
    ClientProfileService,
)

router = APIRouter(
    prefix="/api/v5/client-profiles",
    tags=["client-profiles"],
    dependencies=[Depends(requires("agent:manage"))],
)


async def service(request: Request) -> ClientProfileService:
    return cast(ClientProfileService, request.app.state.client_profile_service)


Service = Annotated[ClientProfileService, Depends(service)]


@router.post("", response_model=ClientProfileResponse, status_code=status.HTTP_201_CREATED)
async def create(body: ClientProfileCreateRequest, profiles: Service) -> ClientProfileResponse:
    try:
        item = await profiles.create_profile(**body.model_dump())
    except ClientProfileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ClientProfileResponse.from_document(item)


@router.get("", response_model=ClientProfileListResponse)
async def list_profiles(
    profiles: Service, limit: Annotated[int, Query(ge=1, le=50)] = 20, cursor: str | None = None
) -> ClientProfileListResponse:
    page = await profiles.list_profiles(limit=limit, cursor=cursor)
    return ClientProfileListResponse(
        items=tuple(ClientProfileResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.get("/{profile_id}", response_model=ClientProfileResponse)
async def get(profile_id: Identifier, profiles: Service) -> ClientProfileResponse:
    try:
        return ClientProfileResponse.from_document(await profiles.get_profile(profile_id))
    except ClientProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail="client profile was not found") from error


@router.patch("/{profile_id}", response_model=ClientProfileResponse)
async def update(
    profile_id: Identifier, body: ClientProfileUpdateRequest, profiles: Service
) -> ClientProfileResponse:
    try:
        item = await profiles.update_profile(profile_id, **body.model_dump())
    except ClientProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail="client profile was not found") from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ClientProfileResponse.from_document(item)


@router.post(
    "/{profile_id}/assignments",
    response_model=ClientProfileAssignmentResponse,
)
async def assign(
    profile_id: Identifier,
    body: ClientProfileAssignmentRequest,
    profiles: Service,
) -> ClientProfileAssignmentResponse:
    try:
        item = await profiles.assign_profile(profile_id, **body.model_dump())
    except ClientProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail="client profile was not found") from error
    except ClientNotFoundForProfileError as error:
        raise HTTPException(status_code=404, detail="agent client was not found") from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ClientProfileAssignmentResponse.from_document(item)
