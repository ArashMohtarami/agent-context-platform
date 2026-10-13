"""V3 Phase 6 authorized research notes and explicit claim assessments."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.concurrency import RevisionConflict
from app.api.dependencies import (
    get_research_notes_claims_service,
    get_research_project_service,
)
from app.api.security import authorize_research_project_links, record_research_write, requires
from app.db.errors import SearchUnavailableError
from app.models.common import Identifier
from app.models.research import ClaimState
from app.repositories.errors import ConcurrencyConflictError
from app.research.errors import (
    ResearchCitationNotFoundError,
    ResearchClaimAlreadyExistsError,
    ResearchClaimNotFoundError,
    ResearchNoteAlreadyExistsError,
    ResearchNoteNotFoundError,
    ResearchProjectNotFoundError,
    ResearchSourceReferenceNotFoundError,
)
from app.schemas.research import (
    ResearchClaimCreateRequest,
    ResearchClaimListResponse,
    ResearchClaimResponse,
    ResearchClaimUpdateRequest,
    ResearchNoteCreateRequest,
    ResearchNoteResponse,
    ResearchNoteSearchItemResponse,
    ResearchNoteSearchRequest,
    ResearchNoteSearchResponse,
    ResearchNoteUpdateRequest,
)
from app.security.principal import Principal
from app.services.research_notes_claims_service import ResearchNotesClaimsService
from app.services.research_project_service import ResearchProjectService

router = APIRouter(prefix="/api/v3/research", tags=["research"])

ResearchProjectServiceDependency = Annotated[
    ResearchProjectService, Depends(get_research_project_service)
]
ResearchNotesClaimsServiceDependency = Annotated[
    ResearchNotesClaimsService, Depends(get_research_notes_claims_service)
]
ResearchReader = Annotated[Principal, Depends(requires("research:read"))]
ResearchWriter = Annotated[Principal, Depends(requires("research:write"))]


async def _authorize_current_project(
    research_project_id: str,
    project_service: ResearchProjectService,
    principal: Principal,
    http_request: Request,
) -> None:
    """Require every current V1 link before touching an artifact collection."""
    try:
        project = await project_service.get_project(research_project_id)
    except ResearchProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research project was not found"
        ) from error
    await authorize_research_project_links(http_request, principal, project.related_project_ids)


@router.post(
    "/notes",
    response_model=ResearchNoteResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Research project or source reference was not found"
        },
        status.HTTP_409_CONFLICT: {"description": "Research note identifier is taken"},
    },
)
async def create_research_note(
    request: ResearchNoteCreateRequest,
    service: ResearchNotesClaimsServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    http_request: Request,
) -> ResearchNoteResponse:
    """Create a working note without writing durable knowledge or inferring facts."""
    await _authorize_current_project(
        request.research_project_id, project_service, principal, http_request
    )
    try:
        note = await service.create_note(
            research_project_id=request.research_project_id,
            note_id=request.note_id,
            title=request.title,
            body=request.body,
            source_refs=request.source_refs,
            status=request.status,
            created_by=principal.key_id if principal.authenticated else None,
            allowed_project_ids=principal.projects,
        )
    except ResearchSourceReferenceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research source reference was not found"
        ) from error
    except ResearchProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research project was not found"
        ) from error
    except ResearchNoteAlreadyExistsError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    await record_research_write(http_request, principal, note.research_project_id)
    return ResearchNoteResponse.from_document(note)


@router.post(
    "/notes/search",
    response_model=ResearchNoteSearchResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Research project was not found"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Research note search is unavailable"},
    },
)
async def search_research_notes(
    request: ResearchNoteSearchRequest,
    service: ResearchNotesClaimsServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    http_request: Request,
) -> ResearchNoteSearchResponse:
    """Search working notes only; relevance never changes claim status or confidence."""
    await _authorize_current_project(
        request.research_project_id, project_service, principal, http_request
    )
    try:
        matches = await service.search_notes(
            research_project_id=request.research_project_id,
            query=request.query,
            phrase=request.phrase,
            limit=request.limit,
            allowed_project_ids=principal.projects,
        )
    except ResearchProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research project was not found"
        ) from error
    except SearchUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    return ResearchNoteSearchResponse(
        research_project_id=request.research_project_id,
        items=tuple(
            ResearchNoteSearchItemResponse(
                note=ResearchNoteResponse.from_document(match.note), retrieval_score=match.score
            )
            for match in matches
        ),
    )


@router.patch(
    "/notes/{note_id}",
    response_model=ResearchNoteResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Research note or source reference was not found"
        },
        status.HTTP_409_CONFLICT: {"description": "Research note changed after it was read"},
    },
)
async def update_research_note(
    note_id: Identifier,
    request: ResearchNoteUpdateRequest,
    service: ResearchNotesClaimsServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    http_request: Request,
) -> ResearchNoteResponse:
    """Revision-guard a note after checking both present and stored V1 scopes."""
    await _authorize_current_project(
        request.research_project_id, project_service, principal, http_request
    )
    try:
        current = await service.get_note(
            research_project_id=request.research_project_id, note_id=note_id
        )
    except ResearchNoteNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research note was not found"
        ) from error
    await authorize_research_project_links(
        http_request, principal, current.authorization.project_ids
    )
    changes = request.model_dump(
        exclude={"research_project_id", "expected_revision"}, exclude_unset=True
    )
    try:
        note = await service.update_note(
            research_project_id=request.research_project_id,
            note_id=note_id,
            expected_revision=request.expected_revision,
            changes=changes,
            allowed_project_ids=principal.projects,
        )
    except ResearchSourceReferenceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research source reference was not found"
        ) from error
    except ResearchNoteNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research note was not found"
        ) from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    await record_research_write(http_request, principal, note.research_project_id)
    return ResearchNoteResponse.from_document(note)


@router.post(
    "/claims",
    response_model=ResearchClaimResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Research project or citation was not found"},
        status.HTTP_409_CONFLICT: {"description": "Research claim identifier is taken"},
    },
)
async def create_research_claim(
    request: ResearchClaimCreateRequest,
    service: ResearchNotesClaimsServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    http_request: Request,
) -> ResearchClaimResponse:
    """Persist a caller-selected claim state; no semantic result labels it supported."""
    await _authorize_current_project(
        request.research_project_id, project_service, principal, http_request
    )
    try:
        claim = await service.create_claim(
            research_project_id=request.research_project_id,
            claim_id=request.claim_id,
            statement=request.statement,
            status=request.status,
            confidence=request.confidence,
            confidence_rationale=request.confidence_rationale,
            supporting_citation_ids=request.supporting_citations,
            contradicting_citation_ids=request.contradicting_citations,
            created_by=principal.key_id if principal.authenticated else None,
            allowed_project_ids=principal.projects,
        )
    except ResearchCitationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research citation was not found"
        ) from error
    except ResearchProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research project was not found"
        ) from error
    except ResearchClaimAlreadyExistsError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    await record_research_write(http_request, principal, claim.research_project_id)
    return ResearchClaimResponse.from_document(claim)


@router.get("/claims", response_model=ResearchClaimListResponse)
async def list_research_claims(
    research_project_id: Identifier,
    service: ResearchNotesClaimsServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    http_request: Request,
    status_filter: Annotated[ClaimState | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> ResearchClaimListResponse:
    """Support the requested CLI list through one bounded, authorized API read."""
    await _authorize_current_project(research_project_id, project_service, principal, http_request)
    page = await service.list_claims(
        research_project_id=research_project_id,
        status=status_filter,
        allowed_project_ids=principal.projects,
        limit=limit,
        cursor=cursor,
    )
    return ResearchClaimListResponse(
        items=tuple(ResearchClaimResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.get(
    "/claims/{claim_id}",
    response_model=ResearchClaimResponse,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Research claim was not found"}},
)
async def get_research_claim(
    claim_id: Identifier,
    research_project_id: Identifier,
    service: ResearchNotesClaimsServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchReader,
    http_request: Request,
) -> ResearchClaimResponse:
    """Support the requested CLI show through present and retained V1 authorization."""
    await _authorize_current_project(research_project_id, project_service, principal, http_request)
    try:
        claim = await service.get_claim(research_project_id=research_project_id, claim_id=claim_id)
    except ResearchClaimNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research claim was not found"
        ) from error
    await authorize_research_project_links(http_request, principal, claim.authorization.project_ids)
    return ResearchClaimResponse.from_document(claim)


@router.patch(
    "/claims/{claim_id}",
    response_model=ResearchClaimResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Research claim or citation was not found"},
        status.HTTP_409_CONFLICT: {"description": "Research claim changed after it was read"},
    },
)
async def update_research_claim(
    claim_id: Identifier,
    request: ResearchClaimUpdateRequest,
    service: ResearchNotesClaimsServiceDependency,
    project_service: ResearchProjectServiceDependency,
    principal: ResearchWriter,
    http_request: Request,
) -> ResearchClaimResponse:
    """Update only explicitly supplied evidence state; promotion remains out of scope."""
    await _authorize_current_project(
        request.research_project_id, project_service, principal, http_request
    )
    try:
        current = await service.get_claim(
            research_project_id=request.research_project_id, claim_id=claim_id
        )
    except ResearchClaimNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research claim was not found"
        ) from error
    await authorize_research_project_links(
        http_request, principal, current.authorization.project_ids
    )
    request_changes = request.model_dump(
        exclude={"research_project_id", "expected_revision"}, exclude_unset=True
    )
    changes = {
        ("supporting_citation_ids" if key == "supporting_citations" else key): value
        for key, value in request_changes.items()
    }
    changes = {
        ("contradicting_citation_ids" if key == "contradicting_citations" else key): value
        for key, value in changes.items()
    }
    try:
        claim = await service.update_claim(
            research_project_id=request.research_project_id,
            claim_id=claim_id,
            expected_revision=request.expected_revision,
            changes=changes,
            allowed_project_ids=principal.projects,
        )
    except ResearchCitationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research citation was not found"
        ) from error
    except ResearchClaimNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="research claim was not found"
        ) from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    await record_research_write(http_request, principal, claim.research_project_id)
    return ResearchClaimResponse.from_document(claim)
