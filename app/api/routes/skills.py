"""REST endpoints for skill metadata, versions, chunks, and search."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import get_retrieval_service, get_skill_service
from app.api.security import authorize_project, requires
from app.db.errors import SearchUnavailableError
from app.embeddings.errors import EmbeddingError
from app.models.common import Identifier
from app.models.skill import LifecycleStatus, SemanticVersion, SkillTrustLevel
from app.schemas.search import (
    SkillChunkSearchResultResponse,
    SkillHybridSearchRequest,
    SkillHybridSearchResponse,
    SkillLexicalSearchRequest,
    SkillLexicalSearchResponse,
    SkillVectorSearchRequest,
    SkillVectorSearchResponse,
)
from app.schemas.skill import (
    SkillChunkResponse,
    SkillDetailResponse,
    SkillListResponse,
    SkillSearchRequest,
    SkillSearchResponse,
    SkillSearchResult,
    SkillSummary,
    SkillVersionSummary,
)
from app.security.principal import Principal
from app.services.embedding_service import EmbeddingService
from app.services.retrieval_service import RetrievalService
from app.services.skill_service import SkillService
from app.skills.errors import SkillNotFoundError

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])

_SEARCH_UNAVAILABLE = "semantic search is unavailable"


SkillServiceDependency = Annotated[SkillService, Depends(get_skill_service)]
RetrievalServiceDependency = Annotated[RetrievalService, Depends(get_retrieval_service)]
# Skills are global knowledge, so one scope covers every route here.
SkillReader = Annotated[Principal, Depends(requires("skills:read"))]


@router.get(
    "",
    response_model=SkillListResponse,
    dependencies=[Depends(requires("skills:read"))],
)
async def list_skills(
    service: SkillServiceDependency,
    status_filter: Annotated[LifecycleStatus | None, Query(alias="status")] = "active",
    category: Identifier | None = None,
    tag: Identifier | None = None,
    trust_level: SkillTrustLevel | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> SkillListResponse:
    """List active skills by default using bounded cursor pagination."""
    page = await service.list_skills(
        status=status_filter,
        category=category,
        tag=tag,
        trust_level=trust_level,
        limit=limit,
        cursor=cursor,
    )
    return SkillListResponse(
        items=tuple(SkillSummary.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{skill_id}",
    response_model=SkillDetailResponse,
    dependencies=[Depends(requires("skills:read"))],
    responses={status.HTTP_404_NOT_FOUND: {"description": "Skill or version not found"}},
)
async def get_skill(
    skill_id: Identifier,
    service: SkillServiceDependency,
    version: SemanticVersion | None = None,
    chunk_limit: Annotated[int, Query(ge=1, le=50)] = 20,
    chunk_cursor: str | None = None,
) -> SkillDetailResponse:
    """Load the active or requested version with history and paginated chunks."""
    try:
        details = await service.get_skill(
            skill_id,
            version=version,
            chunk_limit=chunk_limit,
            chunk_cursor=chunk_cursor,
        )
    except SkillNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="skill or requested version was not found",
        ) from error
    return SkillDetailResponse(
        skill=SkillSummary.from_document(details.skill),
        versions=tuple(SkillVersionSummary.from_document(item) for item in details.versions),
        chunks=tuple(SkillChunkResponse.from_document(item) for item in details.chunks.items),
        next_chunk_cursor=details.chunks.next_cursor,
    )


@router.post(
    "/search",
    response_model=SkillSearchResponse,
    dependencies=[Depends(requires("skills:read"))],
)
async def search_skills(
    request: SkillSearchRequest,
    service: SkillServiceDependency,
) -> SkillSearchResponse:
    """Search active skill metadata and chunks with escaped literal terms."""
    matches = await service.search_skills(
        request.query,
        category=request.category,
        tags=request.tags,
        trust_level=request.trust_level,
        limit=request.limit,
    )
    return SkillSearchResponse(
        results=tuple(
            SkillSearchResult(
                skill=SkillSummary.from_document(match.skill),
                matched_chunks=tuple(
                    SkillChunkResponse.from_document(chunk) for chunk in match.matched_chunks
                ),
            )
            for match in matches
        )
    )


@router.post(
    "/search/vector",
    dependencies=[Depends(requires("skills:read"))],
    response_model=SkillVectorSearchResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Semantic search is unavailable"}
    },
)
async def search_skills_by_vector(
    request: SkillVectorSearchRequest,
    service: RetrievalServiceDependency,
    http_request: Request,
) -> SkillVectorSearchResponse:
    """Rank skill chunks by embedding similarity with metadata filters."""
    embeddings: EmbeddingService | None = http_request.app.state.embeddings
    if embeddings is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_SEARCH_UNAVAILABLE,
        )
    try:
        results = await service.vector_search(
            request.query,
            limit=request.limit,
            skill_id=request.skill_id,
            skill_version=request.skill_version,
            category=request.category,
            tags=request.tags,
            trust_level=request.trust_level,
            active_only=request.active_only,
        )
    except (SearchUnavailableError, EmbeddingError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_SEARCH_UNAVAILABLE,
        ) from error
    return SkillVectorSearchResponse(
        query=request.query,
        embedding_model=embeddings.model,
        embedding_dimensions=embeddings.dimensions,
        results=tuple(SkillChunkSearchResultResponse.from_result(item) for item in results),
    )


@router.post(
    "/search/lexical",
    dependencies=[Depends(requires("skills:read"))],
    response_model=SkillLexicalSearchResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Lexical search is unavailable"}
    },
)
async def search_skills_by_text(
    request: SkillLexicalSearchRequest,
    service: RetrievalServiceDependency,
) -> SkillLexicalSearchResponse:
    """Rank skill chunks by lexical relevance, preserving exact identifiers."""
    try:
        results = await service.lexical_search(
            request.query,
            limit=request.limit,
            phrase=request.phrase,
            skill_id=request.skill_id,
            skill_version=request.skill_version,
            category=request.category,
            tags=request.tags,
            trust_level=request.trust_level,
            active_only=request.active_only,
        )
    except SearchUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_SEARCH_UNAVAILABLE,
        ) from error
    return SkillLexicalSearchResponse(
        query=request.query,
        phrase=request.phrase,
        results=tuple(SkillChunkSearchResultResponse.from_result(item) for item in results),
    )


@router.post(
    "/search/hybrid",
    response_model=SkillHybridSearchResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Hybrid search is unavailable"}
    },
)
async def search_skills_hybrid(
    request: SkillHybridSearchRequest,
    service: RetrievalServiceDependency,
    principal: SkillReader,
    http_request: Request,
) -> SkillHybridSearchResponse:
    """Combine vector and lexical retrieval into one budgeted context."""
    await authorize_project(http_request, principal, request.project_id)
    try:
        context = await service.hybrid_search(
            request.query,
            limit=request.limit,
            token_budget=request.token_budget,
            project_id=request.project_id,
            skill_id=request.skill_id,
            skill_version=request.skill_version,
            category=request.category,
            tags=request.tags,
            trust_level=request.trust_level,
            active_only=request.active_only,
            minimum_importance=request.minimum_importance,
        )
    except (SearchUnavailableError, EmbeddingError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_SEARCH_UNAVAILABLE,
        ) from error
    return SkillHybridSearchResponse.from_context(context, explain=request.explain)
