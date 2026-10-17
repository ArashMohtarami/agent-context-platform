"""Authorized views of the content-free V4 gateway activity trail."""

from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.security import CurrentPrincipal, authorize_project, authorize_scope
from app.models.agent import AgentActivityStatus
from app.models.common import Identifier
from app.schemas.agent import AgentActivityListResponse, AgentActivityResponse
from app.security.principal import Principal
from app.services.agent_activity_service import AgentActivityService
from app.services.audit_service import AuditService

router = APIRouter(prefix="/api/v4/agent-activity", tags=["agent-activity"])


async def _refuse_other_client(request: Request, principal: Principal) -> None:
    """Deny cross-client activity reads without revealing the requested client."""
    audit = cast(AuditService, request.app.state.audit)
    await audit.record(
        "authorize",
        "denied",
        principal=principal,
        scope="audit:self:read",
        method=request.method,
        path=request.url.path,
        reason="this key is not authorized for that client activity",
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="this key is not authorized for that client activity",
    )


@router.get("", response_model=AgentActivityListResponse)
async def list_activity(
    request: Request,
    principal: CurrentPrincipal,
    client_id: Identifier | None = None,
    session_id: Identifier | None = None,
    project_id: Identifier | None = None,
    tool: Identifier | None = None,
    event_status: Annotated[AgentActivityStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> AgentActivityListResponse:
    """List only the caller's own activity, unless it holds admin scope.

    Activity is metadata, not a bypass around V1 project isolation: unscoped
    reads are additionally bounded to global events and the caller's allowed
    projects.  The query contains no prompt, request payload, or credential.
    """
    is_admin = principal.allows_scope("agent:manage")
    effective_client_id = client_id
    if not is_admin:
        await authorize_scope(request, principal, "audit:self:read")
        if principal.client_id is None:
            await _refuse_other_client(request, principal)
        if client_id is not None and client_id != principal.client_id:
            await _refuse_other_client(request, principal)
        effective_client_id = principal.client_id
    if project_id is not None:
        await authorize_project(request, principal, project_id)

    service = cast(AgentActivityService, request.app.state.agent_activity_service)
    page = await service.list(
        client_id=effective_client_id,
        session_id=session_id,
        project_id=project_id,
        project_ids=None if project_id is not None else principal.project_selector(),
        tool=tool,
        status=event_status,
        limit=limit,
        cursor=cursor,
    )
    return AgentActivityListResponse(
        items=tuple(AgentActivityResponse.from_document(item) for item in page.items),
        next_cursor=page.next_cursor,
    )
