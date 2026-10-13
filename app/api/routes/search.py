"""The unified knowledge search endpoint.

One question, every source that can answer it, ranked together and cut to a
token budget. The per-source search endpoints remain for the cases that want
exactly one kind of knowledge; this is the interface for the cases that want an
answer.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_search_service
from app.api.security import authorize_project, requires
from app.db.errors import SearchUnavailableError
from app.embeddings.errors import EmbeddingError
from app.schemas.search import KnowledgeSearchRequest, KnowledgeSearchResponse
from app.security.principal import Principal
from app.services.search_service import SearchService

router = APIRouter(prefix="/api/v1/search", tags=["search"])

_SEARCH_UNAVAILABLE = "knowledge search is unavailable"

SearchServiceDependency = Annotated[SearchService, Depends(get_search_service)]
Searcher = Annotated[Principal, Depends(requires("search:read"))]


@router.post(
    "",
    response_model=KnowledgeSearchResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Knowledge search is unavailable"}
    },
)
async def search_knowledge(
    request: KnowledgeSearchRequest,
    service: SearchServiceDependency,
    principal: Searcher,
    http_request: Request,
) -> KnowledgeSearchResponse:
    """Search every requested source and return one budgeted context."""
    # One query reaches every source, so the project it names is checked before
    # any of them is searched.
    await authorize_project(http_request, principal, request.project_id)
    client = {} if principal.client_id is None else {"memory_client_id": principal.client_id}
    try:
        result = await service.search_knowledge(
            request.query,
            project_id=request.project_id,
            sources=request.sources,
            limit=request.limit,
            token_budget=request.token_budget,
            skill_id=request.skill_id,
            skill_version=request.skill_version,
            category=request.category,
            context_types=request.context_types,
            memory_types=request.memory_types,
            memory_scopes=request.memory_scopes,
            tags=request.tags,
            trust_level=request.trust_level,
            minimum_importance=request.minimum_importance,
            active_only=request.active_only,
            phrase=request.phrase,
            **client,
        )
    except (SearchUnavailableError, EmbeddingError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_SEARCH_UNAVAILABLE,
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return KnowledgeSearchResponse.from_result(result, explain=request.explain)
