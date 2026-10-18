"""Immutable drafts, independent approval, manual export and local receipts."""

from fastapi import APIRouter, Request

from app.api import dependencies as services
from app.api.routes.research_console import gate
from app.content.errors import ContentApprovalStateError
from app.creator.console.boundary import CreatorRoute, profile
from app.creator.console.contracts import Draft, Export, Receipt, Version
from app.creator.console.profiles import Limit
from app.creator.console.service import CreatorConsole
from app.identity.browser import CurrentBrowser
from app.models.common import Identifier
from app.models.content import ContentPublicationProvider, ContentStatus
from app.repositories.errors import ConcurrencyConflictError
from app.schemas import content as dto

router = APIRouter(route_class=CreatorRoute)


@router.post(
    "/items",
    response_model=dto.ContentItemResponse,
    status_code=201,
    dependencies=gate("content:write"),
)
async def draft(body: Draft, request: Request, context: CurrentBrowser) -> dto.ContentItemResponse:
    c = CreatorConsole(request, context)
    profile(body.profile_id)
    c.projects(body.project_ids)
    c.source_refs(body.source_refs)
    row = await c.content.create_content(**{k: getattr(body, k) for k in type(body).model_fields})
    return dto.ContentItemResponse.from_document(row)


@router.get(
    "/profiles/{profile_id}/items/{content_id}",
    response_model=dto.ContentItemResponse,
    dependencies=gate("content:read"),
)
async def item(
    profile_id: Identifier, content_id: Identifier, request: Request, context: CurrentBrowser
) -> dto.ContentItemResponse:
    return dto.ContentItemResponse.from_document(
        await CreatorConsole(request, context).item(profile_id, content_id)
    )


@router.patch(
    "/items/{content_id}",
    response_model=dto.ContentItemResponse,
    dependencies=gate("content:write"),
)
async def update(
    content_id: Identifier,
    body: dto.ContentItemUpdateRequest,
    request: Request,
    context: CurrentBrowser,
) -> dto.ContentItemResponse:
    c = CreatorConsole(request, context)
    await c.item(body.profile_id, content_id)
    if body.status in {ContentStatus.APPROVED, ContentStatus.PUBLISHED}:
        raise ContentApprovalStateError("use the explicit approval or manual receipt action")
    if body.project_ids is not None:
        c.projects(body.project_ids)
    if body.source_refs is not None:
        c.source_refs(body.source_refs)
    row = await c.content.update_content(
        body.profile_id,
        content_id,
        expected_revision=body.expected_revision,
        changes=body.model_dump(exclude={"profile_id", "expected_revision"}, exclude_unset=True),
    )
    return dto.ContentItemResponse.from_document(row)


@router.get(
    "/profiles/{profile_id}/items/{content_id}/versions",
    response_model=dto.ContentVersionListResponse,
    dependencies=gate("content:read"),
)
async def versions(
    profile_id: Identifier,
    content_id: Identifier,
    request: Request,
    context: CurrentBrowser,
    limit: Limit = 20,
    cursor: str | None = None,
) -> dto.ContentVersionListResponse:
    c = CreatorConsole(request, context)
    await c.item(profile_id, content_id)
    page = await c.content.list_versions(
        profile_id, content_id=content_id, limit=limit, cursor=cursor
    )
    return dto.ContentVersionListResponse(
        profile_id=profile_id,
        content_id=content_id,
        items=tuple(dto.ContentVersionResponse.from_document(d) for d in page.items),
        next_cursor=page.next_cursor,
    )


@router.post(
    "/items/{content_id}/versions",
    response_model=dto.ContentItemResponse,
    dependencies=gate("content:write"),
)
async def version(
    content_id: Identifier, body: Version, request: Request, context: CurrentBrowser
) -> dto.ContentItemResponse:
    c = CreatorConsole(request, context)
    await c.item(body.profile_id, content_id)
    if body.rollback_to_version is not None:
        row = await c.content.rollback_content(
            body.profile_id,
            content_id,
            rollback_to_version=body.rollback_to_version,
            expected_revision=body.expected_revision,
            change_reason=body.change_reason,
            created_by=None,
        )
    else:
        if body.body is None:
            raise ValueError("a body is required")
        row = await c.content.create_version(
            body.profile_id,
            content_id,
            body=body.body,
            expected_revision=body.expected_revision,
            change_reason=body.change_reason,
            created_by=None,
        )
    return dto.ContentItemResponse.from_document(row)


@router.post(
    "/items/{content_id}/scan",
    response_model=dto.ContentPublicationReviewResponse,
    dependencies=gate("content:read"),
)
async def scan(
    content_id: Identifier,
    body: dto.ContentPublicationReviewRequest,
    request: Request,
    context: CurrentBrowser,
) -> dto.ContentPublicationReviewResponse:
    row = await CreatorConsole(request, context).item(body.profile_id, content_id)
    result = services.get_content_publication_service(request).review_item_for_publication(row)
    return dto.ContentPublicationReviewResponse.from_value(result, current_status=row.status)


@router.post(
    "/items/{content_id}/approve",
    response_model=dto.ContentApprovalResponse,
    dependencies=gate("content:write"),
)
async def approve(
    content_id: Identifier,
    body: dto.ContentApprovalRequest,
    request: Request,
    context: CurrentBrowser,
) -> dto.ContentApprovalResponse:
    await CreatorConsole(request, context).item(body.profile_id, content_id)
    row, result = await services.get_content_publication_service(request).approve_content(
        body.profile_id, content_id, expected_revision=body.expected_revision
    )
    return dto.ContentApprovalResponse(
        content=dto.ContentItemResponse.from_document(row),
        review=dto.ContentPublicationReviewResponse.from_value(result, current_status=row.status),
    )


@router.post(
    "/items/{content_id}/export",
    response_model=dto.ContentExportResponse,
    dependencies=gate("content:read"),
)
async def export(
    content_id: Identifier, body: Export, request: Request, context: CurrentBrowser
) -> dto.ContentExportResponse:
    row = await CreatorConsole(request, context).item(body.profile_id, content_id)
    if row.revision != body.expected_revision:
        raise ConcurrencyConflictError("content changed")
    if row.status not in {ContentStatus.APPROVED, ContentStatus.PUBLISHED}:
        raise ContentApprovalStateError("approval is required before export")
    result = services.get_content_export_service(request).export_content(row, mode="stdout")
    return dto.ContentExportResponse.from_value(result)


@router.post(
    "/items/{content_id}/receipt",
    response_model=dto.ContentPublishResponse,
    dependencies=gate("content:publish"),
)
async def receipt(
    content_id: Identifier, body: Receipt, request: Request, context: CurrentBrowser
) -> dto.ContentPublishResponse:
    await CreatorConsole(request, context).item(body.profile_id, content_id)
    result = await services.get_content_publishing_service(request).publish_content(
        body.profile_id,
        content_id,
        expected_revision=body.expected_revision,
        provider=ContentPublicationProvider.MANUAL,
        idempotency_key=body.idempotency_key,
    )
    r = result.receipt
    return dto.ContentPublishResponse(
        content=dto.ContentItemResponse.from_document(result.item),
        provider=r.provider,
        external_id=r.external_id,
        external_url=r.external_url,
        published_at=r.published_at,
        idempotency_key=r.idempotency_key,
        already_published=result.already_published,
    )


@router.get(
    "/profiles/{profile_id}/items/{content_id}/metrics",
    response_model=dto.ContentMetricListResponse,
    dependencies=gate("content:read"),
)
async def metrics(
    profile_id: Identifier,
    content_id: Identifier,
    request: Request,
    context: CurrentBrowser,
    limit: Limit = 20,
    cursor: str | None = None,
) -> dto.ContentMetricListResponse:
    await CreatorConsole(request, context).item(profile_id, content_id)
    result = await services.get_content_metrics_service(request).list_metrics(
        profile_id=profile_id, content_id=content_id, limit=limit, cursor=cursor
    )
    return dto.ContentMetricListResponse(
        profile_id=profile_id,
        content_id=content_id,
        items=tuple(dto.ContentMetricResponse.from_document(x) for x in result.page.items),
        next_cursor=result.page.next_cursor,
    )


@router.post(
    "/items/{content_id}/metrics",
    response_model=dto.ContentMetricResponse,
    status_code=201,
    dependencies=gate("content:write"),
)
async def metric(
    content_id: Identifier,
    body: dto.ContentMetricCreateRequest,
    request: Request,
    context: CurrentBrowser,
) -> dto.ContentMetricResponse:
    await CreatorConsole(request, context).item(body.profile_id, content_id)
    row = await services.get_content_metrics_service(request).create_metric(
        content_id=content_id, **body.model_dump()
    )
    return dto.ContentMetricResponse.from_document(row)
