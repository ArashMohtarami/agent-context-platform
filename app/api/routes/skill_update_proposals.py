"""V3 Phase 11 review-only generated skill update proposal endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import (
    get_research_project_service,
    get_skill_update_proposal_service,
)
from app.api.security import (
    authorize_research_project_links,
    authorize_scope,
    record_research_write,
    requires,
)
from app.models.common import Identifier
from app.models.research import ResearchProjectDocument, SkillUpdateProposalStatus
from app.research.errors import (
    ResearchProjectNotFoundError,
    SkillUpdateProposalAlreadyExistsError,
    SkillUpdateProposalNotFoundError,
    SkillUpdateProposalProvenanceError,
    SkillUpdateProposalSkillNotFoundError,
    SkillUpdateProposalVersionMismatchError,
)
from app.schemas.research import (
    SkillUpdateProposalCreateRequest,
    SkillUpdateProposalExportResponse,
    SkillUpdateProposalListResponse,
    SkillUpdateProposalResponse,
)
from app.security.principal import Principal
from app.services.research_project_service import ResearchProjectService
from app.services.skill_update_proposal_service import SkillUpdateProposalService

router = APIRouter(prefix="/api/v3", tags=["skill update proposals"])

ResearchProjectServiceDependency = Annotated[
    ResearchProjectService, Depends(get_research_project_service)
]
SkillUpdateProposalServiceDependency = Annotated[
    SkillUpdateProposalService, Depends(get_skill_update_proposal_service)
]
ResearchReader = Annotated[Principal, Depends(requires("research:read"))]
ResearchWriter = Annotated[Principal, Depends(requires("research:write"))]


async def _authorized_research_project(
    research_project_id: str,
    project_service: ResearchProjectService,
    principal: Principal,
    request: Request,
) -> ResearchProjectDocument:
    """Require current V1 access to every project linked to the research workspace."""
    try:
        project = await project_service.get_project(research_project_id)
    except ResearchProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="research project was not found",
        ) from error
    await authorize_research_project_links(request, principal, project.related_project_ids)
    return project


async def _authorize_skill_read(request: Request, principal: Principal) -> None:
    """Use V1's existing global skill read authorization for canonical-version lookup."""
    await authorize_scope(request, principal, "skills:read")


@router.post(
    "/skill-update-proposals",
    response_model=SkillUpdateProposalResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Research project or canonical skill was not found"
        },
        status.HTTP_409_CONFLICT: {
            "description": "Proposal already exists or its skill version is stale"
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Proposal evidence is invalid"},
    },
)
async def create_skill_update_proposal(
    payload: SkillUpdateProposalCreateRequest,
    service: SkillUpdateProposalServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    request: Request,
) -> SkillUpdateProposalResponse:
    """Store generated patch data; never apply, commit, import, or activate it."""
    await _authorized_research_project(
        payload.research_project_id, project_service, principal, request
    )
    await _authorize_skill_read(request, principal)
    try:
        proposal = await service.create_proposal(
            research_project_id=payload.research_project_id,
            proposal_id=payload.proposal_id,
            skill_id=payload.skill_id,
            current_version=payload.current_version,
            proposed_change=payload.proposed_change,
            proposed_patch=payload.proposed_patch,
            source_claim_ids=payload.source_claim_ids,
            source_citation_ids=payload.source_citation_ids,
            reason=payload.reason,
            created_by=principal.key_id,
            allowed_project_ids=principal.project_selector(),
        )
    except (ResearchProjectNotFoundError, SkillUpdateProposalSkillNotFoundError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (
        SkillUpdateProposalAlreadyExistsError,
        SkillUpdateProposalVersionMismatchError,
    ) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except SkillUpdateProposalProvenanceError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    await record_research_write(request, principal, proposal.research_project_id)
    return SkillUpdateProposalResponse.from_document(proposal)


@router.get("/skill-update-proposals", response_model=SkillUpdateProposalListResponse)
async def list_skill_update_proposals(
    research_project_id: Identifier,
    service: SkillUpdateProposalServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    request: Request,
    status_filter: Annotated[SkillUpdateProposalStatus | None, Query(alias="status")] = None,
    skill_id: Identifier | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> SkillUpdateProposalListResponse:
    """Cursor-page only generated proposal records covered by current and retained grants."""
    await _authorized_research_project(research_project_id, project_service, principal, request)
    await _authorize_skill_read(request, principal)
    page = await service.list_proposals(
        research_project_id=research_project_id,
        status=status_filter,
        skill_id=skill_id,
        allowed_project_ids=principal.project_selector(),
        limit=limit,
        cursor=cursor,
    )
    return SkillUpdateProposalListResponse(
        items=tuple(SkillUpdateProposalResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.get(
    "/skill-update-proposals/{proposal_id}/export",
    response_model=SkillUpdateProposalExportResponse,
)
async def export_skill_update_proposal_patch(
    proposal_id: Identifier,
    research_project_id: Identifier,
    service: SkillUpdateProposalServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    request: Request,
) -> SkillUpdateProposalExportResponse:
    """Return an inert patch for external human review; no file is created or changed."""
    await _authorized_research_project(research_project_id, project_service, principal, request)
    await _authorize_skill_read(request, principal)
    try:
        proposal = await service.export_patch(
            research_project_id=research_project_id,
            proposal_id=proposal_id,
            allowed_project_ids=principal.project_selector(),
        )
    except SkillUpdateProposalNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    response = SkillUpdateProposalResponse.from_document(proposal)
    return SkillUpdateProposalExportResponse(
        proposal=response, proposed_patch=proposal.proposed_patch
    )


@router.get("/skill-update-proposals/{proposal_id}", response_model=SkillUpdateProposalResponse)
async def get_skill_update_proposal(
    proposal_id: Identifier,
    research_project_id: Identifier,
    service: SkillUpdateProposalServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    request: Request,
) -> SkillUpdateProposalResponse:
    """Read one generated proposal without making it canonical or trusted."""
    await _authorized_research_project(research_project_id, project_service, principal, request)
    await _authorize_skill_read(request, principal)
    try:
        proposal = await service.get_proposal(
            research_project_id=research_project_id,
            proposal_id=proposal_id,
            allowed_project_ids=principal.project_selector(),
        )
    except SkillUpdateProposalNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return SkillUpdateProposalResponse.from_document(proposal)
