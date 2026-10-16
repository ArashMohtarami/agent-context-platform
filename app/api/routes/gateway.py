"""Internal API contracts used by the client-neutral MCP gateway."""

from typing import Annotated, cast

from fastapi import APIRouter, Header, HTTPException, Request, status

from app import provenance
from app.api.concurrency import RevisionConflict
from app.api.gateway_context import retrieval_metadata
from app.api.security import authorize_project, authorize_scope, credential
from app.models.agent import AgentClientType
from app.repositories.errors import ConcurrencyConflictError
from app.schemas.agent import AgentSessionResponse
from app.schemas.gateway import (
    GatewayActivityRequest,
    GatewayIdempotencyAcquireRequest,
    GatewayIdempotencyAcquireResponse,
    GatewayIdempotencyCompleteRequest,
    GatewayIdempotencyResult,
    GatewayIdentityResponse,
    GatewaySessionBeginRequest,
    GatewaySessionEndRequest,
    GatewaySessionUpdateRequest,
)
from app.security.principal import AuthenticationError, Principal
from app.security.scopes import Scope
from app.security.tenant_policy import bind_principal, select_action
from app.services.agent_activity_service import AgentActivityService
from app.services.agent_session_service import (
    AgentSessionAccessError,
    AgentSessionNotFoundError,
    AgentSessionService,
    AgentSessionStateError,
)
from app.services.audit_service import AuditService
from app.services.auth_service import (
    AuthService,
    GatewayAuthenticationCode,
    GatewayAuthenticationError,
)
from app.services.gateway_idempotency_service import (
    GatewayIdempotencyService,
    IdempotencyConflictError,
    IdempotencyInProgressError,
)

router = APIRouter(prefix="/api/v4/gateway", tags=["mcp-gateway"])


async def _gateway_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Use the existing authenticator while preserving public API error policy."""
    auth: AuthService | None = request.app.state.auth
    if auth is None:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHENTICATED"})
    else:
        try:
            principal = await auth.authenticate_gateway(credential(authorization))
            bind_principal(principal)
        except GatewayAuthenticationError as error:
            await _record_refusal(request, error.code.value)
            raise HTTPException(
                status_code=(
                    status.HTTP_403_FORBIDDEN
                    if error.code == GatewayAuthenticationCode.CLIENT_DISABLED
                    else status.HTTP_401_UNAUTHORIZED
                ),
                detail={"code": error.code.value},
            ) from error
        except AuthenticationError as error:
            await _record_refusal(request, "UNAUTHENTICATED")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "UNAUTHENTICATED"},
            ) from error
    if "agent:self" in principal.scopes:
        select_action("agent:self")
    request.state.principal = principal
    provenance.set_principal(principal)
    return principal


async def _record_refusal(request: Request, reason: str) -> None:
    """Reuse the existing audit trail for a denied identity preflight."""
    audit = getattr(request.app.state, "audit", None)
    if not isinstance(audit, AuditService):
        return
    request_id = request.headers.get("x-request-id")
    await audit.record(
        "authenticate",
        "denied",
        method=request.method,
        path=request.url.path,
        request_id=request_id,
        reason=reason,
    )


async def _authorize_operation(request: Request, principal: Principal, operation: str) -> None:
    from app.mcp.tools import TOOLS_BY_NAME

    definition = TOOLS_BY_NAME.get(operation)
    if definition is None or not definition.writes:
        raise HTTPException(status_code=403, detail="operation is unavailable")
    await authorize_scope(request, principal, definition.scope)


def _idempotency_service(request: Request) -> GatewayIdempotencyService:
    """Return the Phase 10 retry service, failing closed if startup was incomplete."""
    service = getattr(request.app.state, "gateway_idempotency_service", None)
    if not isinstance(service, GatewayIdempotencyService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="gateway idempotency is unavailable",
        )
    return service


def _gateway_client_id(principal: Principal) -> str:
    """Require the credential-derived client identity needed to isolate retry keys."""
    if principal.client_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN"},
        )
    return principal.client_id


def _idempotency_http_error(error: Exception) -> HTTPException:
    """Translate retry state without exposing a key, hash, or request body."""
    if isinstance(error, IdempotencyConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "IDEMPOTENCY_CONFLICT",
                "message": "the idempotency key was already used for a different request",
            },
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "OPERATION_TIMEOUT",
            "message": "a prior request with this idempotency key is still being processed",
        },
    )


@router.get("/identity", response_model=GatewayIdentityResponse)
async def identity(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    session_id: Annotated[str | None, Header(alias="X-Agent-Context-Session-ID")] = None,
) -> GatewayIdentityResponse:
    """Return only identity and authorization facts; never a credential or hash."""
    principal = await _gateway_principal(request, authorization)
    if session_id is not None:
        try:
            await _session_service(request).require_active_for_gateway(principal, session_id)
        except (
            AgentSessionAccessError,
            AgentSessionNotFoundError,
            AgentSessionStateError,
        ) as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN"},
            ) from error
    return GatewayIdentityResponse(
        org_id=cast(str, principal.org_id),
        key_id=principal.key_id,
        allowed_profile_ids=principal.profile_selector(),
        client_id=principal.client_id,
        client_type=(
            AgentClientType(principal.client_type) if principal.client_type is not None else None
        ),
        scopes=principal.scopes,
        allowed_project_ids=principal.project_selector(),
    )


@router.post("/idempotency/reservations", response_model=GatewayIdempotencyAcquireResponse)
async def acquire_idempotency_reservation(
    body: GatewayIdempotencyAcquireRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> GatewayIdempotencyAcquireResponse:
    """Reserve a retry-safe write or return its prior completed MCP result."""
    principal = await _gateway_principal(request, authorization)
    await _authorize_operation(request, principal, body.operation)
    try:
        reservation = await _idempotency_service(request).acquire(
            client_id=_gateway_client_id(principal),
            operation=body.operation,
            idempotency_key=body.idempotency_key,
            payload_hash=body.payload_hash,
        )
    except (IdempotencyConflictError, IdempotencyInProgressError) as error:
        raise _idempotency_http_error(error) from error
    if not reservation.replay:
        return GatewayIdempotencyAcquireResponse(decision="proceed")
    record = reservation.document
    # The model requires both fields for a completed record.
    if record.result_text is None or record.result_data is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="gateway idempotency result is unavailable",
        )
    await _revalidate_job_replay(request, body.operation, record.result_data)
    return GatewayIdempotencyAcquireResponse(
        decision="replay",
        result=GatewayIdempotencyResult(text=record.result_text, data=record.result_data),
    )


async def _revalidate_job_replay(request: Request, operation: str, data: dict[str, object]) -> None:
    if operation != "ingest_research_source":
        return
    from app.api.dependencies import get_research_ingestion_service
    from app.services.research_ingestion_service import ResearchIngestionJobNotFoundError

    ingestion = data.get("research_ingestion")
    job_data = ingestion.get("job") if isinstance(ingestion, dict) else None
    if (
        not isinstance(job_data, dict)
        or not isinstance(job_data.get("research_project_id"), str)
        or not isinstance(job_data.get("job_id"), str)
    ):
        raise HTTPException(status_code=403, detail="cached job is unavailable")
    try:
        job = await get_research_ingestion_service(request).get_job(
            research_project_id=job_data["research_project_id"], job_id=job_data["job_id"]
        )
        if not isinstance(ingestion, dict) or job.source_id != ingestion.get("source_id"):
            raise ResearchIngestionJobNotFoundError("cached job is unavailable")
    except ResearchIngestionJobNotFoundError as error:
        raise HTTPException(status_code=403, detail="cached job is unavailable") from error


@router.post("/idempotency/reservations/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_idempotency_reservation(
    body: GatewayIdempotencyCompleteRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Persist a replay-safe result after the reserved MCP write succeeded."""
    principal = await _gateway_principal(request, authorization)
    await _authorize_operation(request, principal, body.operation)
    try:
        await _idempotency_service(request).complete_request(
            client_id=_gateway_client_id(principal),
            operation=body.operation,
            idempotency_key=body.idempotency_key,
            payload_hash=body.payload_hash,
            result_text=body.result.text,
            result_data=body.result.data,
        )
    except (IdempotencyConflictError, IdempotencyInProgressError) as error:
        raise _idempotency_http_error(error) from error


@router.post("/idempotency/reservations/abandon", status_code=status.HTTP_204_NO_CONTENT)
async def abandon_idempotency_reservation(
    body: GatewayIdempotencyAcquireRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Release a deterministically failed reservation without storing caller data."""
    principal = await _gateway_principal(request, authorization)
    await _idempotency_service(request).abandon_request(
        client_id=_gateway_client_id(principal),
        operation=body.operation,
        idempotency_key=body.idempotency_key,
        payload_hash=body.payload_hash,
    )


@router.post("/activity", status_code=status.HTTP_204_NO_CONTENT)
async def activity(
    body: GatewayActivityRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Persist MCP operation metadata attributed from the bearer credential."""
    principal = await _gateway_principal(request, authorization)
    metadata = retrieval_metadata(request, principal)
    # Session metadata is gateway-provided only when the client headers agree
    # with the authenticated credential.  The caller cannot select another
    # client, even by sending a plausible-looking provenance header.
    session_id = (
        metadata.session_id
        if metadata.client_id == principal.client_id
        and metadata.client_type is not None
        and metadata.client_type.value == principal.client_type
        else None
    )
    if session_id is not None:
        try:
            # The end tool's event is emitted after the state transition, so
            # retain its correlation once without reopening the session. Every
            # other tool/activity requires a live session.
            if body.operation == "end_agent_session" and body.tool == "end_agent_session":
                await _session_service(request).get(principal, session_id, permit_admin=False)
            else:
                await _session_service(request).require_active_for_gateway(principal, session_id)
        except (
            AgentSessionAccessError,
            AgentSessionNotFoundError,
            AgentSessionStateError,
        ) as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN"},
            ) from error
    service = cast(AgentActivityService, request.app.state.agent_activity_service)
    await service.record(
        principal,
        operation=body.operation,
        tool=body.tool,
        project_id=body.project_id,
        status=body.status,
        latency_ms=body.latency_ms,
        session_id=session_id,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
    )


async def _require_session_scope(request: Request, principal: Principal, scope: Scope) -> None:
    """Allow a client self-scope or the existing elevated agent-admin scope."""
    if principal.allows_scope("agent:manage"):
        return
    await authorize_scope(request, principal, scope)


def _session_service(request: Request) -> AgentSessionService:
    """Return the service wired by application startup, failing closed if absent."""
    service = getattr(request.app.state, "agent_session_service", None)
    if not isinstance(service, AgentSessionService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="agent sessions are unavailable",
        )
    return service


@router.post("/sessions", response_model=AgentSessionResponse, status_code=status.HTTP_201_CREATED)
async def begin_session(
    body: GatewaySessionBeginRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentSessionResponse:
    """Create a small session bound to the authenticated agent credential."""
    principal = await _gateway_principal(request, authorization)
    await _require_session_scope(request, principal, "session:write")
    if body.project_id is not None:
        await authorize_project(request, principal, body.project_id)
    try:
        session = await _session_service(request).begin(
            principal,
            project_id=body.project_id,
            repository=body.repository,
            metadata=body.metadata,
        )
    except AgentSessionAccessError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    return AgentSessionResponse.from_document(session)


@router.get("/sessions/{session_id}", response_model=AgentSessionResponse)
async def get_session(
    session_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentSessionResponse:
    """Read the caller's own session, or an admin-authorized one."""
    principal = await _gateway_principal(request, authorization)
    await _require_session_scope(request, principal, "agent:self")
    try:
        session = await _session_service(request).get(principal, session_id)
    except AgentSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="agent session was not found",
        ) from error
    except AgentSessionAccessError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    return AgentSessionResponse.from_document(session)


@router.patch("/sessions/{session_id}", response_model=AgentSessionResponse)
async def update_session(
    session_id: str,
    body: GatewaySessionUpdateRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentSessionResponse:
    """Update an active session only at its caller-reviewed revision."""
    principal = await _gateway_principal(request, authorization)
    await _require_session_scope(request, principal, "session:write")
    changes = body.model_dump(exclude={"expected_revision"}, exclude_unset=True)
    project_id = changes.get("project_id")
    if isinstance(project_id, str):
        await authorize_project(request, principal, project_id)
    try:
        session = await _session_service(request).update(
            principal,
            session_id,
            expected_revision=body.expected_revision,
            changes=changes,
        )
    except AgentSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="agent session was not found",
        ) from error
    except AgentSessionAccessError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except AgentSessionStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    return AgentSessionResponse.from_document(session)


@router.post("/sessions/{session_id}/end", response_model=AgentSessionResponse)
async def end_session(
    session_id: str,
    body: GatewaySessionEndRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentSessionResponse:
    """Close an active session at a caller-reviewed revision."""
    principal = await _gateway_principal(request, authorization)
    await _require_session_scope(request, principal, "session:write")
    try:
        session = await _session_service(request).end(
            principal,
            session_id,
            expected_revision=body.expected_revision,
        )
    except AgentSessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="agent session was not found",
        ) from error
    except AgentSessionAccessError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except AgentSessionStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    return AgentSessionResponse.from_document(session)
