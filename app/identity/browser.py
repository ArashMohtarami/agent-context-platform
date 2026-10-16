"""Session-only transport, CSRF/Origin checks and cookie/bearer separation."""

import hmac
import logging
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app import provenance
from app.identity.crypto import csrf
from app.identity.service import LocalIdentity
from app.security.tenant import TenantContext, establish_tenant

SESSION_COOKIE = "__Host-acp-session"
PREAUTH_COOKIE = "__Host-acp-login"


class BrowserBoundaryMiddleware:
    """No browser credential is admitted by machine transports, including MCP."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope)
            path = scope["path"]
            browser = SESSION_COOKIE in request.cookies or PREAUTH_COOKIE in request.cookies
            is_v6 = path == "/api/v6" or path.startswith("/api/v6/")
            if (browser and path.startswith(("/api/", "/mcp")) and not is_v6) or (
                is_v6 and "authorization" in request.headers
            ):
                await JSONResponse({"detail": "authentication was not accepted"}, status_code=401)(
                    scope, receive, send
                )
                return
            if is_v6:
                try:
                    too_large = int(request.headers.get("content-length", "0")) > 16384
                except ValueError:
                    too_large = True
                if too_large:
                    await JSONResponse({"detail": "invalid request"}, status_code=413)(
                        scope, receive, send
                    )
                    return

        async def private_send(message: Any) -> None:
            if scope["type"] == "http" and scope["path"].startswith("/api/v6/"):
                # Uvicorn's access logger reads the scope when sending the response.
                # OAuth callback codes/state must not become access-log query strings.
                scope["query_string"] = b""
                if message["type"] == "http.response.start":
                    message["headers"] = [
                        (k, v)
                        for k, v in message["headers"]
                        if k.lower() not in {b"cache-control", b"referrer-policy"}
                    ] + [(b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer")]
            await send(message)

        try:
            await self.app(scope, receive, private_send)
        except Exception as error:
            if scope["type"] != "http" or not scope["path"].startswith("/api/v6/"):
                raise
            logging.getLogger("agent_context.security").error(
                "identity_request_failed", extra={"error_type": type(error).__name__}
            )
            await JSONResponse({"detail": "the request could not be completed"}, status_code=500)(
                scope, receive, private_send
            )


async def identity_boundary(request: Request) -> LocalIdentity:
    service = getattr(request.app.state, "local_identity", None)
    if not isinstance(service, LocalIdentity):
        raise HTTPException(404, "local identity is disabled")
    if "authorization" in request.headers or request.url.scheme != "https":
        raise HTTPException(401, "authentication was not accepted")
    await service.enabled()
    await service.throttle("network", request.client.host if request.client else "unknown", 120)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.app.state.settings.v6_browser_origin
        if (
            request.headers.get("origin") != origin
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            raise HTTPException(403, "browser request was not accepted")
    return service


IdentityBoundary = Annotated[LocalIdentity, Depends(identity_boundary)]


@dataclass(frozen=True)
class BrowserContext:
    service: LocalIdentity
    row: dict[str, Any] = field(repr=False)
    token: str = field(repr=False)


def check_csrf(request: Request, token: str) -> None:
    value = request.headers.get("x-csrf-token", "")
    if len(value) != 64 or not value.isascii() or not hmac.compare_digest(csrf(token), value):
        raise HTTPException(403, "browser request was not accepted")


async def preauth(request: Request, service: IdentityBoundary) -> BrowserContext:
    if SESSION_COOKIE in request.cookies:
        raise HTTPException(401, "authentication was not accepted")
    token = request.cookies.get(PREAUTH_COOKIE, "")
    row = await service.sessions.authenticate(token, kind="preauth")
    check_csrf(request, token)
    return BrowserContext(service, row, token)


async def current_browser(request: Request, service: IdentityBoundary) -> BrowserContext:
    token = request.cookies.get(SESSION_COOKIE, "")
    row = await service.sessions.authenticate(token)
    if request.path_params.get("org_id", row["org_id"]) != row["org_id"]:
        raise HTTPException(403, "organization context was not accepted")
    context_id = request.headers.get("x-session-context")
    if service.sso and (context_id is not None or "org_id" in request.path_params):
        if context_id != service.policy_context(row)["context_id"]:
            raise HTTPException(409, "session context is stale")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_csrf(request, token)
        await service.throttle("security-action", row["user_id"], 10)
    provenance.set_human(
        row["user_id"],
        row["org_id"],
        service.assurance(row),
        (row.get("authentication") or {}).get("method", "local"),
    )
    control = (
        await service.store.find("_tenant_control", {"_id": "installation"})
        if service.policy_engine and service.policy_engine.human_management
        else None
    )
    secret = (control or {}).get("cursor_secret")
    establish_tenant(TenantContext(row["org_id"], secret if isinstance(secret, bytes) else None))
    if service.policy_engine:
        from app.security.tenant_policy import TenantPolicy, bind_policy

        async def refresh() -> None:
            await service.sessions.authenticate(token, touch=False)

        async def transaction_check(tx: Any) -> None:
            # Privileged commands and domain mutations require current MFA,
            # plus a write fence on the actual browser session/user/member.
            service.sessions.recent(row)
            await service.sessions.fence(row, tx)

        anchor = await service.store.find(
            "policy_anchors", {"_id": row["org_id"], "status": "active"}
        )
        if anchor:
            snapshot = await service.policy_engine.snapshot(row["org_id"], row["user_id"])
            bind_policy(
                service.policy_engine.policy(
                    snapshot,
                    refresh=refresh,
                    transaction_check=transaction_check,
                    session_id=row["public_id"],
                )
            )
        else:
            bind_policy(TenantPolicy(row["org_id"], row["user_id"], (), managed=True))
    return BrowserContext(service, row, token)


Preauth = Annotated[BrowserContext, Depends(preauth)]
CurrentBrowser = Annotated[BrowserContext, Depends(current_browser)]


def set_cookie(response: Response, token: str, *, prelogin: bool = False) -> None:
    response.set_cookie(
        PREAUTH_COOKIE if prelogin else SESSION_COOKIE,
        token,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=900 if prelogin else 28800,
    )
    if not prelogin:
        response.delete_cookie(PREAUTH_COOKIE, secure=True, httponly=True, samesite="lax", path="/")


def clear_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
