"""Thin session-only knowledge adapters. No bearer principal or internal HTTP hop."""

from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, Path, Query, Request

from app.api.dependencies import (
    get_context_service,
    get_memory_service,
    get_project_service,
    get_retrieval_service,
    get_skill_service,
)
from app.knowledge.boundary import KnowledgeRoute, action_gate, resource, shared_memory
from app.models.common import Identifier
from app.models.memory import MemoryScope
from app.models.project_context import ContextType
from app.models.skill import LifecycleStatus, SemanticVersion, SkillTrustLevel
from app.schemas.context import (
    ContextCreateRequest,
    ContextListResponse,
    ContextResponse,
    ContextUpdateRequest,
)
from app.schemas.memory import (
    MemoryCreateRequest,
    MemoryListResponse,
    MemoryResponse,
    MemoryUpdateRequest,
)
from app.schemas.project import ProjectCreateRequest, ProjectListResponse, ProjectResponse
from app.schemas.search import KnowledgeSearchRequest, KnowledgeSearchResponse
from app.schemas.skill import (
    SkillChunkResponse,
    SkillDetailResponse,
    SkillListResponse,
    SkillSummary,
    SkillVersionSummary,
)
from app.security.policy.evaluation import PolicyDenied
from app.services.context_service import ContextService
from app.services.memory_service import MemoryService
from app.services.project_service import ProjectService
from app.services.search_service import SearchService
from app.services.skill_service import SkillService

router = APIRouter(
    prefix="/api/v6/organizations/{org_id}/knowledge",
    tags=["human knowledge"],
    route_class=KnowledgeRoute,
)
RecordId = Annotated[str, Path(pattern=r"^[0-9a-f]{24}$")]
Limit = Annotated[int, Query(ge=1, le=50)]
Projects = Annotated[ProjectService, Depends(get_project_service)]
Contexts = Annotated[ContextService, Depends(get_context_service)]
Memories = Annotated[MemoryService, Depends(get_memory_service)]
Skills = Annotated[SkillService, Depends(get_skill_service)]


@router.get(
    "/projects",
    response_model=ProjectListResponse,
    dependencies=[Depends(action_gate("projects:read"))],
)
async def projects(
    service: Projects, limit: Limit = 20, cursor: str | None = None
) -> ProjectListResponse:
    page = await service.list_projects(limit=limit, cursor=cursor)
    return ProjectListResponse(
        items=tuple(ProjectResponse.from_document(d) for d in page.items),
        next_cursor=page.next_cursor,
    )


@router.get(
    "/projects/{project_id}",
    response_model=ProjectResponse,
    dependencies=[Depends(action_gate("projects:read"))],
)
async def project(project_id: Identifier, service: Projects) -> ProjectResponse:
    resource("projects:read", "project_directory", project_id)
    return ProjectResponse.from_document(await service.get_project(project_id))


@router.post(
    "/projects",
    response_model=ProjectResponse,
    status_code=201,
    dependencies=[Depends(action_gate("projects:write"))],
)
async def create_project(body: ProjectCreateRequest, service: Projects) -> ProjectResponse:
    resource("projects:write", "project_directory", body.project_id)
    return ProjectResponse.from_document(await service.create_project(**body.model_dump()))


@router.get(
    "/context",
    response_model=ContextListResponse,
    dependencies=[Depends(action_gate("context:read"))],
)
async def contexts(
    project_id: Identifier,
    service: Contexts,
    status: LifecycleStatus | None = "active",
    type: ContextType | None = None,
    limit: Limit = 20,
    cursor: str | None = None,
) -> ContextListResponse:
    resource("context:read", "project", project_id)
    page = await service.list_context(
        project_id, context_type=type, status=status, limit=limit, cursor=cursor
    )
    return ContextListResponse(
        project_id=project_id,
        items=tuple(ContextResponse.from_document(d) for d in page.items),
        next_cursor=page.next_cursor,
    )


@router.get(
    "/context/{context_id}",
    response_model=ContextResponse,
    dependencies=[Depends(action_gate("context:read"))],
)
async def context(
    context_id: RecordId, project_id: Identifier, service: Contexts
) -> ContextResponse:
    resource("context:read", "project", project_id)
    return ContextResponse.from_document(
        await service.get_context(project_id, ObjectId(context_id))
    )


@router.post(
    "/context",
    response_model=ContextResponse,
    status_code=201,
    dependencies=[Depends(action_gate("context:write"))],
)
async def create_context(body: ContextCreateRequest, service: Contexts) -> ContextResponse:
    resource("context:write", "project", body.project_id)
    values = body.model_dump(exclude={"type", "project_id"})
    return ContextResponse.from_document(
        await service.create_context(body.project_id, context_type=body.type, **values)
    )


@router.patch(
    "/context/{context_id}",
    response_model=ContextResponse,
    dependencies=[Depends(action_gate("context:write"))],
)
async def update_context(
    context_id: RecordId, project_id: Identifier, body: ContextUpdateRequest, service: Contexts
) -> ContextResponse:
    resource("context:write", "project", project_id)
    values = body.model_dump(exclude={"type"})
    return ContextResponse.from_document(
        await service.update_context(
            project_id, ObjectId(context_id), context_type=body.type, **values
        )
    )


@router.get(
    "/memories",
    response_model=MemoryListResponse,
    dependencies=[Depends(action_gate("memory:read"))],
)
async def memories(
    service: Memories,
    project_id: Identifier | None = None,
    scope: MemoryScope | None = None,
    status: LifecycleStatus | None = "active",
    type: Identifier | None = None,
    limit: Limit = 20,
    cursor: str | None = None,
) -> MemoryListResponse:
    resolved = shared_memory("memory:read", scope, project_id)
    page = await service.list_memories(
        project_id=project_id,
        scope=resolved,
        status=status,
        memory_type=type,
        limit=limit,
        cursor=cursor,
    )
    return MemoryListResponse(
        scope=resolved,
        project_id=project_id,
        client_id=None,
        items=tuple(MemoryResponse.from_document(d) for d in page.items),
        next_cursor=page.next_cursor,
    )


@router.get(
    "/memories/{memory_id}",
    response_model=MemoryResponse,
    dependencies=[Depends(action_gate("memory:read"))],
)
async def memory(
    memory_id: RecordId,
    service: Memories,
    project_id: Identifier | None = None,
    scope: MemoryScope | None = None,
) -> MemoryResponse:
    resolved = shared_memory("memory:read", scope, project_id)
    return MemoryResponse.from_document(
        await service.get_memory(ObjectId(memory_id), project_id=project_id, scope=resolved)
    )


@router.post(
    "/memories",
    response_model=MemoryResponse,
    status_code=201,
    dependencies=[Depends(action_gate("memory:write"))],
)
async def create_memory(body: MemoryCreateRequest, service: Memories) -> MemoryResponse:
    shared_memory("memory:write", body.scope, body.project_id)
    values = body.model_dump(exclude={"type", "supersedes"})
    return MemoryResponse.from_document(
        await service.create_memory(
            memory_type=body.type,
            supersedes=ObjectId(body.supersedes) if body.supersedes else None,
            **values,
        )
    )


@router.patch(
    "/memories/{memory_id}",
    response_model=MemoryResponse,
    dependencies=[Depends(action_gate("memory:write"))],
)
async def update_memory(
    memory_id: RecordId,
    body: MemoryUpdateRequest,
    service: Memories,
    project_id: Identifier | None = None,
    scope: MemoryScope | None = None,
) -> MemoryResponse:
    resolved = shared_memory("memory:write", scope, project_id)
    values = body.model_dump(exclude={"type"})
    return MemoryResponse.from_document(
        await service.update_memory(
            ObjectId(memory_id),
            project_id=project_id,
            scope=resolved,
            memory_type=body.type,
            **values,
        )
    )


@router.get(
    "/skills", response_model=SkillListResponse, dependencies=[Depends(action_gate("skills:read"))]
)
async def skills(
    service: Skills,
    status: LifecycleStatus | None = "active",
    category: Identifier | None = None,
    tag: Identifier | None = None,
    trust_level: SkillTrustLevel | None = None,
    limit: Limit = 20,
    cursor: str | None = None,
) -> SkillListResponse:
    page = await service.list_skills(
        status=status,
        category=category,
        tag=tag,
        trust_level=trust_level,
        limit=limit,
        cursor=cursor,
    )
    return SkillListResponse(
        items=tuple(SkillSummary.from_document(d) for d in page.items), next_cursor=page.next_cursor
    )


@router.get(
    "/skills/{skill_id}",
    response_model=SkillDetailResponse,
    dependencies=[Depends(action_gate("skills:read"))],
)
async def skill(
    skill_id: Identifier,
    service: Skills,
    version: SemanticVersion | None = None,
    chunk_limit: Limit = 20,
    chunk_cursor: str | None = None,
) -> SkillDetailResponse:
    resource("skills:read", "skill", skill_id)
    detail = await service.get_skill(
        skill_id, version=version, chunk_limit=chunk_limit, chunk_cursor=chunk_cursor
    )
    return SkillDetailResponse(
        skill=SkillSummary.from_document(detail.skill),
        versions=tuple(SkillVersionSummary.from_document(d) for d in detail.versions),
        chunks=tuple(SkillChunkResponse.from_document(d) for d in detail.chunks.items),
        next_chunk_cursor=detail.chunks.next_cursor,
    )


@router.post(
    "/search",
    response_model=KnowledgeSearchResponse,
    dependencies=[Depends(action_gate("search:read"))],
)
async def search(body: KnowledgeSearchRequest, request: Request) -> KnowledgeSearchResponse:
    if body.project_id is not None:
        resource("search:read", "project", body.project_id)
    if body.skill_id is not None:
        resource("search:read", "skill", body.skill_id)
    # SearchService's machine factory depends on a bearer principal and may log
    # query text. Reuse its retrieval pipeline directly with no query logger.
    # Repository/mongot filters intersect search:read with each source's read
    # action BEFORE selection; the returned rows are defensively validated too.
    if body.memory_scopes and any(s in {"agent", "project_agent"} for s in body.memory_scopes):
        raise PolicyDenied("private memory requires its verified machine identity")
    result = await SearchService(get_retrieval_service(request)).search_knowledge(
        **body.model_dump(exclude={"explain"})
    )
    return KnowledgeSearchResponse.from_result(result, explain=body.explain)
