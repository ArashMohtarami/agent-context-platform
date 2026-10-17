"""V3 Phase 9 review-gated knowledge-candidate endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.concurrency import RevisionConflict
from app.api.dependencies import get_knowledge_candidate_service, get_research_project_service
from app.api.security import (
    authorize_project,
    authorize_research_project_links,
    authorize_scope,
    record_research_write,
    record_write,
    requires,
)
from app.models.common import Identifier
from app.models.research import CandidateState, CandidateType, ResearchProjectDocument
from app.repositories.errors import ConcurrencyConflictError
from app.research.errors import (
    CandidatePromotionStateError,
    CandidatePromotionTargetError,
    CandidateProvenanceError,
    CandidateReviewStateError,
    CandidateTargetProjectNotFoundError,
    KnowledgeCandidateAlreadyExistsError,
    KnowledgeCandidateNotFoundError,
    ResearchProjectNotFoundError,
)
from app.schemas.research import (
    KnowledgeCandidateCreateRequest,
    KnowledgeCandidateListResponse,
    KnowledgeCandidatePromotionRequest,
    KnowledgeCandidatePromotionResponse,
    KnowledgeCandidateRejectRequest,
    KnowledgeCandidateResponse,
    KnowledgeCandidateReviewRequest,
    KnowledgeCandidateReviewResponse,
    PromotionEventResponse,
    PromotionTargetResponse,
)
from app.security.principal import Principal
from app.services.knowledge_candidate_service import (
    CandidatePromotion,
    CandidateReview,
    KnowledgeCandidateService,
)
from app.services.research_project_service import ResearchProjectService

router = APIRouter(prefix="/api/v3", tags=["knowledge candidates"])

ResearchProjectServiceDependency = Annotated[
    ResearchProjectService, Depends(get_research_project_service)
]
CandidateServiceDependency = Annotated[
    KnowledgeCandidateService, Depends(get_knowledge_candidate_service)
]
ResearchReader = Annotated[Principal, Depends(requires("research:read"))]
ResearchWriter = Annotated[Principal, Depends(requires("research:write"))]
ResearchApprover = Annotated[Principal, Depends(requires("research:approve"))]
ResearchPromoter = Annotated[Principal, Depends(requires("research:promote"))]


async def _authorized_research_project(
    research_project_id: str,
    project_service: ResearchProjectService,
    principal: Principal,
    request: Request,
) -> ResearchProjectDocument:
    """Check the current research project links before exposing candidates."""
    try:
        project = await project_service.get_project(research_project_id)
    except ResearchProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="research project was not found",
        ) from error
    await authorize_research_project_links(request, principal, project.related_project_ids)
    return project


@router.post(
    "/knowledge-candidates",
    response_model=KnowledgeCandidateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Research project or target project was not found"
        },
        status.HTTP_409_CONFLICT: {"description": "Candidate identifier is taken"},
    },
)
async def create_knowledge_candidate(
    payload: KnowledgeCandidateCreateRequest,
    service: CandidateServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    request: Request,
) -> KnowledgeCandidateResponse:
    """Create a pending proposal; no request can set it approved or promoted."""
    await _authorized_research_project(
        payload.research_project_id,
        project_service,
        principal,
        request,
    )
    await authorize_project(request, principal, payload.target_project_id)
    try:
        candidate = await service.create_candidate(
            research_project_id=payload.research_project_id,
            candidate_id=payload.candidate_id,
            candidate_type=payload.candidate_type,
            target_project_id=payload.target_project_id,
            target_skill_id=payload.target_skill_id,
            current_skill_version=payload.current_skill_version,
            title=payload.title,
            content=payload.content,
            source_claim_ids=payload.source_claim_ids,
            source_citation_ids=payload.source_citation_ids,
            confidence=payload.confidence,
            confidence_rationale=payload.confidence_rationale,
            freshness_class=payload.freshness_class,
            review_after=payload.review_after,
            created_by=principal.key_id,
            allowed_project_ids=principal.project_selector(),
        )
    except CandidateTargetProjectNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except CandidateProvenanceError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except KnowledgeCandidateAlreadyExistsError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    await record_research_write(request, principal, candidate.research_project_id)
    return KnowledgeCandidateResponse.from_document(candidate)


@router.get("/knowledge-candidates", response_model=KnowledgeCandidateListResponse)
async def list_knowledge_candidates(
    research_project_id: Identifier,
    service: CandidateServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    request: Request,
    status_filter: Annotated[
        CandidateState | None,
        Query(alias="status"),
    ] = None,
    target_project_id: Identifier | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> KnowledgeCandidateListResponse:
    """Page candidates visible to all of the caller's retained V1 grants."""
    await _authorized_research_project(research_project_id, project_service, principal, request)
    await authorize_project(request, principal, target_project_id)
    page = await service.list_candidates(
        research_project_id=research_project_id,
        status=status_filter,
        target_project_id=target_project_id,
        allowed_project_ids=principal.project_selector(),
        limit=limit,
        cursor=cursor,
    )
    return KnowledgeCandidateListResponse(
        items=tuple(KnowledgeCandidateResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.post(
    "/knowledge-candidates/{candidate_id}/promote",
    response_model=KnowledgeCandidatePromotionResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Candidate, research project, or context was not found"
        },
        status.HTTP_409_CONFLICT: {
            "description": "Candidate is not approved, was promoted, or changed"
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "description": "Candidate has no supported Phase 10 target"
        },
    },
)
async def promote_knowledge_candidate(
    candidate_id: Identifier,
    payload: KnowledgeCandidatePromotionRequest,
    service: CandidateServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchPromoter,
    request: Request,
) -> KnowledgeCandidatePromotionResponse:
    """Explicitly promote one approved candidate through the existing V1 service."""
    await _authorized_research_project(
        payload.research_project_id, project_service, principal, request
    )
    try:
        current = await service.get_candidate(
            research_project_id=payload.research_project_id,
            candidate_id=candidate_id,
            allowed_project_ids=principal.project_selector(),
        )
    except KnowledgeCandidateNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await authorize_project(request, principal, current.target_project_id)
    if current.candidate_type is CandidateType.MEMORY:
        await authorize_scope(request, principal, "memory:write")
    elif current.candidate_type in {
        CandidateType.PROJECT_CONTEXT,
        CandidateType.ARCHITECTURE_DECISION,
    }:
        await authorize_scope(request, principal, "context:write")
    try:
        promoted = await service.promote_candidate(
            research_project_id=payload.research_project_id,
            candidate_id=candidate_id,
            expected_revision=payload.expected_revision,
            reviewer=principal.key_id,
            allowed_project_ids=principal.project_selector(),
            supersedes_context_id=payload.supersedes_context_id,
        )
    except CandidatePromotionStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except CandidatePromotionTargetError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except CandidateProvenanceError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    await record_research_write(request, principal, promoted.candidate.research_project_id)
    await record_write(
        request,
        principal,
        "memory.write" if promoted.target_type == "memory" else "context.write",
        current.target_project_id,
    )
    return _promotion_response(promoted)


async def _review(
    *,
    research_project_id: str,
    candidate_id: str,
    expected_revision: int,
    decision: CandidateState,
    reason: str | None,
    service: KnowledgeCandidateService,
    project_service: ResearchProjectService,
    principal: Principal,
    request: Request,
) -> KnowledgeCandidateReviewResponse:
    """Apply one scoped decision and record its immutable event without promotion."""
    await _authorized_research_project(research_project_id, project_service, principal, request)
    try:
        current = await service.get_candidate(
            research_project_id=research_project_id,
            candidate_id=candidate_id,
            allowed_project_ids=principal.project_selector(),
        )
    except KnowledgeCandidateNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    await authorize_project(request, principal, current.target_project_id)
    try:
        reviewed = await service.review_candidate(
            research_project_id=research_project_id,
            candidate_id=candidate_id,
            expected_revision=expected_revision,
            decision=decision,
            reviewer=principal.key_id,
            reason=reason,
            allowed_project_ids=principal.project_selector(),
        )
    except CandidateReviewStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    await record_research_write(request, principal, reviewed.candidate.research_project_id)
    return _review_response(reviewed)


@router.post(
    "/knowledge-candidates/{candidate_id}/approve",
    response_model=KnowledgeCandidateReviewResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Candidate or research project was not found"},
        status.HTTP_409_CONFLICT: {"description": "Candidate cannot be approved"},
    },
)
async def approve_knowledge_candidate(
    candidate_id: Identifier,
    payload: KnowledgeCandidateReviewRequest,
    service: CandidateServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchApprover,
    request: Request,
) -> KnowledgeCandidateReviewResponse:
    """Explicitly approve a proposal but leave every V1 target unchanged."""
    return await _review(
        research_project_id=payload.research_project_id,
        candidate_id=candidate_id,
        expected_revision=payload.expected_revision,
        decision=CandidateState.APPROVED,
        reason=payload.reason,
        service=service,
        project_service=project_service,
        principal=principal,
        request=request,
    )


@router.post(
    "/knowledge-candidates/{candidate_id}/reject",
    response_model=KnowledgeCandidateReviewResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Candidate or research project was not found"},
        status.HTTP_409_CONFLICT: {"description": "Candidate cannot be rejected"},
    },
)
async def reject_knowledge_candidate(
    candidate_id: Identifier,
    payload: KnowledgeCandidateRejectRequest,
    service: CandidateServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    request: Request,
) -> KnowledgeCandidateReviewResponse:
    """Reject or return for changes; the decision remains non-promoting."""
    return await _review(
        research_project_id=payload.research_project_id,
        candidate_id=candidate_id,
        expected_revision=payload.expected_revision,
        decision=payload.decision,
        reason=payload.reason,
        service=service,
        project_service=project_service,
        principal=principal,
        request=request,
    )


def _promotion_response(promotion: CandidatePromotion) -> KnowledgeCandidatePromotionResponse:
    return KnowledgeCandidatePromotionResponse(
        candidate=KnowledgeCandidateResponse.from_document(promotion.candidate),
        event=PromotionEventResponse.from_document(promotion.event),
        target=PromotionTargetResponse(
            target_type=promotion.target_type,
            target_id=promotion.target_id,
            provenance=promotion.provenance,
        ),
    )


def _review_response(review: CandidateReview) -> KnowledgeCandidateReviewResponse:
    return KnowledgeCandidateReviewResponse(
        candidate=KnowledgeCandidateResponse.from_document(review.candidate),
        event=PromotionEventResponse.from_document(review.event),
    )
