"""Session-only OIDC and own-membership boundaries. No management/data UI."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes.local_identity import authenticated
from app.identity.browser import (
    PREAUTH_COOKIE,
    SESSION_COOKIE,
    CurrentBrowser,
    IdentityBoundary,
    Preauth,
    clear_cookie,
)
from app.identity.crypto import AuthRejected
from app.identity.sso import SSOIdentity, policy_context

router = APIRouter(prefix="/api/v6", tags=["SSO and own organizations"])


async def sso_boundary(service: IdentityBoundary) -> SSOIdentity:
    if service.sso is None:
        raise HTTPException(404, "SSO is disabled")
    await service.sso.enabled()
    return service.sso


SSO = Annotated[SSOIdentity, Depends(sso_boundary)]


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class Reauthenticate(Target):
    purpose: Literal["switch", "reauth"] = "switch"


@router.post("/auth/oidc/start")
async def start(body: Target, context: Preauth, sso: SSO) -> dict[str, str]:
    return {"authorization_url": await sso.start(body.org_id, context.row, context.token, "login")}


@router.post("/auth/identities/link")
async def link(body: Target, context: CurrentBrowser, sso: SSO) -> dict[str, str]:
    return {"authorization_url": await sso.start(body.org_id, context.row, context.token, "link")}


@router.post("/auth/oidc/reauthenticate")
async def reauthenticate(body: Reauthenticate, context: CurrentBrowser, sso: SSO) -> dict[str, str]:
    return {
        "authorization_url": await sso.start(body.org_id, context.row, context.token, body.purpose)
    }


@router.get("/auth/oidc/callback")
async def callback(request: Request, response: Response, sso: SSO) -> dict[str, Any]:
    # OAuth callbacks are the narrow mutation exception to Origin/CSRF headers.
    # A single-use state transaction binds browser, purpose, org, nonce and PKCE.
    pairs = request.query_params
    if (
        set(pairs) - {"state", "code", "iss", "session_state", "error", "error_description"}
        or any(len(pairs.getlist(key)) != 1 for key in pairs)
        or "error" in pairs
        or not {"state", "code"} <= set(pairs)
    ):
        raise AuthRejected("authentication was not accepted")
    token = request.cookies.get(SESSION_COOKIE) or request.cookies.get(PREAUTH_COOKIE, "")
    # RFC9207 issuer parameter, when present, is checked before token exchange.
    from app.identity.crypto import digest

    flow = await sso.store.find(
        "oidc_transactions",
        {
            "_id": digest("oidc-state", pairs["state"]),
            "browser_id": digest("oidc-browser", token),
        },
    )
    if not flow:
        raise AuthRejected("authentication was not accepted")
    registration = sso.registry.get(flow["registration_id"])
    if str(request.url.replace(query="")) != registration.redirect_uri or (
        "iss" in pairs and pairs["iss"] != registration.issuer
    ):
        raise AuthRejected("authentication was not accepted")
    issued = await sso.callback(pairs["state"], pairs["code"], token)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    if issued is None:
        clear_cookie(response)
        return {"linked": True, "reauthentication_required": True}
    return {**authenticated(response, issued), "policy_context": policy_context(issued.row)}


@router.get("/me/organizations")
async def organizations(context: CurrentBrowser, sso: SSO) -> dict[str, Any]:
    return {"organizations": await sso.memberships(context.row)}


@router.post("/me/active-organization")
async def switch(
    body: Target, response: Response, context: CurrentBrowser, sso: SSO
) -> dict[str, Any]:
    issued = await sso.switch(context.row, body.org_id)
    return {**authenticated(response, issued), "policy_context": policy_context(issued.row)}


@router.get("/organizations/{org_id}/session-context")
async def organization_context(org_id: str, context: CurrentBrowser, sso: SSO) -> dict[str, Any]:
    return {"policy_context": policy_context(context.row)}


@router.post("/auth/oidc/logout")
async def logout(response: Response, context: CurrentBrowser, sso: SSO) -> dict[str, str]:
    url = await sso.logout(context.row)
    clear_cookie(response)
    return {"end_session_url": url}


@router.get("/auth/oidc/logged-out")
async def logged_out(request: Request, sso: SSO) -> dict[str, bool]:
    if set(request.query_params) != {"state"} or len(request.query_params.getlist("state")) != 1:
        raise AuthRejected("authentication was not accepted")
    await sso.logged_out(request.query_params["state"])
    return {"logged_out": True}
