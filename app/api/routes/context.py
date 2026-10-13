"""REST endpoints for durable, project-scoped context.

Every endpoint names its project explicitly. The documented paths address a
context item by its own identifier, so the project arrives as a required
parameter and the query is scoped by it rather than looked up unscoped and
checked afterwards.
"""

from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from app.api.concurrency import RevisionConflict
from app.api.dependencies import get_context_service, get_retrieval_service
from app.api.security import authorize_project, record_write, requires
from app.db.errors import SearchUnavailableError
from app.embeddings.errors import EmbeddingError
from app.models.common import Identifier
from app.models.project_context import ContextType
from app.models.skill import LifecycleStatus
from app.projects.errors import (
    ContextNotFoundError,
    ContextRetrievalUnavailableError,
    ProjectNotFoundError,
)
from app.repositories.errors import ConcurrencyConflictError
from app.schemas.context import (
    ContextCreateRequest,
    ContextListResponse,
    ContextResponse,
    ContextSearchRequest,
    ContextSearchResponse,
    ContextSearchResultResponse,
    ContextUpdateRequest,
)
from app.security.principal import Principal
from app.services.context_service import ContextService
from app.services.retrieval_service import RetrievalService

router = APIRouter(prefix="/api/v1/context", tags=["context"])

ContextId = Annotated[str, Path(pattern=r"^[0-9a-f]{24}$")]
_SEARCH_UNAVAILABLE = "context search is unavailable"


ContextServiceDependency = Annotated[ContextService, Depends(get_context_service)]
RetrievalServiceDependency = Annotated[RetrievalService, Depends(get_retrieval_service)]
# Context belongs to exactly one project, so every route here checks both the
# scope and the project the caller named.
ContextReader = Annotated[Principal, Depends(requires("context:read"))]
ContextWriter = Annotated[Principal, Depends(requires("context:write"))]


@router.post(
    "",
    response_model=ContextResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Project not found"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "The embedding model is unusable"},
    },
)
async def create_context(
    request: ContextCreateRequest,
    service: ContextServiceDependency,
    principal: ContextWriter,
    http_request: Request,
) -> ContextResponse:
    """Store one context item in an existing project."""
    await authorize_project(http_request, principal, request.project_id)
    try:
        context = await service.create_context(
            request.project_id,
            context_type=request.type,
            title=request.title,
            content=request.content,
            importance=request.importance,
            source=request.source,
            tags=request.tags,
            status=request.status,
            external_id=request.external_id,
        )
    except ProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project was not found",
        ) from error
    except EmbeddingError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="context cannot be stored while the embedding model is unavailable",
        ) from error
    await record_write(http_request, principal, "context.write", request.project_id)
    return ContextResponse.from_document(context)


@router.get(
    "",
    response_model=ContextListResponse,
)
async def list_context(
    service: ContextServiceDependency,
    principal: ContextReader,
    http_request: Request,
    project_id: Identifier,
    context_type: Annotated[ContextType | None, Query(alias="type")] = None,
    status_filter: Annotated[LifecycleStatus | None, Query(alias="status")] = "active",
    minimum_importance: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> ContextListResponse:
    """List one project's context using bounded cursor pagination."""
    await authorize_project(http_request, principal, project_id)
    page = await service.list_context(
        project_id,
        context_type=context_type,
        status=status_filter,
        minimum_importance=minimum_importance,
        limit=limit,
        cursor=cursor,
    )
    return ContextListResponse(
        project_id=project_id,
        items=tuple(ContextResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.post(
    "/search",
    response_model=ContextSearchResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Context search is unavailable"}
    },
)
async def search_context(
    request: ContextSearchRequest,
    service: RetrievalServiceDependency,
    principal: ContextReader,
    http_request: Request,
) -> ContextSearchResponse:
    """Rank one project's context by combined vector and lexical relevance."""
    await authorize_project(http_request, principal, request.project_id)
    try:
        results = await service.context_search(
            request.query,
            project_id=request.project_id,
            limit=request.limit,
            phrase=request.phrase,
            context_types=request.types,
            tags=request.tags,
            status=request.status,
            minimum_importance=request.minimum_importance,
        )
    except (SearchUnavailableError, EmbeddingError, ContextRetrievalUnavailableError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_SEARCH_UNAVAILABLE,
        ) from error
    return ContextSearchResponse(
        project_id=request.project_id,
        query=request.query,
        results=tuple(ContextSearchResultResponse.from_result(item) for item in results),
    )


@router.get(
    "/{context_id}",
    response_model=ContextResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Context not found in this project"}},
)
async def get_context(
    context_id: ContextId,
    service: ContextServiceDependency,
    principal: ContextReader,
    http_request: Request,
    project_id: Identifier,
) -> ContextResponse:
    """Load one context item from inside its own project."""
    await authorize_project(http_request, principal, project_id)
    try:
        context = await service.get_context(project_id, ObjectId(context_id))
    except ContextNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="context was not found",
        ) from error
    return ContextResponse.from_document(context)


@router.patch(
    "/{context_id}",
    response_model=ContextResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Context not found in this project"},
        status.HTTP_409_CONFLICT: {"description": "The context changed after it was read"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "The embedding model is unusable"},
    },
)
async def update_context(
    context_id: ContextId,
    request: ContextUpdateRequest,
    service: ContextServiceDependency,
    principal: ContextWriter,
    http_request: Request,
    project_id: Identifier,
) -> ContextResponse:
    """Apply a partial update, re-embedding whenever the text changes."""
    await authorize_project(http_request, principal, project_id)
    try:
        context = await service.update_context(
            project_id,
            ObjectId(context_id),
            expected_revision=request.expected_revision,
            context_type=request.type,
            title=request.title,
            content=request.content,
            importance=request.importance,
            tags=request.tags,
            status=request.status,
        )
    except ContextNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="context was not found",
        ) from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    except EmbeddingError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="context cannot be updated while the embedding model is unavailable",
        ) from error
    await record_write(http_request, principal, "context.write", project_id)
    return ContextResponse.from_document(context)
