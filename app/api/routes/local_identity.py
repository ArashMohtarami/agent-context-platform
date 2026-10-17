"""Phase 3 identity and self-security endpoints. No org data or management UI."""

import asyncio
from time import monotonic
from typing import Any

from fastapi import APIRouter, Request, Response

from app.identity.browser import (
    PREAUTH_COOKIE,
    SESSION_COOKIE,
    CurrentBrowser,
    IdentityBoundary,
    Preauth,
    clear_cookie,
    set_cookie,
)
from app.identity.crypto import AuthRejected, csrf, digest
from app.identity.models import (
    ChallengeInput,
    ChangePassword,
    EmailInput,
    FactorInput,
    FinishLogin,
    LoginInput,
    PasswordChallenge,
)
from app.identity.sessions import IssuedSession

router = APIRouter(prefix="/api/v6", tags=["local identity"])


def authenticated(
    response: Response, issued: IssuedSession, codes: list[str] | None = None
) -> dict[str, Any]:
    set_cookie(response, issued.token)
    result: dict[str, Any] = {"authenticated": True, "csrf_token": issued.csrf_token}
    if issued.row.get("authentication"):
        from app.identity.sso import policy_context

        result["policy_context"] = policy_context(issued.row)
    if codes is not None:
        result["recovery_codes"] = codes
    return result


@router.get("/auth/csrf")
async def csrf_token(
    request: Request, response: Response, service: IdentityBoundary
) -> dict[str, Any]:
    if SESSION_COOKIE in request.cookies:
        token = request.cookies[SESSION_COOKIE]
        try:
            await service.sessions.authenticate(token)
            return {"csrf_token": csrf(token)}
        except AuthRejected:
            # Invalid HttpOnly cookies must not strand the browser in a login loop.
            clear_cookie(response)
    token = request.cookies.get(PREAUTH_COOKIE, "")
    if token:
        try:
            await service.sessions.authenticate(token, kind="preauth")
        except AuthRejected:
            token = ""
    if not token:
        issued = await service.sessions.preauth()
        token = issued.token
        set_cookie(response, token, prelogin=True)
    return {"csrf_token": csrf(token)}


@router.post("/auth/local/login")
async def login(body: LoginInput, context: Preauth) -> dict[str, Any]:
    token, needed = await context.service.login(
        body.email, body.password.get_secret_value(), body.org_id, digest("browser", context.token)
    )
    return {"challenge": token, "mfa_required": needed}


@router.post("/auth/local/complete")
async def complete_login(body: FinishLogin, response: Response, context: Preauth) -> dict[str, Any]:
    issued = await context.service.finish_login(
        body.token,
        digest("browser", context.token),
        body.code.get_secret_value(),
        recovery=body.recovery,
    )
    return authenticated(response, issued)


@router.post("/auth/invitations/accept", status_code=202)
async def accept_invitation(body: PasswordChallenge, context: Preauth) -> dict[str, Any]:
    await context.service.accept_invite(
        body.token,
        body.password.get_secret_value(),
        digest("browser", context.token),
        body.code.get_secret_value(),
    )
    return {"message": "Continue with the verification message if the request was accepted"}


@router.post("/auth/local/verify", status_code=204)
async def verify(body: ChallengeInput, context: Preauth) -> None:
    await context.service.verify(body.token, digest("browser", context.token))


@router.post("/auth/password-reset/request", status_code=202)
async def request_reset(body: EmailInput, context: Preauth) -> dict[str, Any]:
    started = monotonic()
    try:
        await context.service.request_reset(body.email)
    finally:
        await asyncio.sleep(max(0, 0.2 - (monotonic() - started)))
    return {"message": "If eligible, a reset message will be delivered"}


@router.post("/auth/password-reset/consume", status_code=204)
async def reset(body: PasswordChallenge, context: Preauth) -> None:
    await context.service.reset(body.token, body.password.get_secret_value())


@router.get("/me")
async def me(context: CurrentBrowser) -> dict[str, Any]:
    return await context.service.me(context.row)


@router.get("/me/sessions")
async def sessions(context: CurrentBrowser) -> dict[str, Any]:
    return {"sessions": await context.service.list_sessions(context.row)}


@router.post("/me/sessions/{session_id}/revoke", status_code=204)
async def revoke(session_id: str, context: CurrentBrowser) -> None:
    await context.service.revoke_session(context.row, session_id[:128])


@router.post("/auth/session/rotate")
async def rotate(response: Response, context: CurrentBrowser) -> dict[str, Any]:
    return authenticated(response, await context.service.rotate(context.row))


@router.post("/auth/logout", status_code=204)
async def logout(response: Response, context: CurrentBrowser) -> None:
    await context.service.logout(context.row)
    clear_cookie(response)


@router.post("/auth/mfa/enroll")
async def enroll(context: CurrentBrowser) -> dict[str, Any]:
    seed, uri = await context.service.begin_mfa(context.row)
    return {"seed": seed, "provisioning_uri": uri}


@router.post("/auth/mfa/confirm")
async def confirm(body: FactorInput, response: Response, context: CurrentBrowser) -> dict[str, Any]:
    issued, codes = await context.service.confirm_mfa(context.row, body.code.get_secret_value())
    return authenticated(response, issued, codes)


@router.post("/auth/step-up")
async def step_up(body: FactorInput, response: Response, context: CurrentBrowser) -> dict[str, Any]:
    issued = await context.service.step_up(context.row, body.code.get_secret_value(), body.recovery)
    return authenticated(response, issued)


@router.post("/auth/mfa/remove", status_code=204)
async def remove(response: Response, context: CurrentBrowser) -> None:
    await context.service.remove_mfa(context.row)
    clear_cookie(response)


@router.post("/auth/mfa/recovery")
async def recovery(
    body: FactorInput, response: Response, context: CurrentBrowser
) -> dict[str, Any]:
    issued, codes = await context.service.renew_recovery(context.row, body.code.get_secret_value())
    return authenticated(response, issued, codes)


@router.post("/auth/password/change", status_code=204)
async def change_password(
    body: ChangePassword, response: Response, context: CurrentBrowser
) -> None:
    await context.service.change_password(
        context.row, body.old_password.get_secret_value(), body.new_password.get_secret_value()
    )
    clear_cookie(response)
