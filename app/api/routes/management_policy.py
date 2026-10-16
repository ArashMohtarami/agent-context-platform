"""Session-only Phase 5 governance foundation; no team or corpus management UI."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.identity.browser import CurrentBrowser
from app.identity.models import Input
from app.identity.repository import Row
from app.security.policy.engine import PolicyEngine
from app.security.policy.evaluation import PolicyDenied
from app.security.policy.governance import Governance
from app.security.policy.models import Grant, Issue

router = APIRouter(prefix="/api/v6", tags=["management policy"])


def engine(context: CurrentBrowser, *, mutation: bool = False) -> PolicyEngine:
    policy = context.service.policy_engine
    if policy is None:
        raise HTTPException(404, "management policy is disabled")
    if mutation:
        context.service.sessions.recent(context.row)
    return policy


async def authorized(
    context: CurrentBrowser, action: str, kind: str, identifier: str
) -> tuple[PolicyEngine, Row]:
    service = engine(context)
    snapshot = await service.snapshot(context.row["org_id"], context.row["user_id"])
    service.require(snapshot, action, kind, identifier)
    return service, snapshot


@router.get("/me/effective-access")
async def effective_access(context: CurrentBrowser) -> Row:
    service, snapshot = await authorized(context, "grants:read", "member", context.row["user_id"])
    return {
        "org_id": context.row["org_id"],
        "subject_id": context.row["user_id"],
        "policy_revision": snapshot["anchor"]["revision"],
        "tuples": [g.model_dump() for g in snapshot["tuples"]],
        "assignments": snapshot["explanations"],
        "role_has_corpus_bypass": False,
    }


@router.get("/organizations/{org_id}")
async def organization(org_id: str, context: CurrentBrowser) -> Row:
    service, snapshot = await authorized(context, "org:read", "organization", org_id)
    row = await service.store.find("organizations", {"org_id": org_id})
    if not row:
        raise PolicyDenied("organization is unavailable")
    return {key: row[key] for key in ("org_id", "name", "status", "auth_policy", "policy_version")}


@router.get("/organizations/{org_id}/assignments")
async def assignments(org_id: str, context: CurrentBrowser, subject_id: str) -> Row:
    _, snapshot = await authorized(context, "grants:read", "member", subject_id)
    # Explicit target selection; never expose another org or raw identity DTOs.
    return {
        "org_id": org_id,
        "subject_id": subject_id,
        "assignments": [
            row for row in snapshot["assignments"].values() if row["subject_id"] == subject_id
        ],
    }


@router.post("/organizations/{org_id}/assignments", status_code=201)
@router.post("/organizations/{org_id}/delegations", status_code=201)
async def issue(org_id: str, body: Issue, context: CurrentBrowser) -> Row:
    return await Governance(engine(context, mutation=True)).issue(
        org_id, context.row["user_id"], body
    )


class Change(Input):
    expected_revision: int = Field(ge=1)
    tuples: tuple[Grant, ...] = Field(default=(), max_length=100)


@router.patch("/organizations/{org_id}/assignments/{assignment_id}")
async def narrow(org_id: str, assignment_id: str, body: Change, context: CurrentBrowser) -> Row:
    return await Governance(engine(context, mutation=True)).change(
        org_id, context.row["user_id"], assignment_id, body.expected_revision, tuples=body.tuples
    )


class Revision(Input):
    expected_revision: int = Field(ge=1)


@router.post("/organizations/{org_id}/delegations/{assignment_id}/revoke")
async def revoke(org_id: str, assignment_id: str, body: Revision, context: CurrentBrowser) -> Row:
    return await Governance(engine(context, mutation=True)).change(
        org_id, context.row["user_id"], assignment_id, body.expected_revision, revoke=True
    )


class Rename(Input):
    name: str = Field(min_length=1, max_length=500)
    expected_revision: int = Field(ge=1)


@router.patch("/organizations/{org_id}")
async def rename(org_id: str, body: Rename, context: CurrentBrowser) -> Row:
    service = engine(context, mutation=True)

    async def operation(snapshot: Row, tx: Any) -> Row:
        service.require(snapshot, "org:manage", "organization", org_id)
        if snapshot["anchor"]["revision"] != body.expected_revision:
            raise PolicyDenied("organization policy changed")
        await service.store.update(
            "organizations", {"org_id": org_id}, {"$set": {"name": body.name}}, tx
        )
        await service.audit(org_id, context.row["user_id"], "organization_renamed", org_id, tx)
        return {"org_id": org_id, "name": body.name}

    return await Governance(service).mutate(org_id, context.row["user_id"], operation)


class AuthPolicy(Input):
    auth_policy: Literal["local_only", "sso_preferred", "sso_required"]
    expected_revision: int = Field(ge=1)


@router.patch("/organizations/{org_id}/policy")
async def auth_policy(org_id: str, body: AuthPolicy, context: CurrentBrowser) -> Row:
    service = engine(context, mutation=True)

    async def operation(snapshot: Row, tx: Any) -> Row:
        service.require(snapshot, "org:manage", "organization", org_id)
        if snapshot["anchor"]["revision"] != body.expected_revision:
            raise PolicyDenied("organization policy changed")
        if body.auth_policy != "local_only" and not await service.store.find(
            "organization_oidc", {"org_id": org_id}, tx
        ):
            raise PolicyDenied("an operator-approved provider is required")
        await service.store.update(
            "organizations",
            {"org_id": org_id},
            {"$set": {"auth_policy": body.auth_policy}, "$inc": {"policy_version": 1}},
            tx,
        )
        await service.audit(
            org_id, context.row["user_id"], "organization_auth_policy_changed", org_id, tx
        )
        return {"org_id": org_id, "reauthentication_required": True}

    return await Governance(service).mutate(org_id, context.row["user_id"], operation)


class Transfer(Input):
    target_id: str = Field(min_length=1, max_length=128)


@router.post("/organizations/{org_id}/ownership-transfer", status_code=201)
async def transfer(org_id: str, body: Transfer, context: CurrentBrowser) -> Row:
    return await Governance(engine(context, mutation=True)).offer_transfer(
        org_id, context.row["user_id"], body.target_id
    )


@router.post("/organizations/{org_id}/ownership-transfer/{transfer_id}/accept")
async def accept_transfer(org_id: str, transfer_id: str, context: CurrentBrowser) -> Row:
    return await Governance(engine(context, mutation=True)).accept_transfer(
        org_id, context.row["user_id"], transfer_id
    )
