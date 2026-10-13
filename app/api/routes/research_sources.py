"""V3 source registry plus constrained manual/file and registered-URL ingestion endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import (
    get_research_ingestion_service,
    get_research_project_service,
    get_research_source_service,
)
from app.api.security import authorize_research_project_links, record_research_write, requires
from app.models.common import Identifier
from app.models.research import ResearchRecordStatus, ResearchSourceDocument, SourceClass
from app.research.errors import (
    ResearchProjectNotFoundError,
    ResearchSourceAlreadyExistsError,
    ResearchSourceNotFoundError,
    SourceSnapshotAlreadyExistsError,
)
from app.research.providers import IngestionPayload
from app.schemas.research import (
    IngestionJobResponse,
    ResearchIngestionRequest,
    ResearchIngestionResponse,
    ResearchSourceCreateRequest,
    ResearchSourceListResponse,
    ResearchSourceResponse,
    SourceSnapshotResponse,
)
from app.security.principal import Principal
from app.services.research_ingestion_service import (
    ResearchIngestionCapacityError,
    ResearchIngestionError,
    ResearchIngestionInputError,
    ResearchIngestionJobNotFoundError,
    ResearchIngestionService,
    UnsupportedResearchSourceError,
)
from app.services.research_project_service import ResearchProjectService
from app.services.research_source_service import (
    ResearchSourceLimitError,
    ResearchSourceService,
    SourceSnapshotRegistration,
)

router = APIRouter(prefix="/api/v3/research/sources", tags=["research"])

ResearchProjectServiceDependency = Annotated[
    ResearchProjectService, Depends(get_research_project_service)
]
ResearchSourceServiceDependency = Annotated[
    ResearchSourceService, Depends(get_research_source_service)
]
ResearchIngestionServiceDependency = Annotated[
    ResearchIngestionService, Depends(get_research_ingestion_service)
]
ResearchReader = Annotated[Principal, Depends(requires("research:read"))]
ResearchWriter = Annotated[Principal, Depends(requires("research:write"))]


def _snapshot_registration(
    request: ResearchSourceCreateRequest,
) -> SourceSnapshotRegistration | None:
    """Translate optional submitted snapshot metadata without acquiring its content."""
    if request.snapshot is None:
        return None
    return SourceSnapshotRegistration(
        snapshot_id=request.snapshot.snapshot_id,
        retrieved_at=request.snapshot.retrieved_at,
        content_hash=request.snapshot.content_hash,
        version_identifier=request.snapshot.version_identifier,
        metadata=request.snapshot.metadata,
    )


@router.post(
    "",
    response_model=ResearchSourceResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Research project was not found"},
        status.HTTP_409_CONFLICT: {
            "description": "A duplicate source or immutable snapshot exists"
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Research source limit reached"},
    },
)
async def create_research_source(
    request: ResearchSourceCreateRequest,
    source_service: ResearchSourceServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    http_request: Request,
) -> ResearchSourceResponse:
    """Register untrusted source metadata after every linked V1 project is authorized."""
    try:
        project = await project_service.get_project(request.research_project_id)
    except ResearchProjectNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await authorize_research_project_links(http_request, principal, project.related_project_ids)
    try:
        registered = await source_service.create_source(
            research_project_id=request.research_project_id,
            source_id=request.source_id,
            source_type=request.source_type,
            uri=request.uri,
            title=request.title,
            publisher=request.publisher,
            author=request.author,
            published_at=request.published_at,
            retrieved_at=request.retrieved_at,
            source_class=request.source_class,
            trust_metadata=request.trust_metadata,
            status=ResearchRecordStatus(request.status),
            owner_key_id=principal.key_id if principal.authenticated else None,
            snapshot=_snapshot_registration(request),
        )
    except ResearchProjectNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (ResearchSourceAlreadyExistsError, SourceSnapshotAlreadyExistsError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ResearchSourceLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)
        ) from error
    await record_research_write(http_request, principal, registered.source.research_project_id)
    return ResearchSourceResponse.from_document(
        registered.source, registered_snapshot=registered.snapshot
    )


async def _authorized_source(
    source_id: str,
    source_service: ResearchSourceService,
    project_service: ResearchProjectService,
    principal: Principal,
    http_request: Request,
) -> ResearchSourceDocument:
    """Load a source only after preserving both present and stored V1 project scopes."""
    try:
        source = await source_service.get_source(source_id)
        project = await project_service.get_project(source.research_project_id)
    except (ResearchSourceNotFoundError, ResearchProjectNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research source was not found"
        ) from error
    await authorize_research_project_links(http_request, principal, project.related_project_ids)
    await authorize_research_project_links(
        http_request, principal, source.authorization.project_ids
    )
    return source


@router.post(
    "/{source_id}/ingest",
    response_model=ResearchIngestionResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Research source was not found"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "description": "Submitted content was unsafe or malformed"
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Ingestion capacity is exhausted"},
    },
)
async def ingest_research_source(
    source_id: Identifier,
    request: ResearchIngestionRequest,
    ingestion_service: ResearchIngestionServiceDependency,
    source_service: ResearchSourceServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    http_request: Request,
) -> ResearchIngestionResponse:
    """Ingest supplied bytes or one already-authorized registered remote source."""
    source = await _authorized_source(
        source_id, source_service, project_service, principal, http_request
    )
    try:
        result = await ingestion_service.ingest(
            source=source,
            payload=IngestionPayload(
                data=request.submitted_bytes(),
                content_type=request.content_type or "application/octet-stream",
                title=request.title or source.title,
                language=request.language,
                filename=request.filename,
                acquire_url=request.fetch_url,
                acquire_repository=request.fetch_repository,
            ),
            manually_provided=request.manually_provided,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except (ResearchIngestionInputError, UnsupportedResearchSourceError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except ResearchIngestionCapacityError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)
        ) from error
    except ResearchIngestionError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    await record_research_write(http_request, principal, source.research_project_id)
    return ResearchIngestionResponse(
        source_id=source.source_id,
        snapshot=SourceSnapshotResponse.from_document(result.snapshot),
        job=IngestionJobResponse.from_document(result.job),
        idempotent=result.idempotent,
    )


@router.get(
    "/{source_id}/ingestion-jobs/{job_id}",
    response_model=IngestionJobResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Research ingestion job was not found"}},
)
async def get_research_ingestion_status(
    source_id: Identifier,
    job_id: Identifier,
    ingestion_service: ResearchIngestionServiceDependency,
    source_service: ResearchSourceServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    http_request: Request,
) -> IngestionJobResponse:
    """Read one source-bound job through the same V1 project authorization boundary."""
    source = await _authorized_source(
        source_id, source_service, project_service, principal, http_request
    )
    try:
        job = await ingestion_service.get_job(
            research_project_id=source.research_project_id, job_id=job_id
        )
    except ResearchIngestionJobNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    if job.source_id != source.source_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research ingestion job was not found"
        )
    return IngestionJobResponse.from_document(job)


@router.get(
    "/{source_id}",
    response_model=ResearchSourceResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Research source was not found"}},
)
async def get_research_source(
    source_id: Identifier,
    source_service: ResearchSourceServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    http_request: Request,
) -> ResearchSourceResponse:
    """Read one source only when both current and preserved V1 scopes are allowed."""
    try:
        source = await source_service.get_source(source_id)
        project = await project_service.get_project(source.research_project_id)
    except (ResearchSourceNotFoundError, ResearchProjectNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research source was not found"
        ) from error
    await authorize_research_project_links(http_request, principal, project.related_project_ids)
    await authorize_research_project_links(
        http_request, principal, source.authorization.project_ids
    )
    return ResearchSourceResponse.from_document(source)


@router.get("", response_model=ResearchSourceListResponse)
async def list_research_sources(
    research_project_id: Identifier,
    source_service: ResearchSourceServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    http_request: Request,
    status_filter: Annotated[
        str | None, Query(alias="status", pattern="^(active|archived)$")
    ] = None,
    source_class: SourceClass | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> ResearchSourceListResponse:
    """Page source metadata within one authorized research project boundary."""
    try:
        project = await project_service.get_project(research_project_id)
    except ResearchProjectNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await authorize_research_project_links(http_request, principal, project.related_project_ids)
    selected_status = ResearchRecordStatus(status_filter) if status_filter is not None else None
    page = await source_service.list_sources(
        research_project_id=research_project_id,
        allowed_project_ids=principal.projects,
        status=selected_status,
        source_class=source_class,
        limit=limit,
        cursor=cursor,
    )
    return ResearchSourceListResponse(
        items=tuple(ResearchSourceResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )
