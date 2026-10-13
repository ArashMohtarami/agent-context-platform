"""REST endpoints for intentional memory.

Memory is written only when a caller asks for it. Nothing here observes a
conversation, and nothing promotes one to memory.

A memory is addressed by its own identifier, so the scope arrives as a
parameter: omitting `project_id` addresses global memory, and naming one
addresses that project's. The query is scoped by it rather than looked up
unscoped and checked afterwards.
"""

from typing import Annotated, TypedDict

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from app.api.concurrency import RevisionConflict
from app.api.dependencies import get_memory_service, get_retrieval_service
from app.api.security import authorize_project, record_write, requires
from app.db.errors import SearchUnavailableError
from app.embeddings.errors import EmbeddingError
from app.memory.errors import (
    MemoryNotFoundError,
    MemoryRetrievalUnavailableError,
    MemorySupersessionError,
)
from app.models.common import Identifier
from app.models.memory import MemoryScope
from app.models.skill import LifecycleStatus
from app.projects.errors import ProjectNotFoundError
from app.repositories.errors import ConcurrencyConflictError
from app.schemas.memory import (
    MemoryCreateRequest,
    MemoryListResponse,
    MemoryResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    MemorySearchResultResponse,
    MemoryUpdateRequest,
)
from app.security.principal import Principal
from app.services.memory_service import MemoryService, memory_scope
from app.services.retrieval_service import RetrievalService

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])

MemoryId = Annotated[str, Path(pattern=r"^[0-9a-f]{24}$")]
_SEARCH_UNAVAILABLE = "memory search is unavailable"

MemoryServiceDependency = Annotated[MemoryService, Depends(get_memory_service)]
RetrievalServiceDependency = Annotated[RetrievalService, Depends(get_retrieval_service)]
# Memory is global or bound to one project, so naming none is a narrower
# request rather than a wider one and is always allowed.
MemoryReader = Annotated[Principal, Depends(requires("memory:read"))]
MemoryWriter = Annotated[Principal, Depends(requires("memory:write"))]


class ClientArguments(TypedDict, total=False):
    """An authenticated client identity, when the key is client-linked."""

    client_id: str


class MemoryOwnershipArguments(ClientArguments, total=False):
    """Optional ownership fields accepted by the memory service."""

    scope: MemoryScope


@router.post(
    "",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Project or superseded memory not found"},
        status.HTTP_409_CONFLICT: {"description": "That memory was already superseded"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "The embedding model is unusable"},
    },
)
async def create_memory(
    request: MemoryCreateRequest,
    service: MemoryServiceDependency,
    principal: MemoryWriter,
    http_request: Request,
) -> MemoryResponse:
    """Store one memory, optionally replacing the one it corrects."""
    await authorize_project(http_request, principal, request.project_id)
    ownership = _ownership_arguments(principal, request.scope)
    try:
        memory = await service.create_memory(
            memory_type=request.type,
            content=request.content,
            importance=request.importance,
            confidence=request.confidence,
            source=request.source,
            project_id=request.project_id,
            summary=request.summary,
            tags=request.tags,
            status=request.status,
            external_id=request.external_id,
            supersedes=None if request.supersedes is None else ObjectId(request.supersedes),
            **ownership,
        )
    except ProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project was not found",
        ) from error
    except MemoryNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="the memory to supersede was not found",
        ) from error
    except MemorySupersessionError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except EmbeddingError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="memory cannot be stored while the embedding model is unavailable",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    await record_write(http_request, principal, "memory.write", request.project_id)
    return MemoryResponse.from_document(memory)


@router.get("", response_model=MemoryListResponse)
async def list_memory(
    service: MemoryServiceDependency,
    principal: MemoryReader,
    http_request: Request,
    project_id: Identifier | None = None,
    scope: MemoryScope | None = None,
    memory_type: Annotated[str | None, Query(alias="type")] = None,
    status_filter: Annotated[LifecycleStatus | None, Query(alias="status")] = "active",
    minimum_importance: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> MemoryListResponse:
    """List one scope's memories using bounded cursor pagination."""
    await authorize_project(http_request, principal, project_id)
    ownership = _ownership_arguments(principal, scope)
    try:
        page = await service.list_memories(
            project_id=project_id,
            memory_type=memory_type,
            status=status_filter,
            minimum_importance=minimum_importance,
            limit=limit,
            cursor=cursor,
            **ownership,
        )
        resolved_scope = memory_scope(
            project_id,
            client_id=principal.client_id,
            requested_scope=scope,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return MemoryListResponse(
        scope=resolved_scope,
        project_id=project_id,
        client_id=principal.client_id if resolved_scope in {"agent", "project_agent"} else None,
        items=tuple(MemoryResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.post(
    "/search",
    response_model=MemorySearchResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Memory search is unavailable"}
    },
)
async def search_memory(
    request: MemorySearchRequest,
    service: RetrievalServiceDependency,
    principal: MemoryReader,
    http_request: Request,
) -> MemorySearchResponse:
    """Rank the memory this request may see by combined relevance."""
    await authorize_project(http_request, principal, request.project_id)
    ownership = _client_argument(principal)
    try:
        results = await service.memory_search(
            request.query,
            project_id=request.project_id,
            scopes=request.scopes,
            limit=request.limit,
            phrase=request.phrase,
            memory_types=request.types,
            tags=request.tags,
            status=request.status,
            minimum_importance=request.minimum_importance,
            **ownership,
        )
    except (SearchUnavailableError, EmbeddingError, MemoryRetrievalUnavailableError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_SEARCH_UNAVAILABLE,
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return MemorySearchResponse(
        query=request.query,
        project_id=request.project_id,
        results=tuple(MemorySearchResultResponse.from_result(item) for item in results),
    )


@router.get(
    "/{memory_id}",
    response_model=MemoryResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Memory not found in this scope"}},
)
async def get_memory(
    memory_id: MemoryId,
    service: MemoryServiceDependency,
    principal: MemoryReader,
    http_request: Request,
    project_id: Identifier | None = None,
    scope: MemoryScope | None = None,
) -> MemoryResponse:
    """Load one memory from inside its own scope."""
    await authorize_project(http_request, principal, project_id)
    ownership = _ownership_arguments(principal, scope)
    try:
        memory = await service.get_memory(
            ObjectId(memory_id),
            project_id=project_id,
            **ownership,
        )
    except MemoryNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="memory was not found",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return MemoryResponse.from_document(memory)


@router.patch(
    "/{memory_id}",
    response_model=MemoryResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Memory not found in this scope"},
        status.HTTP_409_CONFLICT: {"description": "The memory changed after it was read"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "The embedding model is unusable"},
    },
)
async def update_memory(
    memory_id: MemoryId,
    request: MemoryUpdateRequest,
    service: MemoryServiceDependency,
    principal: MemoryWriter,
    http_request: Request,
    project_id: Identifier | None = None,
    scope: MemoryScope | None = None,
) -> MemoryResponse:
    """Apply a partial update, re-embedding whenever the text changes."""
    await authorize_project(http_request, principal, project_id)
    ownership = _ownership_arguments(principal, scope)
    try:
        memory = await service.update_memory(
            ObjectId(memory_id),
            project_id=project_id,
            expected_revision=request.expected_revision,
            memory_type=request.type,
            content=request.content,
            summary=request.summary,
            importance=request.importance,
            confidence=request.confidence,
            tags=request.tags,
            status=request.status,
            **ownership,
        )
    except MemoryNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="memory was not found",
        ) from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    except EmbeddingError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="memory cannot be updated while the embedding model is unavailable",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    await record_write(http_request, principal, "memory.write", project_id)
    return MemoryResponse.from_document(memory)


def _client_argument(principal: Principal) -> ClientArguments:
    """Pass a client identity only when authentication derived one.

    Existing V1 keys have no client identity and retain their original service
    call shape. Agent credentials receive this value from the authenticated
    principal; it never comes from a request body or MCP tool argument.
    """
    if principal.client_id is None:
        return {}
    return {"client_id": principal.client_id}


def _ownership_arguments(
    principal: Principal, scope: MemoryScope | None
) -> MemoryOwnershipArguments:
    """Pass optional scope and server-derived client ownership to a service."""
    arguments: MemoryOwnershipArguments = {}
    if scope is not None:
        arguments["scope"] = scope
    if principal.client_id is not None:
        arguments["client_id"] = principal.client_id
    return arguments
