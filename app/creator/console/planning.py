"""Bounded discovery, historical overlap, repurposing and evidence planning."""

from fastapi import APIRouter, Request

from app.api import dependencies as services
from app.api.routes.research_console import gate
from app.creator.console.boundary import CreatorRoute, profile, project_selector
from app.creator.console.contracts import Brief
from app.creator.console.service import CreatorConsole
from app.db.content_indexes import ContentQueryFilters
from app.identity.browser import CurrentBrowser
from app.models.common import Identifier
from app.models.content import ContentBriefDocument, ContentIdeaDocument, ContentItemDocument
from app.schemas import content as dto

router = APIRouter(route_class=CreatorRoute)


@router.post(
    "/ideas",
    response_model=dto.ContentIdeaResponse,
    status_code=201,
    dependencies=gate("content:write"),
)
async def idea(
    body: dto.ContentIdeaCreateRequest, request: Request, context: CurrentBrowser
) -> dto.ContentIdeaResponse:
    c = CreatorConsole(request, context)
    profile(body.profile_id)
    if body.origin.project_id:
        c.projects((body.origin.project_id,))
    row = await c.content.create_idea(**{k: getattr(body, k) for k in type(body).model_fields})
    return dto.ContentIdeaResponse.from_document(row)


@router.get(
    "/profiles/{profile_id}/ideas/{idea_id}",
    response_model=dto.ContentIdeaResponse,
    dependencies=gate("content:read"),
)
async def get_idea(
    profile_id: Identifier, idea_id: Identifier, request: Request, context: CurrentBrowser
) -> dto.ContentIdeaResponse:
    profile(profile_id)
    return dto.ContentIdeaResponse.from_document(
        await CreatorConsole(request, context).content.get_idea(profile_id, idea_id)
    )


@router.patch(
    "/ideas/{idea_id}", response_model=dto.ContentIdeaResponse, dependencies=gate("content:write")
)
async def update_idea(
    idea_id: Identifier,
    body: dto.ContentIdeaUpdateRequest,
    request: Request,
    context: CurrentBrowser,
) -> dto.ContentIdeaResponse:
    c = CreatorConsole(request, context)
    profile(body.profile_id)
    if body.origin and body.origin.project_id:
        c.projects((body.origin.project_id,))
    row = await c.content.update_idea(
        body.profile_id,
        idea_id,
        expected_revision=body.expected_revision,
        changes=body.model_dump(exclude={"profile_id", "expected_revision"}, exclude_unset=True),
    )
    return dto.ContentIdeaResponse.from_document(row)


@router.post(
    "/briefs",
    response_model=dto.ContentBriefResponse,
    status_code=201,
    dependencies=gate("content:write"),
)
async def brief(body: Brief, request: Request, context: CurrentBrowser) -> dto.ContentBriefResponse:
    c = CreatorConsole(request, context)
    profile(body.profile_id)
    c.source_refs(body.source_refs)
    claims, citations = [], []
    if body.claim_ids or body.citations:
        if not body.research_project_id:
            raise ValueError("research selections require a project")
    if body.research_project_id:
        await c.research(body.research_project_id)
        from app.repositories.research_repository import (
            MongoResearchCitationRepository,
            MongoResearchClaimRepository,
        )
        from app.services.content_research_service import _citation_reference, _claim_reference

        for identifier in body.claim_ids:
            claim = await MongoResearchClaimRepository(c.db, body.research_project_id).get(
                identifier
            )
            if claim is None:
                raise ValueError("research claim is unavailable")
            claims.append(_claim_reference(claim))
        for key in body.citations:
            if key.claim_id not in body.claim_ids:
                raise ValueError("citation must belong to a selected claim")
            citation = await MongoResearchCitationRepository(c.db, body.research_project_id).get(
                key.claim_id, key.citation_id
            )
            if citation is None:
                raise ValueError("research citation is unavailable")
            citations.append(_citation_reference(citation))
    row = await c.content.create_brief(
        **{
            k: getattr(body, k)
            for k in type(body).model_fields
            if k not in {"claim_ids", "citations"}
        },
        claim_refs=claims,
        citation_refs=citations,
        avoid_repetition_with=(),
        tags=(),
        allowed_project_ids=project_selector(),
    )
    return dto.ContentBriefResponse.from_document(row)


@router.get(
    "/profiles/{profile_id}/briefs/{brief_id}",
    response_model=dto.ContentBriefResponse,
    dependencies=gate("content:read"),
)
async def get_brief(
    profile_id: Identifier, brief_id: Identifier, request: Request, context: CurrentBrowser
) -> dto.ContentBriefResponse:
    profile(profile_id)
    return dto.ContentBriefResponse.from_document(
        await CreatorConsole(request, context).content.get_brief(profile_id, brief_id)
    )


@router.post(
    "/discover", response_model=dto.ContentIdeaDiscoveryResponse, dependencies=gate("content:read")
)
async def discover(
    body: dto.ContentIdeaDiscoveryRequest, request: Request, context: CurrentBrowser
) -> dto.ContentIdeaDiscoveryResponse:
    c = CreatorConsole(request, context)
    profile(body.profile_id)
    if body.project_id:
        c.projects((body.project_id,))
    result = await services.get_content_discovery_service(request).discover_content_ideas(
        **{k: getattr(body, k) for k in type(body).model_fields}
    )
    return dto.ContentIdeaDiscoveryResponse.from_value(result)


@router.post(
    "/prepare-brief",
    response_model=dto.ContentBriefPreparationResponse,
    dependencies=gate("content:read"),
)
async def prepare_brief(
    body: dto.ContentBriefPrepareRequest, request: Request, context: CurrentBrowser
) -> dto.ContentBriefPreparationResponse:
    c = CreatorConsole(request, context)
    profile(body.profile_id)
    if body.project_id:
        c.projects((body.project_id,))
    await c.content.get_idea(body.profile_id, body.idea_id)  # live transitive source envelope
    result = await services.get_content_discovery_service(request).prepare_content_brief(
        **body.model_dump()
    )
    return dto.ContentBriefPreparationResponse.from_value(result)


@router.post(
    "/novelty", response_model=dto.ContentNoveltyResponse, dependencies=gate("content:read")
)
async def novelty(
    body: dto.ContentNoveltyRequest, request: Request, context: CurrentBrowser
) -> dto.ContentNoveltyResponse:
    c = CreatorConsole(request, context)
    profile(body.profile_id)
    if body.project_id:
        c.projects((body.project_id,))
    result = await services.get_content_novelty_service(request).check_content_novelty(
        **body.model_dump(), allowed_project_ids=c.allowed_projects
    )
    return dto.ContentNoveltyResponse.from_value(result)


@router.post("/search", response_model=dto.ContentSearchResponse, dependencies=gate("content:read"))
async def search(
    body: dto.ContentSearchRequest, request: Request, context: CurrentBrowser
) -> dto.ContentSearchResponse:
    c = CreatorConsole(request, context)
    profile(body.profile_id)
    if body.project_id:
        c.projects((body.project_id,))
    result = await services.get_content_retrieval_service(request).search(
        profile_id=body.profile_id,
        query=body.query,
        filters=ContentQueryFilters(
            profile_id=body.profile_id,
            platform=body.platform,
            project_id=body.project_id,
            status=body.status,
            pillar_id=body.pillar_id,
            tags=tuple(body.tags),
        ),
        retrieval_profile=body.retrieval_profile,
        method=body.method,
        kinds=tuple(body.kinds),
        limit=body.limit,
        token_budget=body.token_budget,
    )
    allowed = c.allowed_projects
    visible = tuple(
        x for x in result.items if allowed is None or set(x.private_project_ids) <= allowed
    )
    # Scoped repositories prefilter every candidate before ranking. This repeats
    # the domain's defensive check, rather than implementing browser-side hiding.
    return dto.ContentSearchResponse.from_result(result, visible)


@router.post(
    "/repurpose/{record_id}",
    response_model=dto.ContentRepurposeResponse,
    dependencies=gate("content:read"),
)
async def repurpose(
    record_id: Identifier,
    body: dto.ContentRepurposeRequest,
    request: Request,
    context: CurrentBrowser,
) -> dto.ContentRepurposeResponse:
    c = CreatorConsole(request, context)
    profile(body.profile_id)
    source: ContentIdeaDocument | ContentBriefDocument | ContentItemDocument
    if body.source_kind == "idea":
        source = await c.content.get_idea(body.profile_id, record_id)
    elif body.source_kind == "brief":
        source = await c.content.get_brief(body.profile_id, record_id)
    else:
        source = await c.item(body.profile_id, record_id)
    result = await services.get_content_repurpose_service(request).prepare_repurpose_plan(
        source=source,
        targets=body.targets,
        method=body.method,
        limit=body.limit,
        token_budget=body.token_budget,
        allowed_project_ids=c.allowed_projects,
    )
    return dto.ContentRepurposeResponse.from_value(result)


@router.post(
    "/items/{content_id}/facts",
    response_model=dto.ContentFactReviewResponse,
    dependencies=gate("content:read"),
)
async def facts(
    content_id: Identifier,
    body: dto.ContentFactReviewRequest,
    request: Request,
    context: CurrentBrowser,
) -> dto.ContentFactReviewResponse:
    c = CreatorConsole(request, context)
    item = await c.item(body.profile_id, content_id)
    brief = await c.content.get_brief(body.profile_id, item.brief_id) if item.brief_id else None
    from_brief = brief.research_project_id if brief else None
    if body.research_project_id and from_brief and body.research_project_id != from_brief:
        raise ValueError("research project does not match brief")
    rid = body.research_project_id or from_brief
    if not rid:
        raise ValueError("a research project is required")
    await c.research(rid)
    result = await services.get_content_research_service(request).review_content_facts(
        content=item,
        research_project_id=rid,
        claim_ids=body.claim_ids,
        claim_statements=body.claim_statements,
        allowed_project_ids=project_selector(),
    )
    return dto.ContentFactReviewResponse.from_value(result, profile_id=body.profile_id)
