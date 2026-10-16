"""V3 Phase 8 authorized structured research brief endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_research_brief_service, get_research_project_service
from app.api.security import authorize_research_project_links, record_research_write, requires
from app.models.common import Identifier
from app.models.research import ResearchProjectDocument
from app.research.errors import (
    ResearchBriefAlreadyExistsError,
    ResearchBriefNotFoundError,
    ResearchProjectNotFoundError,
)
from app.schemas.research import ResearchBriefBuildRequest, ResearchBriefResponse
from app.security.principal import Principal
from app.services.research_brief_service import ResearchBriefService
from app.services.research_project_service import ResearchProjectService

router = APIRouter(prefix="/api/v3/research", tags=["research"])

ResearchProjectServiceDependency = Annotated[
    ResearchProjectService, Depends(get_research_project_service)
]
ResearchBriefServiceDependency = Annotated[
    ResearchBriefService, Depends(get_research_brief_service)
]
ResearchReader = Annotated[Principal, Depends(requires("research:read"))]
ResearchWriter = Annotated[Principal, Depends(requires("research:write"))]


async def _authorized_project(
    research_project_id: str,
    project_service: ResearchProjectService,
    principal: Principal,
    http_request: Request,
) -> ResearchProjectDocument:
    """Require every linked V1 grant before building or revealing a brief."""
    try:
        project = await project_service.get_project(research_project_id)
    except ResearchProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research project was not found"
        ) from error
    await authorize_research_project_links(http_request, principal, project.related_project_ids)
    return project


@router.post(
    "/briefs",
    response_model=ResearchBriefResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Research project was not found"},
        status.HTTP_409_CONFLICT: {"description": "Research brief identifier is taken"},
    },
)
async def build_research_brief(
    request: ResearchBriefBuildRequest,
    service: ResearchBriefServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    http_request: Request,
) -> ResearchBriefResponse:
    """Build structured research material without prose synthesis or promotion."""
    await _authorized_project(request.research_project_id, project_service, principal, http_request)
    try:
        brief = await service.build_brief(
            research_project_id=request.research_project_id,
            brief_id=request.brief_id,
            scope=request.scope,
            additional_unknowns=request.additional_unknowns,
            next_questions=request.next_questions,
            status=request.status,
            created_by=principal.key_id if principal.authenticated else None,
            allowed_project_ids=principal.project_selector(),
        )
    except ResearchBriefAlreadyExistsError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    await record_research_write(http_request, principal, brief.research_project_id)
    return ResearchBriefResponse.from_document(brief)


@router.get(
    "/briefs/{brief_id}",
    response_model=ResearchBriefResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Research brief was not found"}},
)
async def get_research_brief(
    brief_id: Identifier,
    research_project_id: Identifier,
    service: ResearchBriefServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    http_request: Request,
) -> ResearchBriefResponse:
    """Read one retained structured brief after current and historical scope checks."""
    await _authorized_project(research_project_id, project_service, principal, http_request)
    try:
        brief = await service.get_brief(
            research_project_id=research_project_id,
            brief_id=brief_id,
            allowed_project_ids=principal.project_selector(),
        )
    except ResearchBriefNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research brief was not found"
        ) from error
    return ResearchBriefResponse.from_document(brief)
