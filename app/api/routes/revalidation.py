"""V3 Phase 14 advisory revalidation endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import get_research_project_service, get_revalidation_service
from app.api.security import authorize_research_project_links, record_research_write, requires
from app.models.common import Identifier
from app.research.errors import (
    KnowledgeCandidateNotFoundError,
    ResearchProjectNotFoundError,
    RevalidationNotNeededError,
)
from app.schemas.research import (
    RevalidationProposalRequest,
    RevalidationProposalResponse,
    StaleKnowledgeListResponse,
    StaleKnowledgeResponse,
)
from app.security.principal import Principal
from app.services.research_project_service import ResearchProjectService
from app.services.revalidation_service import RevalidationService

router = APIRouter(prefix="/api/v3/research/revalidation", tags=["research revalidation"])

ResearchProjectServiceDependency = Annotated[
    ResearchProjectService, Depends(get_research_project_service)
]
RevalidationServiceDependency = Annotated[RevalidationService, Depends(get_revalidation_service)]
ResearchReader = Annotated[Principal, Depends(requires("research:read"))]
ResearchWriter = Annotated[Principal, Depends(requires("research:write"))]


async def _authorize_research_project(
    research_project_id: str,
    project_service: ResearchProjectService,
    principal: Principal,
    request: Request,
) -> None:
    try:
        project = await project_service.get_project(research_project_id)
    except ResearchProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research project was not found"
        ) from error
    await authorize_research_project_links(request, principal, project.related_project_ids)


@router.get("/stale", response_model=StaleKnowledgeListResponse)
async def list_stale_knowledge(
    research_project_id: Identifier,
    service: RevalidationServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    request: Request,
    as_of: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> StaleKnowledgeListResponse:
    """List potential review work without changing V1 knowledge or research records."""
    await _authorize_research_project(research_project_id, project_service, principal, request)
    result = await service.list_stale_knowledge(
        research_project_id=research_project_id,
        allowed_project_ids=principal.projects,
        as_of=as_of,
        limit=limit,
        cursor=cursor,
    )
    return StaleKnowledgeListResponse(
        items=tuple(StaleKnowledgeResponse.from_value(item) for item in result.items),
        next_cursor=result.next_cursor,
    )


@router.post(
    "/candidates/{candidate_id}",
    response_model=RevalidationProposalResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Research project or promoted candidate was not found"
        },
        status.HTTP_409_CONFLICT: {"description": "No observable revalidation trigger exists"},
    },
)
async def propose_revalidation(
    candidate_id: Identifier,
    payload: RevalidationProposalRequest,
    service: RevalidationServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    request: Request,
) -> RevalidationProposalResponse:
    """Create a pending candidate only; approval and promotion remain separate actions."""
    await _authorize_research_project(
        payload.research_project_id, project_service, principal, request
    )
    try:
        proposal = await service.propose_revalidation(
            research_project_id=payload.research_project_id,
            candidate_id=candidate_id,
            created_by=principal.key_id,
            allowed_project_ids=principal.projects,
            as_of=payload.as_of,
        )
    except KnowledgeCandidateNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except RevalidationNotNeededError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    await record_research_write(request, principal, payload.research_project_id)
    return RevalidationProposalResponse.from_value(proposal)
