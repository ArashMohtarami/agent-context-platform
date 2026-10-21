"""Session-only human organization APIs. All mutations use shared governance fencing."""

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes.management_policy import engine
from app.identity.browser import CurrentBrowser
from app.identity.models import ChallengeInput
from app.management.access_requests import AccessRequests
from app.management.directory import Directory
from app.management.invitations import Invitations
from app.management.memberships import Memberships
from app.management.models import (
    Decision,
    InviteCreate,
    RequestCreate,
    ReviewedPolicy,
    Revision,
    TeamChange,
    TeamCreate,
    TeamMemberChange,
)
from app.management.teams import Teams
from app.security.policy.engine import PolicyEngine
from app.security.policy.models import Grant


class DirectoryRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def directory(request: Request) -> Response:
            # These three read services take their own current policy snapshot
            # before constructing any predicate. No corpus principal is needed.
            if request.method == "GET" and self.name in {"members", "teams", "team_members"}:
                request.state.v6_service_policy = True
            return await original(request)

        return directory


router = APIRouter(
    prefix="/api/v6", tags=["human organization management"], route_class=DirectoryRoute
)
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=2048)]
Prefix = Annotated[str, Query(max_length=128)]


class DTO(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MemberDTO(DTO):
    org_id: str
    user_id: str
    display_name: str
    role: str
    status: str
    revision: int


class TeamMemberDTO(MemberDTO):
    team_id: str
    team_membership_status: Literal["active", "suspended", "revoked"]
    team_membership_revision: int


class TeamLinkDTO(DTO):
    org_id: str
    team_id: str
    user_id: str
    status: Literal["active", "suspended", "revoked"]
    revision: int


class TeamDTO(DTO):
    org_id: str
    team_id: str
    name: str
    status: Literal["active", "suspended"]
    revision: int
    generation: int


class Page[T](DTO):
    items: list[T]
    next_cursor: str | None
    policy_revision: int


class InvitationDTO(DTO):
    id: str = Field(alias="_id")
    org_id: str
    user_id: str
    issuer_id: str
    policy: ReviewedPolicy
    status: Literal["pending", "accepted", "revoked"]
    revision: int
    expires_at: datetime
    accepted_at: datetime | None
    assignment_id: str | None


class InviteReceipt(DTO):
    invitation_id: str
    status: str
    expires_at: datetime | None = None
    org_id: str | None = None
    reauthentication_required: bool | None = None


class RequestDTO(DTO):
    id: str = Field(alias="_id")
    org_id: str
    user_id: str
    policy: ReviewedPolicy
    policy_revision: int
    status: str
    revision: int
    expires_at: datetime
    expired: bool = False


class DecisionDTO(DTO):
    id: str = Field(alias="_id")
    org_id: str
    request_id: str
    request_revision: int
    policy_revision: int
    actor_id: str
    outcome: Literal["approved", "denied"]
    assignment_id: str | None
    created_at: datetime


class ExplanationDTO(DTO):
    assignment_id: str
    effective: bool
    reason: str | None = None
    generation: int | None = None
    expires_at: datetime | None = None
    tuples: list[Grant] = Field(default_factory=list)


class EffectiveDTO(DTO):
    org_id: str
    subject_id: str
    policy_revision: int
    tuples: list[Grant]
    assignments: list[ExplanationDTO]
    role_has_corpus_bypass: Literal[False]


def management(context: CurrentBrowser, *, mutation: bool = False) -> PolicyEngine:
    service = engine(context, mutation=mutation)
    if not service.human_management:
        raise HTTPException(404, "human management is disabled")
    return service


@router.get("/organizations/{org_id}/members", response_model=Page[MemberDTO])
async def members(
    org_id: str,
    context: CurrentBrowser,
    prefix: Prefix = "",
    limit: Limit = 50,
    cursor: Cursor = None,
) -> Any:
    return await Directory(management(context)).members(
        org_id, context.row["user_id"], prefix=prefix, limit=limit, cursor=cursor
    )


@router.get(
    "/organizations/{org_id}/members/{user_id}/effective-access",
    response_model=EffectiveDTO,
    response_model_exclude_none=True,
)
async def effective(org_id: str, user_id: str, context: CurrentBrowser) -> Any:
    return await Directory(management(context)).effective_access(
        org_id, context.row["user_id"], user_id
    )


async def member_state(
    org_id: str,
    user_id: str,
    body: Revision,
    context: CurrentBrowser,
    state: Literal["active", "suspended", "revoked"],
) -> Any:
    return await Memberships(management(context, mutation=True)).change_status(
        org_id, context.row["user_id"], user_id, body.expected_revision, state
    )


@router.post("/organizations/{org_id}/members/{user_id}/suspend", response_model=MemberDTO)
async def suspend(org_id: str, user_id: str, body: Revision, context: CurrentBrowser) -> Any:
    return await member_state(org_id, user_id, body, context, "suspended")


@router.post("/organizations/{org_id}/members/{user_id}/reinstate", response_model=MemberDTO)
async def reinstate(org_id: str, user_id: str, body: Revision, context: CurrentBrowser) -> Any:
    return await member_state(org_id, user_id, body, context, "active")


@router.post("/organizations/{org_id}/members/{user_id}/offboard", response_model=MemberDTO)
async def offboard(org_id: str, user_id: str, body: Revision, context: CurrentBrowser) -> Any:
    return await member_state(org_id, user_id, body, context, "revoked")


@router.get("/organizations/{org_id}/teams", response_model=Page[TeamDTO])
async def teams(
    org_id: str,
    context: CurrentBrowser,
    limit: Limit = 50,
    cursor: Cursor = None,
    prefix: Prefix = "",
    status: Literal["active", "suspended"] | None = None,
) -> Any:
    return await Directory(management(context)).teams(
        org_id, context.row["user_id"], limit=limit, cursor=cursor, prefix=prefix, status=status
    )


@router.post("/organizations/{org_id}/teams", response_model=TeamDTO, status_code=201)
async def create_team(org_id: str, body: TeamCreate, context: CurrentBrowser) -> Any:
    return await Teams(management(context, mutation=True)).create(
        org_id, context.row["user_id"], body
    )


@router.patch("/organizations/{org_id}/teams/{team_id}", response_model=TeamDTO)
async def change_team(org_id: str, team_id: str, body: TeamChange, context: CurrentBrowser) -> Any:
    return await Teams(management(context, mutation=True)).change_team(
        org_id, context.row["user_id"], team_id, body
    )


@router.get("/organizations/{org_id}/teams/{team_id}/members", response_model=Page[TeamMemberDTO])
async def team_members(
    org_id: str, team_id: str, context: CurrentBrowser, limit: Limit = 50, cursor: Cursor = None
) -> Any:
    return await Directory(management(context)).team_members(
        org_id, context.row["user_id"], team_id, limit=limit, cursor=cursor
    )


@router.post(
    "/organizations/{org_id}/teams/{team_id}/members/{user_id}", response_model=TeamLinkDTO
)
async def change_team_member(
    org_id: str, team_id: str, user_id: str, body: TeamMemberChange, context: CurrentBrowser
) -> Any:
    return await Teams(management(context, mutation=True)).change_member(
        org_id, context.row["user_id"], team_id, user_id, body
    )


@router.get("/organizations/{org_id}/invitations", response_model=Page[InvitationDTO])
async def invitations(
    org_id: str, context: CurrentBrowser, limit: Limit = 50, cursor: Cursor = None
) -> Any:
    management(context)
    return await Invitations(context.service).list_invitations(
        org_id, context.row["user_id"], limit=limit, cursor=cursor
    )


@router.post("/organizations/{org_id}/invitations", response_model=InviteReceipt, status_code=201)
async def invite(org_id: str, body: InviteCreate, context: CurrentBrowser) -> Any:
    management(context, mutation=True)
    return await Invitations(context.service).create(org_id, context.row["user_id"], body)


@router.post(
    "/organizations/{org_id}/invitations/{invitation_id}/revoke", response_model=InviteReceipt
)
async def revoke_invite(
    org_id: str, invitation_id: str, body: Revision, context: CurrentBrowser
) -> Any:
    management(context, mutation=True)
    return await Invitations(context.service).revoke(
        org_id, context.row["user_id"], invitation_id, body.expected_revision
    )


@router.post("/me/invitations/accept", response_model=InviteReceipt)
async def accept_invite(body: ChallengeInput, context: CurrentBrowser) -> Any:
    management(context, mutation=True)
    return await Invitations(context.service).accept(context.row, body.token)


@router.get("/organizations/{org_id}/access-requests", response_model=Page[RequestDTO])
async def requests(
    org_id: str, context: CurrentBrowser, limit: Limit = 50, cursor: Cursor = None
) -> Any:
    return await AccessRequests(management(context)).list_requests(
        org_id, context.row["user_id"], limit=limit, cursor=cursor
    )


@router.post("/organizations/{org_id}/access-requests", response_model=RequestDTO, status_code=201)
async def request_access(org_id: str, body: RequestCreate, context: CurrentBrowser) -> Any:
    return await AccessRequests(management(context, mutation=True)).create(
        org_id, context.row["user_id"], body
    )


@router.post(
    "/organizations/{org_id}/access-requests/{request_id}/decisions",
    response_model=DecisionDTO,
    status_code=201,
)
async def decide(org_id: str, request_id: str, body: Decision, context: CurrentBrowser) -> Any:
    return await AccessRequests(management(context, mutation=True)).decide(
        org_id, context.row["user_id"], request_id, body
    )


@router.post(
    "/organizations/{org_id}/access-requests/{request_id}/cancel", response_model=RequestDTO
)
async def cancel(org_id: str, request_id: str, body: Revision, context: CurrentBrowser) -> Any:
    return await AccessRequests(management(context, mutation=True)).cancel(
        org_id, context.row["user_id"], request_id, body.expected_revision
    )
