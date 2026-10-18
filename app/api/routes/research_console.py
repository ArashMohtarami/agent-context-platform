"""Session-only evidence console; domain services retain ingestion/review rules."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.params import Depends as Dependency

from app.api import dependencies as services
from app.identity.browser import CurrentBrowser
from app.knowledge.boundary import action_gate, resource
from app.models.common import Identifier
from app.models.research import (
    CandidateState,
    ClaimState,
    ResearchProjectStatus,
    ResearchRecordStatus,
)
from app.research.console.boundary import ResearchRoute, projects, research_resource
from app.research.console.contracts import (
    BrowserIngestion,
    ClaimDecision,
    ConsoleEvidence,
    Decision,
    ProposalDecision,
    RetainedChunk,
    RetainedDocument,
)
from app.research.console.service import (
    Kind,
    Record,
    ResearchConsole,
    browser_source_type,
    project_document,
    skill_allowed,
)
from app.schemas import research as dto
from app.security.policy.evaluation import PolicyDenied
from app.security.tenant_policy import current_policy

router = APIRouter(
    prefix="/api/v6/organizations/{org_id}/research",
    tags=["human research"],
    route_class=ResearchRoute,
)
Limit = Annotated[int, Query(ge=1, le=50)]


def gate(action: str) -> list[Dependency]:
    return [Depends(action_gate(action))]


def console(request: Request, context: CurrentBrowser) -> ResearchConsole:
    return ResearchConsole(request, context)


@router.get(
    "/projects", response_model=dto.ResearchProjectListResponse, dependencies=gate("research:read")
)
async def projects_page(
    request: Request, context: CurrentBrowser, limit: Limit = 20, cursor: str | None = None
) -> dto.ResearchProjectListResponse:
    page = await services.get_research_project_service(request).list_projects(
        allowed_project_ids=projects(), status=None, limit=limit, cursor=cursor
    )
    return dto.ResearchProjectListResponse(
        items=tuple(dto.ResearchProjectResponse.from_document(d) for d in page.items),
        next_cursor=page.next_cursor,
    )


@router.post(
    "/projects",
    response_model=dto.ResearchProjectResponse,
    status_code=201,
    dependencies=gate("research:write"),
)
async def create_project(
    body: dto.ResearchProjectCreateRequest, request: Request, context: CurrentBrowser
) -> dto.ResearchProjectResponse:
    research_resource(body.research_project_id)
    for p in body.related_project_ids:
        resource("research:write", "project", p)
    if not body.related_project_ids:
        resource("research:write", "organization_knowledge", context.row["org_id"])
    row = await services.get_research_project_service(request).create_project(
        **body.model_dump(exclude={"status"}),
        status=ResearchProjectStatus(body.status),
        owner_key_id=None,
    )
    return dto.ResearchProjectResponse.from_document(row)


@router.get(
    "/projects/{research_project_id}",
    response_model=dto.ResearchProjectResponse,
    dependencies=gate("research:read"),
)
async def project(
    research_project_id: Identifier, request: Request, context: CurrentBrowser
) -> dto.ResearchProjectResponse:
    return dto.ResearchProjectResponse.from_document(
        await console(request, context).project(research_project_id)
    )


@router.patch(
    "/projects/{research_project_id}",
    response_model=dto.ResearchProjectResponse,
    dependencies=gate("research:write"),
)
async def update_project(
    research_project_id: Identifier,
    body: dto.ResearchProjectUpdateRequest,
    request: Request,
    context: CurrentBrowser,
) -> dto.ResearchProjectResponse:
    await console(request, context).project(research_project_id)
    if body.related_project_ids is not None:
        for p in body.related_project_ids:
            resource("research:write", "project", p)
        if not body.related_project_ids:
            resource("research:write", "organization_knowledge", context.row["org_id"])
    row = await services.get_research_project_service(request).update_project(
        research_project_id,
        expected_revision=body.expected_revision,
        changes=body.model_dump(exclude={"expected_revision"}, exclude_unset=True),
    )
    return dto.ResearchProjectResponse.from_document(row)


@router.get(
    "/projects/{research_project_id}/records/{kind}",
    response_model=dto.ResearchPageSchema[Record],
    dependencies=gate("research:read"),
)
async def catalog(
    research_project_id: Identifier,
    kind: Kind,
    request: Request,
    context: CurrentBrowser,
    limit: Limit = 20,
    cursor: str | None = None,
    source_id: Identifier | None = None,
    status: Annotated[str | None, Query(max_length=32)] = None,
) -> dto.ResearchPageSchema[Record]:
    return dto.ResearchPageSchema[Record].model_validate(
        await console(request, context).page(
            research_project_id,
            kind,
            limit=limit,
            cursor=cursor,
            source_id=source_id,
            status=status,
        )
    )


@router.post(
    "/sources",
    response_model=dto.ResearchSourceResponse,
    status_code=201,
    dependencies=gate("research:write"),
)
async def create_source(
    body: dto.ResearchSourceCreateRequest, request: Request, context: CurrentBrowser
) -> dto.ResearchSourceResponse:
    await console(request, context).project(body.research_project_id)
    browser_source_type(body.source_type)
    from app.api.routes.research_sources import _snapshot_registration

    row = await services.get_research_source_service(request).create_source(
        **body.model_dump(exclude={"snapshot", "status"}),
        status=ResearchRecordStatus(body.status),
        owner_key_id=None,
        snapshot=_snapshot_registration(body),
    )
    return dto.ResearchSourceResponse.from_document(row.source, registered_snapshot=row.snapshot)


@router.get(
    "/projects/{research_project_id}/sources/{source_id}",
    response_model=dto.ResearchSourceResponse,
    dependencies=gate("research:read"),
)
async def source(
    research_project_id: Identifier,
    source_id: Identifier,
    request: Request,
    context: CurrentBrowser,
) -> dto.ResearchSourceResponse:
    return dto.ResearchSourceResponse.from_document(
        await console(request, context).source(research_project_id, source_id)
    )


@router.post(
    "/projects/{research_project_id}/sources/{source_id}/ingest",
    response_model=dto.ResearchIngestionResponse,
    dependencies=gate("research:write"),
)
async def ingest(
    research_project_id: Identifier,
    source_id: Identifier,
    body: BrowserIngestion,
    request: Request,
    context: CurrentBrowser,
) -> dto.ResearchIngestionResponse:
    from app.research.providers import IngestionPayload

    row = await console(request, context).source(research_project_id, source_id)
    browser_source_type(row.source_type)
    result = await services.get_research_ingestion_service(request).ingest(
        source=row,
        payload=IngestionPayload(
            data=body.submitted_bytes(),
            content_type=body.content_type or "application/octet-stream",
            title=body.title or row.title,
            language=body.language,
            filename=body.filename,
            acquire_url=body.fetch_url,
        ),
        manually_provided=body.manually_provided,
    )
    return dto.ResearchIngestionResponse(
        source_id=source_id,
        snapshot=dto.SourceSnapshotResponse.from_document(result.snapshot),
        job=project_document(result.job, dto.IngestionJobResponse),
        idempotent=result.idempotent,
    )


@router.get(
    "/projects/{research_project_id}/sources/{source_id}/jobs/{job_id}",
    response_model=dto.IngestionJobResponse,
    dependencies=gate("research:read"),
)
async def job(
    research_project_id: Identifier,
    source_id: Identifier,
    job_id: Identifier,
    request: Request,
    context: CurrentBrowser,
) -> dto.IngestionJobResponse:
    return dto.IngestionJobResponse.model_validate(
        project_document(
            await console(request, context).job(research_project_id, source_id, job_id),
            dto.IngestionJobResponse,
        )
    )


@router.get(
    "/projects/{research_project_id}/sources/{source_id}/jobs/{job_id}/documents",
    response_model=dto.ResearchPageSchema[RetainedDocument],
    dependencies=gate("research:read"),
)
async def job_documents(
    research_project_id: Identifier,
    source_id: Identifier,
    job_id: Identifier,
    request: Request,
    context: CurrentBrowser,
    limit: Limit = 20,
    cursor: str | None = None,
) -> dto.ResearchPageSchema[RetainedDocument]:
    service = console(request, context)
    row = await service.job(research_project_id, source_id, job_id)
    from app.repositories.research_repository import MongoResearchDocumentRepository

    page = await MongoResearchDocumentRepository(service.db, research_project_id).list(
        source_id=source_id, snapshot_id=row.snapshot_id, limit=limit, cursor=cursor
    )
    return dto.ResearchPageSchema(
        items=tuple(project_document(d, RetainedDocument) for d in page.items),
        next_cursor=page.next_cursor,
    )


@router.get(
    "/projects/{research_project_id}/sources/{source_id}/snapshots/{snapshot_id}/documents/{document_id}/chunks",
    response_model=dto.ResearchPageSchema[RetainedChunk],
    dependencies=gate("research:read"),
)
async def chunks(
    research_project_id: Identifier,
    source_id: Identifier,
    snapshot_id: Identifier,
    document_id: Identifier,
    request: Request,
    context: CurrentBrowser,
    limit: Limit = 20,
    cursor: str | None = None,
) -> dto.ResearchPageSchema[RetainedChunk]:
    return dto.ResearchPageSchema[RetainedChunk].model_validate(
        await console(request, context).chunks(
            research_project_id, source_id, snapshot_id, document_id, limit, cursor
        )
    )


@router.post(
    "/notes",
    response_model=dto.ResearchNoteResponse,
    status_code=201,
    dependencies=gate("research:write"),
)
async def create_note(
    body: dto.ResearchNoteCreateRequest, request: Request, context: CurrentBrowser
) -> dto.ResearchNoteResponse:
    await console(request, context).project(body.research_project_id)
    row = await services.get_research_notes_claims_service(request).create_note(
        **{k: getattr(body, k) for k in type(body).model_fields},
        created_by=None,
        allowed_project_ids=projects(),
    )
    return dto.ResearchNoteResponse.from_document(row)


@router.patch(
    "/notes/{note_id}", response_model=dto.ResearchNoteResponse, dependencies=gate("research:write")
)
async def update_note(
    note_id: Identifier,
    body: dto.ResearchNoteUpdateRequest,
    request: Request,
    context: CurrentBrowser,
) -> dto.ResearchNoteResponse:
    await console(request, context).project(body.research_project_id)
    changes = {
        k: getattr(body, k)
        for k in body.model_fields_set - {"research_project_id", "expected_revision"}
    }
    row = await services.get_research_notes_claims_service(request).update_note(
        research_project_id=body.research_project_id,
        note_id=note_id,
        expected_revision=body.expected_revision,
        changes=changes,
        allowed_project_ids=projects(),
    )
    return dto.ResearchNoteResponse.from_document(row)


@router.post(
    "/claims",
    response_model=dto.ResearchClaimResponse,
    status_code=201,
    dependencies=gate("research:write"),
)
async def create_claim(
    body: dto.ResearchClaimCreateRequest, request: Request, context: CurrentBrowser
) -> dto.ResearchClaimResponse:
    await console(request, context).project(body.research_project_id)
    if body.status != ClaimState.UNREVIEWED:
        raise PolicyDenied("new claims require independent assessment")
    values = body.model_dump(exclude={"supporting_citations", "contradicting_citations"})
    row = await services.get_research_notes_claims_service(request).create_claim(
        **values,
        supporting_citation_ids=body.supporting_citations,
        contradicting_citation_ids=body.contradicting_citations,
        created_by=None,
        allowed_project_ids=projects(),
    )
    return dto.ResearchClaimResponse.from_document(row)


@router.post(
    "/projects/{research_project_id}/claims/{claim_id}/decision",
    response_model=dto.ResearchClaimResponse,
    dependencies=gate("research:approve"),
)
async def assess_claim(
    research_project_id: Identifier,
    claim_id: Identifier,
    body: ClaimDecision,
    request: Request,
    context: CurrentBrowser,
) -> dto.ResearchClaimResponse:
    await console(request, context).project(research_project_id)
    values = body.model_dump(
        exclude={"expected_revision", "supporting_citations", "contradicting_citations"}
    )
    values.update(
        supporting_citation_ids=body.supporting_citations,
        contradicting_citation_ids=body.contradicting_citations,
    )
    row = await services.get_research_notes_claims_service(request).update_claim(
        research_project_id=research_project_id,
        claim_id=claim_id,
        expected_revision=body.expected_revision,
        changes=values,
        allowed_project_ids=projects(),
    )
    return dto.ResearchClaimResponse.from_document(row)


@router.post(
    "/citations",
    response_model=dto.ResearchCitationResponse,
    status_code=201,
    dependencies=gate("research:write"),
)
async def create_citation(
    body: dto.ResearchCitationCreateRequest, request: Request, context: CurrentBrowser
) -> dto.ResearchCitationResponse:
    await console(request, context).project(body.research_project_id)
    row = await services.get_research_evidence_service(request).create_citation(
        **{k: getattr(body, k) for k in type(body).model_fields},
        created_by=None,
        allowed_project_ids=projects(),
    )
    return dto.ResearchCitationResponse.from_document(row)


@router.get(
    "/projects/{research_project_id}/claims/{claim_id}/evidence",
    response_model=ConsoleEvidence,
    dependencies=gate("research:read"),
)
async def evidence(
    research_project_id: Identifier, claim_id: Identifier, request: Request, context: CurrentBrowser
) -> ConsoleEvidence:
    await console(request, context).project(research_project_id)
    items = await services.get_research_evidence_service(request).get_claim_evidence(
        research_project_id=research_project_id, claim_id=claim_id, allowed_project_ids=projects()
    )
    return ConsoleEvidence.model_validate(
        dto.ResearchClaimEvidenceResponse.from_evidence(
            research_project_id=research_project_id, claim_id=claim_id, evidence=items
        ).model_dump()
    )


@router.post(
    "/briefs",
    response_model=dto.ResearchBriefResponse,
    status_code=201,
    dependencies=gate("research:write"),
)
async def build_brief(
    body: dto.ResearchBriefBuildRequest, request: Request, context: CurrentBrowser
) -> dto.ResearchBriefResponse:
    await console(request, context).project(body.research_project_id)
    row = await services.get_research_brief_service(request).build_brief(
        **body.model_dump(), created_by=None, allowed_project_ids=projects()
    )
    return dto.ResearchBriefResponse.from_document(row)


@router.post(
    "/candidates",
    response_model=dto.KnowledgeCandidateResponse,
    status_code=201,
    dependencies=gate("research:write"),
)
async def create_candidate(
    body: dto.KnowledgeCandidateCreateRequest, request: Request, context: CurrentBrowser
) -> dto.KnowledgeCandidateResponse:
    await console(request, context).project(body.research_project_id)
    if body.target_skill_id:
        raise PolicyDenied("use an inert skill proposal for canonical Git changes")
    if body.target_project_id:
        resource("research:write", "project", body.target_project_id)
    row = await services.get_knowledge_candidate_service(request).create_candidate(
        **body.model_dump(), created_by=None, allowed_project_ids=projects()
    )
    return dto.KnowledgeCandidateResponse.from_document(row)


@router.post(
    "/projects/{research_project_id}/candidates/{candidate_id}/decision",
    response_model=dto.KnowledgeCandidateReviewResponse,
    dependencies=gate("research:approve"),
)
async def review_candidate(
    research_project_id: Identifier,
    candidate_id: Identifier,
    body: Decision,
    request: Request,
    context: CurrentBrowser,
) -> dto.KnowledgeCandidateReviewResponse:
    await console(request, context).project(research_project_id)
    result = await services.get_knowledge_candidate_service(request).review_candidate(
        research_project_id=research_project_id,
        candidate_id=candidate_id,
        expected_revision=body.expected_revision,
        decision=CandidateState(body.decision),
        reviewer=context.row["user_id"],
        reason=body.reason,
        allowed_project_ids=projects(),
    )
    return dto.KnowledgeCandidateReviewResponse(
        candidate=dto.KnowledgeCandidateResponse.from_document(result.candidate),
        event=dto.PromotionEventResponse.from_document(result.event),
    )


@router.post(
    "/candidates/{candidate_id}/promote",
    response_model=dto.KnowledgeCandidatePromotionResponse,
    dependencies=gate("research:promote"),
)
async def promote(
    candidate_id: Identifier,
    body: dto.KnowledgeCandidatePromotionRequest,
    request: Request,
    context: CurrentBrowser,
) -> dto.KnowledgeCandidatePromotionResponse:
    await console(request, context).project(body.research_project_id)
    service = services.get_knowledge_candidate_service(request)
    candidate = await service.get_candidate(
        research_project_id=body.research_project_id,
        candidate_id=candidate_id,
        allowed_project_ids=projects(),
    )
    policy = current_policy()
    if policy is None:
        raise PolicyDenied("verified human policy is required")
    from app.security.policy.resources import resource_allows

    if not resource_allows(policy, "research:read", candidate.model_dump(), "knowledge_candidates"):
        raise PolicyDenied("promotion requires readable provenance")
    target_action = "memory:write" if candidate.candidate_type == "memory" else "context:write"
    resource(
        target_action,
        "project" if candidate.target_project_id else "organization_knowledge",
        candidate.target_project_id or context.row["org_id"],
    )
    result = await service.promote_candidate(
        research_project_id=body.research_project_id,
        candidate_id=candidate_id,
        expected_revision=body.expected_revision,
        reviewer=context.row["user_id"],
        allowed_project_ids=projects(),
        supersedes_context_id=body.supersedes_context_id,
    )
    return dto.KnowledgeCandidatePromotionResponse(
        candidate=dto.KnowledgeCandidateResponse.from_document(result.candidate),
        event=dto.PromotionEventResponse.from_document(result.event),
        target=dto.PromotionTargetResponse(
            target_type=result.target_type, target_id=result.target_id, provenance=result.provenance
        ),
    )


@router.post(
    "/proposals",
    response_model=dto.SkillUpdateProposalResponse,
    status_code=201,
    dependencies=gate("research:write"),
)
async def create_proposal(
    body: dto.SkillUpdateProposalCreateRequest, request: Request, context: CurrentBrowser
) -> dto.SkillUpdateProposalResponse:
    await console(request, context).project(body.research_project_id)
    skill_allowed("research:write", body.skill_id)
    row = await services.get_skill_update_proposal_service(request).create_proposal(
        **body.model_dump(), created_by=None, allowed_project_ids=projects()
    )
    return dto.SkillUpdateProposalResponse.from_document(row)


@router.post(
    "/projects/{research_project_id}/proposals/{proposal_id}/decision",
    response_model=dto.SkillUpdateProposalResponse,
    dependencies=gate("research:approve"),
)
async def review_proposal(
    research_project_id: Identifier,
    proposal_id: Identifier,
    body: ProposalDecision,
    request: Request,
    context: CurrentBrowser,
) -> dto.SkillUpdateProposalResponse:
    await console(request, context).project(research_project_id)
    service = services.get_skill_update_proposal_service(request)
    row = await service.get_proposal(
        research_project_id=research_project_id,
        proposal_id=proposal_id,
        allowed_project_ids=projects(),
    )
    skill_allowed("research:approve", row.skill_id)
    reviewed = await service.review_proposal(
        research_project_id=research_project_id,
        proposal_id=proposal_id,
        expected_revision=body.expected_revision,
        decision=body.decision,
        allowed_project_ids=projects(),
    )
    return dto.SkillUpdateProposalResponse.from_document(reviewed)


@router.get(
    "/projects/{research_project_id}/proposals/{proposal_id}/patch",
    response_model=dto.SkillUpdateProposalExportResponse,
    dependencies=gate("research:read"),
)
async def patch(
    research_project_id: Identifier,
    proposal_id: Identifier,
    request: Request,
    context: CurrentBrowser,
) -> dto.SkillUpdateProposalExportResponse:
    await console(request, context).project(research_project_id)
    row = await services.get_skill_update_proposal_service(request).export_patch(
        research_project_id=research_project_id,
        proposal_id=proposal_id,
        allowed_project_ids=projects(),
    )
    skill_allowed("research:read", row.skill_id)
    return dto.SkillUpdateProposalExportResponse(
        proposal=dto.SkillUpdateProposalResponse.from_document(row),
        proposed_patch=row.proposed_patch,
    )


@router.get(
    "/projects/{research_project_id}/freshness",
    response_model=dto.StaleKnowledgeListResponse,
    dependencies=gate("research:read"),
)
async def freshness(
    research_project_id: Identifier,
    request: Request,
    context: CurrentBrowser,
    limit: Limit = 20,
    cursor: str | None = None,
) -> dto.StaleKnowledgeListResponse:
    await console(request, context).project(research_project_id)
    page = await services.get_revalidation_service(request).list_stale_knowledge(
        research_project_id=research_project_id,
        allowed_project_ids=projects(),
        limit=limit,
        cursor=cursor,
    )
    return dto.StaleKnowledgeListResponse(
        items=tuple(dto.StaleKnowledgeResponse.from_value(i) for i in page.items),
        next_cursor=page.next_cursor,
    )


@router.post(
    "/candidates/{candidate_id}/revalidation",
    response_model=dto.RevalidationProposalResponse,
    dependencies=gate("research:write"),
)
async def revalidation(
    candidate_id: Identifier,
    body: dto.RevalidationProposalRequest,
    request: Request,
    context: CurrentBrowser,
) -> dto.RevalidationProposalResponse:
    await console(request, context).project(body.research_project_id)
    result = await services.get_revalidation_service(request).propose_revalidation(
        research_project_id=body.research_project_id,
        candidate_id=candidate_id,
        created_by=None,
        allowed_project_ids=projects(),
        as_of=body.as_of,
    )
    return dto.RevalidationProposalResponse.from_value(result)


@router.get(
    "/projects/{research_project_id}/record/{kind}/{record_id}",
    response_model=Record,
    dependencies=gate("research:read"),
)
async def record_detail(
    research_project_id: Identifier,
    kind: Literal["notes", "claims", "briefs", "candidates", "proposals"],
    record_id: Identifier,
    request: Request,
    context: CurrentBrowser,
) -> Record:
    await console(request, context).project(research_project_id)
    if kind in {"notes", "claims"}:
        service = services.get_research_notes_claims_service(request)
        if kind == "notes":
            return dto.ResearchNoteResponse.from_document(
                await service.get_note(research_project_id=research_project_id, note_id=record_id)
            )
        return dto.ResearchClaimResponse.from_document(
            await service.get_claim(research_project_id=research_project_id, claim_id=record_id)
        )
    if kind == "briefs":
        brief = await services.get_research_brief_service(request).get_brief(
            research_project_id=research_project_id,
            brief_id=record_id,
            allowed_project_ids=projects(),
        )
        return dto.ResearchBriefResponse.from_document(brief)
    if kind == "candidates":
        candidate = await services.get_knowledge_candidate_service(request).get_candidate(
            research_project_id=research_project_id,
            candidate_id=record_id,
            allowed_project_ids=projects(),
        )
        return dto.KnowledgeCandidateResponse.from_document(candidate)
    proposal = await services.get_skill_update_proposal_service(request).get_proposal(
        research_project_id=research_project_id,
        proposal_id=record_id,
        allowed_project_ids=projects(),
    )
    skill_allowed("research:read", proposal.skill_id)
    return dto.SkillUpdateProposalResponse.from_document(proposal)
