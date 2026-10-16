"""V2 creator-content storage, retrieval, discovery, and brief endpoints."""

from collections.abc import Iterable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import AwareDatetime

from app.api.concurrency import RevisionConflict
from app.api.dependencies import (
    get_content_discovery_service,
    get_content_export_service,
    get_content_metrics_service,
    get_content_novelty_service,
    get_content_publication_service,
    get_content_publishing_service,
    get_content_repurpose_service,
    get_content_research_service,
    get_content_retrieval_service,
    get_content_service,
    get_research_project_service,
)
from app.api.security import (
    authorize_profile,
    authorize_project,
    authorize_research_project_links,
    record_content_approval,
    record_content_publish,
    record_content_write,
    requires,
)
from app.content.errors import (
    ContentAlreadyPublishedError,
    ContentApprovalBlockedError,
    ContentApprovalStateError,
    ContentBriefAlreadyExistsError,
    ContentBriefNotFoundError,
    ContentIdeaAlreadyExistsError,
    ContentIdeaNotFoundError,
    ContentItemAlreadyExistsError,
    ContentItemNotFoundError,
    ContentMetricAlreadyExistsError,
    ContentPublishingStateError,
    ContentRetrievalUnavailableError,
    ContentVersionConflictError,
    ContentVersionNotFoundError,
    InvalidContentLifecycleTransitionError,
    PublisherDeliveryError,
    PublisherUnavailableError,
)
from app.content.platforms.base import (
    ContentPlatformValidationError,
    UnsupportedContentPlatformError,
)
from app.creator.errors import ContentPillarNotFoundError, CreatorProfileNotFoundError
from app.db.content_indexes import ContentQueryFilters
from app.models.common import Identifier
from app.models.content import (
    ContentBriefDocument,
    ContentIdeaDocument,
    ContentItemDocument,
    ContentPlatform,
    ContentSourceReference,
    ContentStatus,
)
from app.projects.errors import ProjectNotFoundError
from app.repositories.errors import ConcurrencyConflictError
from app.research.errors import ResearchProjectNotFoundError
from app.schemas.content import (
    ContentApprovalRequest,
    ContentApprovalResponse,
    ContentBriefCreateRequest,
    ContentBriefPreparationResponse,
    ContentBriefPrepareRequest,
    ContentBriefResponse,
    ContentExportRequest,
    ContentExportResponse,
    ContentFactReviewRequest,
    ContentFactReviewResponse,
    ContentIdeaCreateRequest,
    ContentIdeaDiscoveryRequest,
    ContentIdeaDiscoveryResponse,
    ContentIdeaListResponse,
    ContentIdeaResponse,
    ContentIdeaUpdateRequest,
    ContentItemCreateRequest,
    ContentItemResponse,
    ContentItemUpdateRequest,
    ContentMetricCreateRequest,
    ContentMetricListResponse,
    ContentMetricResponse,
    ContentNoveltyRequest,
    ContentNoveltyResponse,
    ContentPublicationReviewRequest,
    ContentPublicationReviewResponse,
    ContentPublishRequest,
    ContentPublishResponse,
    ContentRepurposeRequest,
    ContentRepurposeResponse,
    ContentSearchRequest,
    ContentSearchResponse,
    ContentVersionCreateRequest,
    ContentVersionListResponse,
    ContentVersionResponse,
)
from app.security.principal import Principal
from app.services.content_discovery_service import ContentDiscoveryService
from app.services.content_export_service import ContentExportService
from app.services.content_metrics_service import ContentMetricsService
from app.services.content_novelty_service import ContentNoveltyService
from app.services.content_publication_service import ContentPublicationService
from app.services.content_publishing_service import ContentPublishingService
from app.services.content_repurpose_service import ContentRepurposeService
from app.services.content_research_service import ContentResearchIntegrationService
from app.services.content_retrieval_service import ContentRetrievalService
from app.services.content_service import ContentService
from app.services.research_project_service import ResearchProjectService

router = APIRouter(prefix="/api/v2", tags=["content"])

ContentServiceDependency = Annotated[ContentService, Depends(get_content_service)]
ContentRetrievalServiceDependency = Annotated[
    ContentRetrievalService, Depends(get_content_retrieval_service)
]
ContentResearchServiceDependency = Annotated[
    ContentResearchIntegrationService, Depends(get_content_research_service)
]
ResearchProjectServiceDependency = Annotated[
    ResearchProjectService, Depends(get_research_project_service)
]
ContentMetricsServiceDependency = Annotated[
    ContentMetricsService, Depends(get_content_metrics_service)
]
ContentDiscoveryServiceDependency = Annotated[
    ContentDiscoveryService, Depends(get_content_discovery_service)
]
ContentExportServiceDependency = Annotated[
    ContentExportService, Depends(get_content_export_service)
]
ContentNoveltyServiceDependency = Annotated[
    ContentNoveltyService, Depends(get_content_novelty_service)
]
ContentRepurposeServiceDependency = Annotated[
    ContentRepurposeService, Depends(get_content_repurpose_service)
]
ContentPublicationServiceDependency = Annotated[
    ContentPublicationService, Depends(get_content_publication_service)
]
ContentPublishingServiceDependency = Annotated[
    ContentPublishingService, Depends(get_content_publishing_service)
]
ContentReader = Annotated[Principal, Depends(requires("content:read"))]
ContentWriter = Annotated[Principal, Depends(requires("content:write"))]
ContentPublisher = Annotated[Principal, Depends(requires("content:publish"))]
ContentId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
BriefId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
IdeaId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


async def _authorize_research_project(
    research_project_id: str,
    service: ResearchProjectService,
    principal: Principal,
    http_request: Request,
) -> None:
    """Require every V1 project linked to research before V2 exposes its evidence."""
    try:
        research_project = await service.get_project(research_project_id)
    except ResearchProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research project was not found"
        ) from error
    await authorize_research_project_links(
        http_request, principal, research_project.related_project_ids
    )


async def _authorize_projects(
    request: Request,
    principal: Principal,
    project_ids: Iterable[str],
    source_refs: Iterable[ContentSourceReference],
) -> None:
    """Require the project grant for every persisted provenance reference."""
    identifiers = set(project_ids)
    identifiers.update(ref.project_id for ref in source_refs if ref.project_id is not None)
    for project_id in sorted(identifiers):
        await authorize_project(request, principal, project_id)


@router.post("/content/search", response_model=ContentSearchResponse)
async def search_content(
    request: ContentSearchRequest,
    service: ContentRetrievalServiceDependency,
    principal: ContentReader,
    http_request: Request,
) -> ContentSearchResponse:
    """Search one creator profile without disclosing inaccessible project-derived prose."""
    await authorize_profile(http_request, principal, request.profile_id)
    await authorize_project(http_request, principal, request.project_id)
    try:
        result = await service.search(
            profile_id=request.profile_id,
            query=request.query,
            filters=ContentQueryFilters(
                profile_id=request.profile_id,
                platform=request.platform,
                project_id=request.project_id,
                status=None if request.status is None else request.status.value,
                pillar_id=request.pillar_id,
                tags=tuple(request.tags),
            ),
            retrieval_profile=request.retrieval_profile,
            method=request.method,
            kinds=tuple(request.kinds),
            limit=request.limit,
            token_budget=request.token_budget,
        )
    except ContentRetrievalUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    # A content record can cite more than one project.  Its profile is allowed
    # above, but every referenced project must also be granted before any prose
    # reaches the response.  Filtering is intentionally silent to avoid
    # confirming that private material exists.
    visible = tuple(
        item
        for item in result.items
        if all(principal.allows_project(project_id) for project_id in item.private_project_ids)
    )
    return ContentSearchResponse.from_result(result, visible)


@router.post("/content/novelty", response_model=ContentNoveltyResponse)
async def check_content_novelty(
    request: ContentNoveltyRequest,
    service: ContentNoveltyServiceDependency,
    principal: ContentReader,
    http_request: Request,
) -> ContentNoveltyResponse:
    """Return explainable historical overlap, never a claim of objective novelty."""
    await authorize_profile(http_request, principal, request.profile_id)
    await authorize_project(http_request, principal, request.project_id)
    try:
        result = await service.check_content_novelty(
            profile_id=request.profile_id,
            title=request.title,
            description=request.description,
            tags=request.tags,
            pillar_id=request.pillar_id,
            platform=request.platform,
            project_id=request.project_id,
            method=request.method,
            limit=request.limit,
            token_budget=request.token_budget,
            allowed_project_ids=None
            if principal.project_selector() is None
            else frozenset(principal.project_selector() or ()),
        )
    except ContentRetrievalUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ContentNoveltyResponse.from_value(result)


@router.post("/content-ideas/discover", response_model=ContentIdeaDiscoveryResponse)
async def discover_content_ideas(
    request: ContentIdeaDiscoveryRequest,
    service: ContentDiscoveryServiceDependency,
    principal: ContentReader,
    http_request: Request,
) -> ContentIdeaDiscoveryResponse:
    """Assemble scoped evidence for an agent to turn into reviewed idea prose."""
    await authorize_profile(http_request, principal, request.profile_id)
    await authorize_project(http_request, principal, request.project_id)
    try:
        result = await service.discover_content_ideas(
            profile_id=request.profile_id,
            project_id=request.project_id,
            pillar_id=request.pillar_id,
            time_range=request.time_range,
            platform=request.platform,
            limit=request.limit,
        )
    except CreatorProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail="creator profile was not found") from error
    except ContentPillarNotFoundError as error:
        raise HTTPException(status_code=404, detail="content pillar was not found") from error
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail="project was not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ContentIdeaDiscoveryResponse.from_value(result)


@router.get("/content-ideas", response_model=ContentIdeaListResponse)
async def list_content_ideas(
    profile_id: Identifier,
    service: ContentServiceDependency,
    principal: ContentReader,
    http_request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> ContentIdeaListResponse:
    """Cursor-page ideas owned by one authorized creator profile."""
    await authorize_profile(http_request, principal, profile_id)
    try:
        page = await service.list_ideas(profile_id, limit=limit, cursor=cursor)
    except CreatorProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail="creator profile was not found") from error
    for idea in page.items:
        if idea.origin.project_id is not None:
            await authorize_project(http_request, principal, idea.origin.project_id)
    return ContentIdeaListResponse(
        profile_id=profile_id,
        items=tuple(ContentIdeaResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.post(
    "/content-ideas",
    response_model=ContentIdeaResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_content_idea(
    request: ContentIdeaCreateRequest,
    service: ContentServiceDependency,
    principal: ContentWriter,
    http_request: Request,
) -> ContentIdeaResponse:
    """Store an initial idea without performing idea discovery."""
    await authorize_profile(http_request, principal, request.profile_id)
    if request.origin.project_id is not None:
        await authorize_project(http_request, principal, request.origin.project_id)
    try:
        idea = await service.create_idea(**request.model_dump())
    except CreatorProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail="creator profile was not found") from error
    except ContentIdeaAlreadyExistsError as error:
        raise HTTPException(status_code=409, detail="content idea already exists") from error
    except InvalidContentLifecycleTransitionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await record_content_write(http_request, principal, request.profile_id)
    return ContentIdeaResponse.from_document(idea)


@router.patch("/content-ideas/{idea_id}", response_model=ContentIdeaResponse)
async def update_content_idea(
    idea_id: IdeaId,
    request: ContentIdeaUpdateRequest,
    service: ContentServiceDependency,
    principal: ContentWriter,
    http_request: Request,
) -> ContentIdeaResponse:
    """Revision-guard idea metadata and lifecycle transitions."""
    await authorize_profile(http_request, principal, request.profile_id)
    if request.origin is not None and request.origin.project_id is not None:
        await authorize_project(http_request, principal, request.origin.project_id)
    changes = request.model_dump(exclude={"profile_id", "expected_revision"}, exclude_unset=True)
    try:
        idea = await service.update_idea(
            request.profile_id,
            idea_id,
            expected_revision=request.expected_revision,
            changes=changes,
        )
    except (CreatorProfileNotFoundError, ContentIdeaNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content idea was not found") from error
    except (ConcurrencyConflictError, ContentVersionConflictError) as error:
        raise RevisionConflict() from error
    except InvalidContentLifecycleTransitionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await record_content_write(http_request, principal, request.profile_id)
    return ContentIdeaResponse.from_document(idea)


@router.post(
    "/content-briefs",
    response_model=ContentBriefResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_content_brief(
    request: ContentBriefCreateRequest,
    service: ContentServiceDependency,
    principal: ContentWriter,
    http_request: Request,
) -> ContentBriefResponse:
    """Store a brief with source provenance supplied by the caller."""
    await authorize_profile(http_request, principal, request.profile_id)
    await _authorize_projects(http_request, principal, (), request.source_refs)
    if request.research_project_id is not None:
        await _authorize_research_project(
            request.research_project_id,
            get_research_project_service(http_request),
            principal,
            http_request,
        )
    try:
        brief = await service.create_brief(
            **request.model_dump(), allowed_project_ids=principal.project_selector()
        )
    except CreatorProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail="creator profile was not found") from error
    except ContentIdeaNotFoundError as error:
        raise HTTPException(status_code=404, detail="content idea was not found") from error
    except ContentBriefAlreadyExistsError as error:
        raise HTTPException(status_code=409, detail="content brief already exists") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await record_content_write(http_request, principal, request.profile_id)
    return ContentBriefResponse.from_document(brief)


@router.post("/content-briefs/prepare", response_model=ContentBriefPreparationResponse)
async def prepare_content_brief(
    request: ContentBriefPrepareRequest,
    service: ContentDiscoveryServiceDependency,
    content_service: ContentServiceDependency,
    principal: ContentReader,
    http_request: Request,
) -> ContentBriefPreparationResponse:
    """Return a non-persistent, provenance-preserving brief input package."""
    await authorize_profile(http_request, principal, request.profile_id)
    try:
        idea = await content_service.get_idea(request.profile_id, request.idea_id)
    except (CreatorProfileNotFoundError, ContentIdeaNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content idea was not found") from error
    await authorize_project(http_request, principal, idea.origin.project_id)
    await authorize_project(http_request, principal, request.project_id)
    try:
        result = await service.prepare_content_brief(
            profile_id=request.profile_id,
            idea_id=request.idea_id,
            platform=request.platform,
            project_id=request.project_id,
            pillar_id=request.pillar_id,
        )
    except ContentPillarNotFoundError as error:
        raise HTTPException(status_code=404, detail="content pillar was not found") from error
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail="project was not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ContentBriefPreparationResponse.from_value(result)


@router.get("/content-briefs/{brief_id}", response_model=ContentBriefResponse)
async def get_content_brief(
    brief_id: BriefId,
    profile_id: Identifier,
    service: ContentServiceDependency,
    principal: ContentReader,
    http_request: Request,
) -> ContentBriefResponse:
    """Read a brief only after both profile and source projects are allowed."""
    await authorize_profile(http_request, principal, profile_id)
    try:
        brief = await service.get_brief(profile_id, brief_id)
    except (CreatorProfileNotFoundError, ContentBriefNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content brief was not found") from error
    await _authorize_projects(http_request, principal, (), brief.source_refs)
    if brief.research_project_id is not None:
        await _authorize_research_project(
            brief.research_project_id,
            get_research_project_service(http_request),
            principal,
            http_request,
        )
    return ContentBriefResponse.from_document(brief)


@router.post("/content", response_model=ContentItemResponse, status_code=status.HTTP_201_CREATED)
async def create_content(
    request: ContentItemCreateRequest,
    service: ContentServiceDependency,
    principal: ContentWriter,
    http_request: Request,
) -> ContentItemResponse:
    """Create a draft and version 1; this endpoint never publishes content."""
    await authorize_profile(http_request, principal, request.profile_id)
    await _authorize_projects(http_request, principal, request.project_ids, request.source_refs)
    try:
        item = await service.create_content(**request.model_dump())
    except CreatorProfileNotFoundError as error:
        raise HTTPException(status_code=404, detail="creator profile was not found") from error
    except ContentBriefNotFoundError as error:
        raise HTTPException(status_code=404, detail="content brief was not found") from error
    except ContentItemAlreadyExistsError as error:
        raise HTTPException(status_code=409, detail="content item already exists") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await record_content_write(http_request, principal, request.profile_id)
    return ContentItemResponse.from_document(item)


@router.post(
    "/content/{content_id}/fact-review",
    response_model=ContentFactReviewResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Content or research project was not found"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Fact review request was not valid"},
    },
)
async def review_content_facts(
    content_id: ContentId,
    request: ContentFactReviewRequest,
    service: ContentResearchServiceDependency,
    content_service: ContentServiceDependency,
    research_projects: ResearchProjectServiceDependency,
    principal: ContentReader,
    http_request: Request,
) -> ContentFactReviewResponse:
    """Map a saved draft to authored research evidence without altering its prose.

    This advisory operation never advances content state, rewrites the draft,
    changes any V3 claim, or triggers publication.
    """
    await authorize_profile(http_request, principal, request.profile_id)
    try:
        item = await content_service.get_content(request.profile_id, content_id)
    except (CreatorProfileNotFoundError, ContentItemNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content item was not found") from error
    await _authorize_projects(http_request, principal, item.project_ids, item.source_refs)
    brief = None
    if item.brief_id is not None:
        try:
            brief = await content_service.get_brief(request.profile_id, item.brief_id)
        except ContentBriefNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="content brief was not available for fact review",
            ) from error
        await _authorize_projects(http_request, principal, (), brief.source_refs)
    brief_research_project_id = None if brief is None else brief.research_project_id
    if (
        request.research_project_id is not None
        and brief_research_project_id is not None
        and request.research_project_id != brief_research_project_id
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="research_project_id does not match the content brief",
        )
    research_project_id = request.research_project_id or brief_research_project_id
    if research_project_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="a research_project_id or research-backed content brief is required",
        )
    await _authorize_research_project(
        research_project_id, research_projects, principal, http_request
    )
    try:
        review = await service.review_content_facts(
            content=item,
            research_project_id=research_project_id,
            claim_ids=request.claim_ids,
            claim_statements=request.claim_statements,
            allowed_project_ids=principal.project_selector(),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return ContentFactReviewResponse.from_value(review, profile_id=request.profile_id)


@router.post("/content/{content_id}/export", response_model=ContentExportResponse)
async def export_content(
    content_id: ContentId,
    request: ContentExportRequest,
    service: ContentExportServiceDependency,
    content_service: ContentServiceDependency,
    principal: ContentReader,
    http_request: Request,
) -> ContentExportResponse:
    """Validate and format an authorized draft without changing or publishing it."""
    await authorize_profile(http_request, principal, request.profile_id)
    try:
        item = await content_service.get_content(request.profile_id, content_id)
    except (CreatorProfileNotFoundError, ContentItemNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content item was not found") from error
    await _authorize_projects(http_request, principal, item.project_ids, item.source_refs)
    if item.status not in {ContentStatus.APPROVED, ContentStatus.PUBLISHED}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="content must be approved before it can be exported",
        )
    try:
        exported = service.export_content(item, mode=request.mode)
    except (ContentPlatformValidationError, UnsupportedContentPlatformError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ContentExportResponse.from_value(exported)


@router.post(
    "/content/{content_id}/publication-review",
    response_model=ContentPublicationReviewResponse,
)
async def review_content_for_publication(
    content_id: ContentId,
    request: ContentPublicationReviewRequest,
    service: ContentPublicationServiceDependency,
    content_service: ContentServiceDependency,
    principal: ContentReader,
    http_request: Request,
) -> ContentPublicationReviewResponse:
    """Return redacted, advisory safety findings for one authorized current item."""
    await authorize_profile(http_request, principal, request.profile_id)
    try:
        item = await content_service.get_content(request.profile_id, content_id)
    except (CreatorProfileNotFoundError, ContentItemNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content item was not found") from error
    await _authorize_projects(http_request, principal, item.project_ids, item.source_refs)
    review = service.review_item_for_publication(item)
    return ContentPublicationReviewResponse.from_value(review, current_status=item.status)


@router.post("/content/{content_id}/approve", response_model=ContentApprovalResponse)
async def approve_content(
    content_id: ContentId,
    request: ContentApprovalRequest,
    service: ContentPublicationServiceDependency,
    content_service: ContentServiceDependency,
    principal: ContentWriter,
    http_request: Request,
) -> ContentApprovalResponse:
    """Advance one reviewed revision to approved after a fresh redacted safety check."""
    await authorize_profile(http_request, principal, request.profile_id)
    try:
        current = await content_service.get_content(request.profile_id, content_id)
    except (CreatorProfileNotFoundError, ContentItemNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content item was not found") from error
    await _authorize_projects(http_request, principal, current.project_ids, current.source_refs)
    try:
        approved, review = await service.approve_content(
            request.profile_id,
            content_id,
            expected_revision=request.expected_revision,
        )
    except ContentApprovalBlockedError as error:
        await record_content_approval(
            http_request,
            principal,
            request.profile_id,
            outcome="denied",
            reason="publication safety findings require review",
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except ContentApprovalStateError as error:
        await record_content_approval(
            http_request,
            principal,
            request.profile_id,
            outcome="denied",
            reason="content is not ready for approval",
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ConcurrencyConflictError as error:
        await record_content_approval(
            http_request,
            principal,
            request.profile_id,
            outcome="denied",
            reason="content changed before approval",
        )
        raise RevisionConflict() from error
    await record_content_approval(http_request, principal, request.profile_id)
    return ContentApprovalResponse(
        content=ContentItemResponse.from_document(approved),
        review=ContentPublicationReviewResponse.from_value(review, current_status=approved.status),
    )


@router.post("/content/{content_id}/publish", response_model=ContentPublishResponse)
async def publish_content(
    content_id: ContentId,
    request: ContentPublishRequest,
    service: ContentPublishingServiceDependency,
    content_service: ContentServiceDependency,
    principal: ContentPublisher,
    http_request: Request,
) -> ContentPublishResponse:
    """Explicitly publish one approved stored item; no arbitrary prose is accepted."""
    await authorize_profile(http_request, principal, request.profile_id)
    try:
        current = await content_service.get_content(request.profile_id, content_id)
    except (CreatorProfileNotFoundError, ContentItemNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content item was not found") from error
    await _authorize_projects(http_request, principal, current.project_ids, current.source_refs)
    try:
        outcome = await service.publish_content(
            request.profile_id,
            content_id,
            expected_revision=request.expected_revision,
            provider=request.provider,
            idempotency_key=request.idempotency_key,
        )
    except ContentApprovalBlockedError as error:
        await record_content_publish(
            http_request,
            principal,
            request.profile_id,
            outcome="denied",
            reason="publication safety findings require review",
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except (ContentPublishingStateError, ContentAlreadyPublishedError) as error:
        await record_content_publish(
            http_request,
            principal,
            request.profile_id,
            outcome="denied",
            reason="content is not eligible for publication",
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except PublisherUnavailableError as error:
        await record_content_publish(
            http_request,
            principal,
            request.profile_id,
            outcome="failed",
            reason="publication provider is not configured",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    except PublisherDeliveryError as error:
        await record_content_publish(
            http_request,
            principal,
            request.profile_id,
            outcome="failed",
            reason="publication provider failed",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="the publication provider failed",
        ) from error
    except ConcurrencyConflictError as error:
        await record_content_publish(
            http_request,
            principal,
            request.profile_id,
            outcome="denied",
            reason="content changed before publication",
        )
        raise RevisionConflict() from error
    await record_content_publish(http_request, principal, request.profile_id)
    receipt = outcome.receipt
    return ContentPublishResponse(
        content=ContentItemResponse.from_document(outcome.item),
        provider=receipt.provider,
        external_id=receipt.external_id,
        external_url=receipt.external_url,
        published_at=receipt.published_at,
        idempotency_key=receipt.idempotency_key,
        already_published=outcome.already_published,
    )


@router.post("/content/{content_id}/repurpose", response_model=ContentRepurposeResponse)
async def prepare_repurpose_plan(
    content_id: ContentId,
    request: ContentRepurposeRequest,
    service: ContentRepurposeServiceDependency,
    content_service: ContentServiceDependency,
    principal: ContentReader,
    http_request: Request,
) -> ContentRepurposeResponse:
    """Prepare a read-only, platform-specific plan for an authorized stored source."""
    await authorize_profile(http_request, principal, request.profile_id)
    source: ContentIdeaDocument | ContentBriefDocument | ContentItemDocument
    try:
        if request.source_kind == "idea":
            source = await content_service.get_idea(request.profile_id, content_id)
            await authorize_project(http_request, principal, source.origin.project_id)
        elif request.source_kind == "brief":
            source = await content_service.get_brief(request.profile_id, content_id)
            await _authorize_projects(http_request, principal, (), source.source_refs)
        else:
            source = await content_service.get_content(request.profile_id, content_id)
            await _authorize_projects(
                http_request, principal, source.project_ids, source.source_refs
            )
    except (
        CreatorProfileNotFoundError,
        ContentIdeaNotFoundError,
        ContentBriefNotFoundError,
        ContentItemNotFoundError,
    ) as error:
        raise HTTPException(status_code=404, detail="content source was not found") from error
    try:
        result = await service.prepare_repurpose_plan(
            source=source,
            targets=request.targets,
            method=request.method,
            limit=request.limit,
            token_budget=request.token_budget,
            allowed_project_ids=None
            if principal.project_selector() is None
            else frozenset(principal.project_selector() or ()),
        )
    except ContentRetrievalUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ContentRepurposeResponse.from_value(result)


@router.get("/content/{content_id}/metrics", response_model=ContentMetricListResponse)
async def get_content_metrics(
    content_id: ContentId,
    profile_id: Identifier,
    service: ContentMetricsServiceDependency,
    content_service: ContentServiceDependency,
    principal: ContentReader,
    http_request: Request,
    platform: ContentPlatform | None = None,
    captured_after: AwareDatetime | None = None,
    captured_before: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> ContentMetricListResponse:
    """Read an authorized content item's descriptive metric time series."""
    await authorize_profile(http_request, principal, profile_id)
    try:
        item = await content_service.get_content(profile_id, content_id)
    except (CreatorProfileNotFoundError, ContentItemNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content item was not found") from error
    await _authorize_projects(http_request, principal, item.project_ids, item.source_refs)
    try:
        history = await service.list_metrics(
            profile_id=profile_id,
            content_id=content_id,
            platform=platform,
            captured_after=captured_after,
            captured_before=captured_before,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ContentMetricListResponse(
        profile_id=profile_id,
        content_id=content_id,
        items=tuple(ContentMetricResponse.from_document(metric) for metric in history.page.items),
        next_cursor=history.page.next_cursor,
    )


@router.post(
    "/content/{content_id}/metrics",
    response_model=ContentMetricResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_content_metric(
    content_id: ContentId,
    request: ContentMetricCreateRequest,
    service: ContentMetricsServiceDependency,
    content_service: ContentServiceDependency,
    principal: ContentWriter,
    http_request: Request,
) -> ContentMetricResponse:
    """Store one explicit manual observation without collecting provider analytics."""
    await authorize_profile(http_request, principal, request.profile_id)
    try:
        item = await content_service.get_content(request.profile_id, content_id)
    except (CreatorProfileNotFoundError, ContentItemNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content item was not found") from error
    await _authorize_projects(http_request, principal, item.project_ids, item.source_refs)
    try:
        metric = await service.create_metric(
            content_id=content_id, **request.model_dump(mode="python")
        )
    except ContentMetricAlreadyExistsError as error:
        raise HTTPException(status_code=409, detail="content metric already exists") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await record_content_write(http_request, principal, request.profile_id)
    return ContentMetricResponse.from_document(metric)


@router.get("/content/{content_id}", response_model=ContentItemResponse)
async def get_content(
    content_id: ContentId,
    profile_id: Identifier,
    service: ContentServiceDependency,
    principal: ContentReader,
    http_request: Request,
) -> ContentItemResponse:
    """Read the current item, including archived items, inside its profile."""
    await authorize_profile(http_request, principal, profile_id)
    try:
        item = await service.get_content(profile_id, content_id)
    except (CreatorProfileNotFoundError, ContentItemNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content item was not found") from error
    await _authorize_projects(http_request, principal, item.project_ids, item.source_refs)
    return ContentItemResponse.from_document(item)


@router.get("/content/{content_id}/versions", response_model=ContentVersionListResponse)
async def get_content_history(
    content_id: ContentId,
    profile_id: Identifier,
    service: ContentServiceDependency,
    principal: ContentReader,
    http_request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> ContentVersionListResponse:
    """Read append-only history after enforcing the current item's provenance grants."""
    await authorize_profile(http_request, principal, profile_id)
    try:
        item = await service.get_content(profile_id, content_id)
        page = await service.list_versions(profile_id, content_id, limit=limit, cursor=cursor)
    except (CreatorProfileNotFoundError, ContentItemNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content item was not found") from error
    await _authorize_projects(http_request, principal, item.project_ids, item.source_refs)
    return ContentVersionListResponse(
        profile_id=profile_id,
        content_id=content_id,
        items=tuple(ContentVersionResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.patch("/content/{content_id}", response_model=ContentItemResponse)
async def update_content(
    content_id: ContentId,
    request: ContentItemUpdateRequest,
    service: ContentServiceDependency,
    principal: ContentWriter,
    http_request: Request,
) -> ContentItemResponse:
    """Update current metadata or lifecycle state, never the immutable body history."""
    await authorize_profile(http_request, principal, request.profile_id)
    try:
        current = await service.get_content(request.profile_id, content_id)
    except (CreatorProfileNotFoundError, ContentItemNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content item was not found") from error
    await _authorize_projects(http_request, principal, current.project_ids, current.source_refs)
    project_ids = request.project_ids if request.project_ids is not None else current.project_ids
    source_refs = request.source_refs if request.source_refs is not None else current.source_refs
    await _authorize_projects(http_request, principal, project_ids, source_refs)
    changes = request.model_dump(exclude={"profile_id", "expected_revision"}, exclude_unset=True)
    try:
        item = await service.update_content(
            request.profile_id,
            content_id,
            expected_revision=request.expected_revision,
            changes=changes,
        )
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    except InvalidContentLifecycleTransitionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await record_content_write(http_request, principal, request.profile_id)
    return ContentItemResponse.from_document(item)


@router.post("/content/{content_id}/versions", response_model=ContentItemResponse)
async def create_content_version(
    content_id: ContentId,
    request: ContentVersionCreateRequest,
    service: ContentServiceDependency,
    principal: ContentWriter,
    http_request: Request,
) -> ContentItemResponse:
    """Append a body or rollback snapshot as a new immutable draft version."""
    await authorize_profile(http_request, principal, request.profile_id)
    try:
        current = await service.get_content(request.profile_id, content_id)
    except (CreatorProfileNotFoundError, ContentItemNotFoundError) as error:
        raise HTTPException(status_code=404, detail="content item was not found") from error
    await _authorize_projects(http_request, principal, current.project_ids, current.source_refs)
    try:
        if request.rollback_to_version is not None:
            item = await service.rollback_content(
                request.profile_id,
                content_id,
                expected_revision=request.expected_revision,
                rollback_to_version=request.rollback_to_version,
                change_reason=request.change_reason,
                created_by=request.created_by,
            )
        else:
            if request.body is None:
                raise HTTPException(status_code=422, detail="a content body is required")
            item = await service.create_version(
                request.profile_id,
                content_id,
                expected_revision=request.expected_revision,
                body=request.body,
                change_reason=request.change_reason,
                created_by=request.created_by,
            )
    except ContentVersionNotFoundError as error:
        raise HTTPException(status_code=404, detail="content version was not found") from error
    except (ContentVersionConflictError, ConcurrencyConflictError) as error:
        raise RevisionConflict() from error
    except InvalidContentLifecycleTransitionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await record_content_write(http_request, principal, request.profile_id)
    return ContentItemResponse.from_document(item)
