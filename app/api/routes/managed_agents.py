"""Session-only machine management; no normal MCP tool reaches this router."""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes.human_management import management
from app.identity.browser import CurrentBrowser
from app.managed_agents.models import (
    ClientApproval,
    CredentialIssue,
    ReviewerApproval,
    Revision,
    ServiceCreate,
    TemplatePut,
)
from app.managed_agents.service import ManagedAgents
from app.security.policy.models import Grant

router = APIRouter(prefix="/api/v6/organizations/{org_id}", tags=["managed agents"])


class DTO(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ManagedClientDTO(DTO):
    org_id: str
    client_id: str
    subject_kind: Literal["human", "service_account"]
    subject_id: str
    sponsor_id: str
    team_id: str | None
    parent_id: str
    parent_generation: int
    issuer_id: str
    tuples: list[Grant]
    scopes: list[str]
    profile_id: str | None
    profile_revision: int | None
    revision: int
    status: str
    expires_at: datetime


class ClientPage(DTO):
    items: list[ManagedClientDTO]
    truncated: bool
    policy_revision: int


class CredentialDTO(DTO):
    org_id: str
    client_id: str
    key_id: str
    recipient_id: str
    issuance_id: str
    status: str
    revision: int
    expires_at: datetime
    replaces_key_id: str | None


class DeliveryDTO(CredentialDTO):
    delivered: bool
    token: str | None = Field(default=None, repr=False)


class PreviewDTO(DTO):
    client_id: str
    effective_tuples: list[Grant]
    copied_scopes: list[str]
    profile_revision: int | None
    rate_limit_profile_enforced: Literal[False]
    policy_revision: int


class InspectionDTO(DTO):
    client: ManagedClientDTO
    credentials: list[CredentialDTO]
    effective_tuples: list[Grant]
    rate_limit_profile_enforced: Literal[False]


class ServiceDTO(DTO):
    org_id: str
    service_account_id: str
    name: str
    custodian_id: str
    team_id: str
    revision: int
    status: str
    expires_at: datetime


class ServicePage(DTO):
    items: list[ServiceDTO]
    truncated: bool
    policy_revision: int


class RevokedDTO(DTO):
    client_id: str | None = None
    key_id: str | None = None
    service_account_id: str | None = None
    status: Literal["revoked"]


class TemplateDTO(DTO):
    profile_id: str
    revision: int
    assigned_clients_changed: Literal[False]
    rate_limit_profile_enforced: Literal[False]


def agents(context: CurrentBrowser, *, mutation: bool = False) -> ManagedAgents:
    engine = management(context, mutation=mutation)
    if not engine.managed_agents:
        raise HTTPException(404, "managed agents are disabled")
    return ManagedAgents(engine)


@router.get("/managed-clients", response_model=ClientPage)
async def clients(org_id: str, context: CurrentBrowser) -> Any:
    return await agents(context).list_clients(org_id, context.row["user_id"])


@router.post("/managed-clients/preview", response_model=PreviewDTO)
async def preview(org_id: str, body: ClientApproval, context: CurrentBrowser) -> Any:
    return await agents(context, mutation=True).approve(
        org_id, context.row["user_id"], body, preview=True
    )


@router.post("/managed-clients", response_model=ManagedClientDTO, status_code=201)
async def approve(org_id: str, body: ClientApproval, context: CurrentBrowser) -> Any:
    return await agents(context, mutation=True).approve(org_id, context.row["user_id"], body)


@router.get("/managed-clients/{client_id}", response_model=InspectionDTO)
async def inspect(org_id: str, client_id: str, context: CurrentBrowser) -> Any:
    return await agents(context).inspect(org_id, context.row["user_id"], client_id)


@router.post(
    "/managed-clients/{client_id}/credentials",
    response_model=DeliveryDTO,
    response_model_exclude_none=True,
)
async def issue(
    org_id: str, client_id: str, body: CredentialIssue, context: CurrentBrowser, response: Response
) -> Any:
    response.headers["Cache-Control"] = "no-store"
    return await agents(context, mutation=True).issue(
        org_id, context.row["user_id"], client_id, body
    )


@router.post(
    "/managed-clients/{client_id}/revoke",
    response_model=RevokedDTO,
    response_model_exclude_none=True,
)
async def revoke(org_id: str, client_id: str, body: Revision, context: CurrentBrowser) -> Any:
    return await agents(context, mutation=True).revoke(
        org_id, context.row["user_id"], client_id, body.expected_revision
    )


@router.post(
    "/managed-clients/{client_id}/credentials/{key_id}/revoke",
    response_model=RevokedDTO,
    response_model_exclude_none=True,
)
async def revoke_key(
    org_id: str, client_id: str, key_id: str, body: Revision, context: CurrentBrowser
) -> Any:
    return await agents(context, mutation=True).revoke(
        org_id, context.row["user_id"], client_id, body.expected_revision, key_id=key_id
    )


@router.post("/service-accounts", response_model=ServiceDTO, status_code=201)
async def create_service(org_id: str, body: ServiceCreate, context: CurrentBrowser) -> Any:
    return await agents(context, mutation=True).create_service(org_id, context.row["user_id"], body)


@router.post("/service-accounts/{service_account_id}/reviewer-approval", response_model=ServiceDTO)
async def approve_reviewer(
    org_id: str, service_account_id: str, body: ReviewerApproval, context: CurrentBrowser
) -> Any:
    return await agents(context, mutation=True).reviewer_approve(
        org_id, context.row["user_id"], service_account_id, body
    )


@router.post(
    "/service-accounts/{service_account_id}/revoke",
    response_model=RevokedDTO,
    response_model_exclude_none=True,
)
async def revoke_service(
    org_id: str, service_account_id: str, body: Revision, context: CurrentBrowser
) -> Any:
    return await agents(context, mutation=True).revoke_service(
        org_id, context.row["user_id"], service_account_id, body.expected_revision
    )


@router.put("/client-templates/{profile_id}", response_model=TemplateDTO)
async def template(org_id: str, profile_id: str, body: TemplatePut, context: CurrentBrowser) -> Any:
    return await agents(context, mutation=True).put_template(
        org_id, context.row["user_id"], profile_id, body
    )


@router.get("/service-accounts", response_model=ServicePage)
async def service_accounts(org_id: str, context: CurrentBrowser) -> Any:
    return await agents(context).list_services(org_id, context.row["user_id"])
