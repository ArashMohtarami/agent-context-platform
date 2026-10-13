"""V2 REST endpoints for creator preference data and content pillars."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from app.api.concurrency import RevisionConflict
from app.api.dependencies import get_creator_service
from app.api.security import authorize_profile, record_creator_write, requires
from app.creator.errors import (
    ContentPillarAlreadyExistsError,
    ContentPillarNotFoundError,
    CreatorProfileAlreadyExistsError,
    CreatorProfileNotFoundError,
)
from app.models.common import Identifier
from app.repositories.errors import ConcurrencyConflictError
from app.schemas.creator import (
    ContentPillarCreateRequest,
    ContentPillarListResponse,
    ContentPillarResponse,
    ContentPillarUpdateRequest,
    CreatorProfileCreateRequest,
    CreatorProfileListResponse,
    CreatorProfileResponse,
    CreatorProfileUpdateRequest,
)
from app.security.principal import Principal
from app.services.creator_service import CreatorService

router = APIRouter(prefix="/api/v2", tags=["creator"])

CreatorServiceDependency = Annotated[CreatorService, Depends(get_creator_service)]
CreatorReader = Annotated[Principal, Depends(requires("creator:read"))]
CreatorWriter = Annotated[Principal, Depends(requires("creator:write"))]
PillarId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


@router.post(
    "/creator-profiles",
    response_model=CreatorProfileResponse,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_409_CONFLICT: {"description": "Creator profile identifier is taken"}},
)
async def create_creator_profile(
    request: CreatorProfileCreateRequest,
    service: CreatorServiceDependency,
    principal: CreatorWriter,
    http_request: Request,
) -> CreatorProfileResponse:
    """Create one profile of inert communication-preference data."""
    await authorize_profile(http_request, principal, request.profile_id)
    try:
        profile = await service.create_profile(**request.model_dump())
    except CreatorProfileAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="creator profile already exists",
        ) from error
    await record_creator_write(http_request, principal, profile.profile_id)
    return CreatorProfileResponse.from_document(profile)


@router.get("/creator-profiles", response_model=CreatorProfileListResponse)
async def list_creator_profiles(
    service: CreatorServiceDependency,
    principal: CreatorReader,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> CreatorProfileListResponse:
    """Page only profiles admitted by the caller's profile grant."""
    page = await service.list_profiles(
        allowed_profile_ids=principal.profiles,
        limit=limit,
        cursor=cursor,
    )
    return CreatorProfileListResponse(
        items=tuple(CreatorProfileResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.get(
    "/creator-profiles/{profile_id}",
    response_model=CreatorProfileResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Creator profile not found"}},
)
async def get_creator_profile(
    profile_id: Identifier,
    service: CreatorServiceDependency,
    principal: CreatorReader,
    http_request: Request,
) -> CreatorProfileResponse:
    """Read one authorized creator profile."""
    await authorize_profile(http_request, principal, profile_id)
    try:
        profile = await service.get_profile(profile_id)
    except CreatorProfileNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="creator profile was not found",
        ) from error
    return CreatorProfileResponse.from_document(profile)


@router.patch(
    "/creator-profiles/{profile_id}",
    response_model=CreatorProfileResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Creator profile not found"},
        status.HTTP_409_CONFLICT: {"description": "Creator profile changed after it was read"},
    },
)
async def update_creator_profile(
    profile_id: Identifier,
    request: CreatorProfileUpdateRequest,
    service: CreatorServiceDependency,
    principal: CreatorWriter,
    http_request: Request,
) -> CreatorProfileResponse:
    """Revision-guard one partial update to inert creator preference data."""
    await authorize_profile(http_request, principal, profile_id)
    changes = request.model_dump(exclude={"expected_revision"}, exclude_unset=True)
    try:
        profile = await service.update_profile(
            profile_id,
            expected_revision=request.expected_revision,
            changes=changes,
        )
    except CreatorProfileNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="creator profile was not found",
        ) from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    await record_creator_write(http_request, principal, profile_id)
    return CreatorProfileResponse.from_document(profile)


@router.post(
    "/content-pillars",
    response_model=ContentPillarResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Creator profile not found"},
        status.HTTP_409_CONFLICT: {"description": "Content pillar identifier is taken"},
    },
)
async def create_content_pillar(
    request: ContentPillarCreateRequest,
    service: CreatorServiceDependency,
    principal: CreatorWriter,
    http_request: Request,
) -> ContentPillarResponse:
    """Create a theme inside one authorized creator-profile boundary."""
    await authorize_profile(http_request, principal, request.profile_id)
    try:
        pillar = await service.create_pillar(**request.model_dump())
    except CreatorProfileNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="creator profile was not found",
        ) from error
    except ContentPillarAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="content pillar already exists",
        ) from error
    await record_creator_write(http_request, principal, request.profile_id)
    return ContentPillarResponse.from_document(pillar)


@router.get("/content-pillars", response_model=ContentPillarListResponse)
async def list_content_pillars(
    profile_id: Identifier,
    service: CreatorServiceDependency,
    principal: CreatorReader,
    http_request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> ContentPillarListResponse:
    """Page pillars from exactly one authorized creator profile."""
    await authorize_profile(http_request, principal, profile_id)
    try:
        page = await service.list_pillars(profile_id, limit=limit, cursor=cursor)
    except CreatorProfileNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="creator profile was not found",
        ) from error
    return ContentPillarListResponse(
        profile_id=profile_id,
        items=tuple(ContentPillarResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.patch(
    "/content-pillars/{pillar_id}",
    response_model=ContentPillarResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Creator profile or content pillar not found"},
        status.HTTP_409_CONFLICT: {"description": "Content pillar changed after it was read"},
    },
)
async def update_content_pillar(
    pillar_id: PillarId,
    request: ContentPillarUpdateRequest,
    service: CreatorServiceDependency,
    principal: CreatorWriter,
    http_request: Request,
) -> ContentPillarResponse:
    """Update a pillar only through its explicit owning profile boundary."""
    await authorize_profile(http_request, principal, request.profile_id)
    changes = request.model_dump(
        exclude={"profile_id", "expected_revision"},
        exclude_unset=True,
    )
    try:
        pillar = await service.update_pillar(
            request.profile_id,
            pillar_id,
            expected_revision=request.expected_revision,
            changes=changes,
        )
    except (CreatorProfileNotFoundError, ContentPillarNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="content pillar was not found",
        ) from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    await record_creator_write(http_request, principal, request.profile_id)
    return ContentPillarResponse.from_document(pillar)
