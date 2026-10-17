"""V3 Phase 7 authorized citation and evidence-packet endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_research_evidence_service, get_research_project_service
from app.api.security import authorize_research_project_links, record_research_write, requires
from app.models.common import Identifier
from app.models.research import ResearchProjectDocument
from app.research.errors import (
    ResearchCitationAlreadyExistsError,
    ResearchCitationProvenanceError,
    ResearchClaimNotFoundError,
    ResearchProjectNotFoundError,
)
from app.schemas.research import (
    ResearchCitationCreateRequest,
    ResearchCitationResponse,
    ResearchClaimEvidenceResponse,
    ResearchEvidencePacketRequest,
    ResearchEvidencePacketResponse,
)
from app.security.principal import Principal
from app.services.research_evidence_service import ResearchEvidenceService
from app.services.research_project_service import ResearchProjectService
from app.services.research_search_service import ResearchSearchError, ResearchSearchUnavailableError

router = APIRouter(prefix="/api/v3/research", tags=["research"])

ResearchProjectServiceDependency = Annotated[
    ResearchProjectService, Depends(get_research_project_service)
]
ResearchEvidenceServiceDependency = Annotated[
    ResearchEvidenceService, Depends(get_research_evidence_service)
]
ResearchReader = Annotated[Principal, Depends(requires("research:read"))]
ResearchWriter = Annotated[Principal, Depends(requires("research:write"))]


async def _authorized_project(
    research_project_id: str,
    project_service: ResearchProjectService,
    principal: Principal,
    http_request: Request,
) -> ResearchProjectDocument:
    """Check every linked V1 project before revealing an evidence record or count."""
    try:
        project = await project_service.get_project(research_project_id)
    except ResearchProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research project was not found"
        ) from error
    await authorize_research_project_links(http_request, principal, project.related_project_ids)
    return project


@router.post(
    "/citations",
    response_model=ResearchCitationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Research project or claim was not found"},
        status.HTTP_409_CONFLICT: {
            "description": "Citation identifier is already used by this claim"
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Citation provenance was not valid"},
    },
)
async def create_research_citation(
    request: ResearchCitationCreateRequest,
    service: ResearchEvidenceServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    http_request: Request,
) -> ResearchCitationResponse:
    """Persist an explicit citation without inferring support or updating a claim."""
    await _authorized_project(request.research_project_id, project_service, principal, http_request)
    try:
        citation = await service.create_citation(
            research_project_id=request.research_project_id,
            citation_id=request.citation_id,
            claim_id=request.claim_id,
            source_id=request.source_id,
            snapshot_id=request.snapshot_id,
            chunk_id=request.chunk_id,
            locator=request.locator,
            evidence_excerpt=request.evidence_excerpt,
            relation=request.relation,
            created_by=principal.key_id if principal.authenticated else None,
            allowed_project_ids=principal.project_selector(),
        )
    except ResearchClaimNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research claim was not found"
        ) from error
    except ResearchCitationAlreadyExistsError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ResearchCitationProvenanceError as error:
        # Keep source/snapshot/chunk authorization failures indistinguishable
        # from malformed provenance references.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="research citation provenance was not valid",
        ) from error
    await record_research_write(http_request, principal, citation.research_project_id)
    return ResearchCitationResponse.from_document(citation)


@router.get(
    "/claims/{claim_id}/evidence",
    response_model=ResearchClaimEvidenceResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Research claim was not found"}},
)
async def get_claim_evidence(
    claim_id: Identifier,
    research_project_id: Identifier,
    service: ResearchEvidenceServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    http_request: Request,
) -> ResearchClaimEvidenceResponse:
    """Read the claim's persisted support, conflict, qualifier, and background relations."""
    await _authorized_project(research_project_id, project_service, principal, http_request)
    try:
        evidence = await service.get_claim_evidence(
            research_project_id=research_project_id,
            claim_id=claim_id,
            allowed_project_ids=principal.project_selector(),
        )
    except ResearchClaimNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research claim was not found"
        ) from error
    return ResearchClaimEvidenceResponse.from_evidence(
        research_project_id=research_project_id, claim_id=claim_id, evidence=evidence
    )


@router.post(
    "/evidence",
    response_model=ResearchEvidencePacketResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Research project was not found"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Evidence request was not valid"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Research search is unavailable"},
    },
)
async def build_evidence_packet(
    request: ResearchEvidencePacketRequest,
    service: ResearchEvidenceServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    http_request: Request,
) -> ResearchEvidencePacketResponse:
    """Present relevant context with authored evidence and visible uncertainty/conflict."""
    project = await _authorized_project(
        request.research_project_id, project_service, principal, http_request
    )
    if request.project_id is not None and request.project_id not in project.related_project_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="project_id is not linked to this research project",
        )
    try:
        packet = await service.build_evidence_packet(
            query=request.query,
            research_project_id=request.research_project_id,
            project_id=request.project_id,
            token_budget=request.token_budget,
            allowed_project_ids=principal.project_selector(),
        )
    except ResearchSearchUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    except (ResearchCitationProvenanceError, ResearchSearchError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return ResearchEvidencePacketResponse.from_packet(packet)
