"""Strongly scoped administrative V4 Phase 1 agent-client endpoints."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.concurrency import RevisionConflict
from app.api.security import requires
from app.models.common import Identifier
from app.repositories.errors import ConcurrencyConflictError
from app.schemas.agent import (
    AgentClientCreateRequest,
    AgentClientListResponse,
    AgentClientResponse,
    AgentClientUpdateRequest,
    AgentCredentialCreateRequest,
    AgentCredentialResponse,
)
from app.services.agent_client_service import (
    AgentClientExistsError,
    AgentClientNotFoundError,
    AgentClientService,
)

router = APIRouter(
    prefix="/api/v4/clients",
    tags=["agent-clients"],
    dependencies=[Depends(requires("agent:manage"))],
)


async def service(request: Request) -> AgentClientService:
    return cast(AgentClientService, request.app.state.agent_client_service)


Service = Annotated[AgentClientService, Depends(service)]


@router.post("", response_model=AgentClientResponse, status_code=status.HTTP_201_CREATED)
async def create(body: AgentClientCreateRequest, clients: Service) -> AgentClientResponse:
    try:
        item = await clients.create_client(**body.model_dump())
    except AgentClientExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return AgentClientResponse.from_document(item)


@router.get("", response_model=AgentClientListResponse)
async def list_clients(
    clients: Service, limit: Annotated[int, Query(ge=1, le=50)] = 20, cursor: str | None = None
) -> AgentClientListResponse:
    page = await clients.list_clients(limit=limit, cursor=cursor)
    return AgentClientListResponse(
        items=tuple(AgentClientResponse.from_document(x) for x in page.items),
        next_cursor=page.next_cursor,
    )


@router.get("/{client_id}", response_model=AgentClientResponse)
async def get(client_id: Identifier, clients: Service) -> AgentClientResponse:
    try:
        return AgentClientResponse.from_document(await clients.get_client(client_id))
    except AgentClientNotFoundError as error:
        raise HTTPException(status_code=404, detail="agent client was not found") from error


@router.patch("/{client_id}", response_model=AgentClientResponse)
async def update(
    client_id: Identifier, body: AgentClientUpdateRequest, clients: Service
) -> AgentClientResponse:
    try:
        item = await clients.update_client(client_id, **body.model_dump())
    except AgentClientNotFoundError as error:
        raise HTTPException(status_code=404, detail="agent client was not found") from error
    except ConcurrencyConflictError as error:
        raise RevisionConflict() from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return AgentClientResponse.from_document(item)


@router.post(
    "/{client_id}/credentials",
    response_model=AgentCredentialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def credential(
    client_id: Identifier, body: AgentCredentialCreateRequest, clients: Service
) -> AgentCredentialResponse:
    try:
        issued = await clients.create_credential(
            client_id,
            scopes=body.scopes,
            projects=body.project_ids,
            expires_in_days=body.expires_in_days,
        )
    except AgentClientNotFoundError as error:
        raise HTTPException(status_code=404, detail="agent client was not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    key = issued.record
    return AgentCredentialResponse(
        key_id=key.key_id,
        client_id=client_id,
        status=key.status,
        scopes=tuple(key.scopes),
        project_ids=tuple(key.projects),
        expires_at=key.expires_at,
        token=issued.token,
    )


@router.post("/{client_id}/revoke", response_model=AgentClientResponse)
async def revoke(client_id: Identifier, clients: Service) -> AgentClientResponse:
    try:
        return AgentClientResponse.from_document(await clients.revoke_client(client_id))
    except AgentClientNotFoundError as error:
        raise HTTPException(status_code=404, detail="agent client was not found") from error
