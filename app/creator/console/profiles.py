"""Creator preference profiles and pillars, distinct from human roles and templates."""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api import dependencies as services
from app.api.routes.research_console import gate
from app.creator.console.boundary import CreatorRoute, profile
from app.creator.console.contracts import PageResponse
from app.creator.console.service import CreatorConsole, Kind, Record
from app.identity.browser import CurrentBrowser
from app.models.common import Identifier
from app.schemas import creator as dto
from app.security.tenant_policy import current_policy

router = APIRouter(route_class=CreatorRoute)
Limit = Annotated[int, Query(ge=1, le=50)]


@router.get(
    "/profiles", response_model=dto.CreatorProfileListResponse, dependencies=gate("creator:read")
)
async def profiles(
    request: Request, context: CurrentBrowser, limit: Limit = 20, cursor: str | None = None
) -> dto.CreatorProfileListResponse:
    p = current_policy()
    page = await services.get_creator_service(request).list_profiles(
        allowed_profile_ids=p.selector("creator:read", "creator_profile") if p else (),
        limit=limit,
        cursor=cursor,
    )
    return dto.CreatorProfileListResponse(
        items=tuple(dto.CreatorProfileResponse.from_document(d) for d in page.items),
        next_cursor=page.next_cursor,
    )


@router.post(
    "/profiles",
    response_model=dto.CreatorProfileResponse,
    status_code=201,
    dependencies=gate("creator:write"),
)
async def create_profile(
    body: dto.CreatorProfileCreateRequest, request: Request, context: CurrentBrowser
) -> dto.CreatorProfileResponse:
    profile(body.profile_id)
    row = await services.get_creator_service(request).create_profile(
        **{k: getattr(body, k) for k in type(body).model_fields}
    )
    return dto.CreatorProfileResponse.from_document(row)


@router.get(
    "/profiles/{profile_id}",
    response_model=dto.CreatorProfileResponse,
    dependencies=gate("creator:read"),
)
async def get_profile(
    profile_id: Identifier, request: Request, context: CurrentBrowser
) -> dto.CreatorProfileResponse:
    profile(profile_id)
    return dto.CreatorProfileResponse.from_document(
        await services.get_creator_service(request).get_profile(profile_id)
    )


@router.patch(
    "/profiles/{profile_id}",
    response_model=dto.CreatorProfileResponse,
    dependencies=gate("creator:write"),
)
async def update_profile(
    profile_id: Identifier,
    body: dto.CreatorProfileUpdateRequest,
    request: Request,
    context: CurrentBrowser,
) -> dto.CreatorProfileResponse:
    profile(profile_id)
    row = await services.get_creator_service(request).update_profile(
        profile_id,
        expected_revision=body.expected_revision,
        changes={k: getattr(body, k) for k in body.model_fields_set - {"expected_revision"}},
    )
    return dto.CreatorProfileResponse.from_document(row)


@router.get(
    "/profiles/{profile_id}/records/{kind}",
    response_model=PageResponse[Record],
    dependencies=gate("content:read"),
)
async def records(
    profile_id: Identifier,
    kind: Kind,
    request: Request,
    context: CurrentBrowser,
    limit: Limit = 20,
    cursor: str | None = None,
) -> PageResponse[Record]:
    return PageResponse[Record].model_validate(
        await CreatorConsole(request, context).page(profile_id, kind, limit, cursor)
    )


@router.post(
    "/pillars",
    response_model=dto.ContentPillarResponse,
    status_code=201,
    dependencies=gate("creator:write"),
)
async def create_pillar(
    body: dto.ContentPillarCreateRequest, request: Request, context: CurrentBrowser
) -> dto.ContentPillarResponse:
    profile(body.profile_id)
    row = await services.get_creator_service(request).create_pillar(**body.model_dump())
    return dto.ContentPillarResponse.from_document(row)


@router.patch(
    "/pillars/{pillar_id}",
    response_model=dto.ContentPillarResponse,
    dependencies=gate("creator:write"),
)
async def update_pillar(
    pillar_id: Identifier,
    body: dto.ContentPillarUpdateRequest,
    request: Request,
    context: CurrentBrowser,
) -> dto.ContentPillarResponse:
    profile(body.profile_id)
    row = await services.get_creator_service(request).update_pillar(
        body.profile_id,
        pillar_id,
        expected_revision=body.expected_revision,
        changes=body.model_dump(exclude={"profile_id", "expected_revision"}, exclude_unset=True),
    )
    return dto.ContentPillarResponse.from_document(row)


@router.get(
    "/profiles/{profile_id}/pillars",
    response_model=dto.ContentPillarListResponse,
    dependencies=gate("creator:read"),
)
async def pillars(
    profile_id: Identifier,
    request: Request,
    context: CurrentBrowser,
    limit: Limit = 20,
    cursor: str | None = None,
) -> dto.ContentPillarListResponse:
    profile(profile_id)
    page = await services.get_creator_service(request).list_pillars(
        profile_id, limit=limit, cursor=cursor
    )
    return dto.ContentPillarListResponse(
        profile_id=profile_id,
        items=tuple(dto.ContentPillarResponse.from_document(x) for x in page.items),
        next_cursor=page.next_cursor,
    )
