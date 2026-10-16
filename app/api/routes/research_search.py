"""V3 Phase 5 authorized research search endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_research_project_service, get_research_search_service
from app.api.security import authorize_research_project_links, requires
from app.research.errors import ResearchProjectNotFoundError
from app.schemas.research import ResearchSearchRequest, ResearchSearchResponse
from app.security.principal import Principal
from app.services.research_project_service import ResearchProjectService
from app.services.research_search_service import (
    ResearchSearchError,
    ResearchSearchFilters,
    ResearchSearchService,
    ResearchSearchUnavailableError,
)

router = APIRouter(prefix="/api/v3/research", tags=["research"])

ResearchProjectServiceDependency = Annotated[
    ResearchProjectService, Depends(get_research_project_service)
]
ResearchSearchServiceDependency = Annotated[
    ResearchSearchService, Depends(get_research_search_service)
]
ResearchReader = Annotated[Principal, Depends(requires("research:read"))]


@router.post(
    "/search",
    response_model=ResearchSearchResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Research project was not found"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Search input was invalid"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Research search is unavailable"},
    },
)
async def search_research(
    request: ResearchSearchRequest,
    service: ResearchSearchServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    http_request: Request,
) -> ResearchSearchResponse:
    """Search evidence only after checking every linked V1 project grant."""
    try:
        project = await project_service.get_project(request.research_project_id)
    except ResearchProjectNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    # This check runs before any search, so a private linked V1 project is not
    # revealed through hit counts, source metadata, or ranking differences.
    await authorize_research_project_links(http_request, principal, project.related_project_ids)
    if (
        request.related_project_id is not None
        and request.related_project_id not in project.related_project_ids
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="related_project_id is not linked to this research project",
        )
    try:
        result = await service.search(
            project=project,
            query=request.query,
            filters=ResearchSearchFilters(
                source_classes=tuple(request.source_classes),
                source_types=tuple(request.source_types),
                publisher=request.publisher,
                domain=request.domain,
                published_after=request.published_after,
                published_before=request.published_before,
                retrieved_after=request.retrieved_after,
                as_of=request.as_of,
                related_project_id=request.related_project_id,
            ),
            retrieval_profile=request.retrieval_profile,
            method=request.method,
            phrase=request.phrase,
            limit=request.limit,
            token_budget=request.token_budget,
            cursor=request.cursor,
            allowed_project_ids=principal.project_selector(),
        )
    except ResearchSearchUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    except ResearchSearchError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return ResearchSearchResponse.from_result(result)
