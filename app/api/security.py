"""What every endpoint asks before it does anything.

An endpoint declares the scope it needs and, where it touches project- or
profile-owned data, that the caller may reach that resource. Both are
dependencies rather than lines inside a handler, because a dependency cannot be
forgotten halfway through a function.

The refusals are deliberately uninformative. A caller that did not
authenticate is told the credential was not accepted, whatever was wrong with
it; a caller refused a project is not told which, because naming it confirms it
exists. The reason is kept in the audit trail and the operator's log.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app import provenance
from app.models.audit_log import AuditAction, AuditOutcome
from app.security.principal import (
    AuthenticationError,
    AuthorizationError,
    Principal,
    unauthenticated_principal,
)
from app.security.scopes import Scope
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService

BEARER = "Bearer"
_UNAUTHENTICATED_DETAIL = "the API key was not accepted"
# Sent with every 401 so a client knows what kind of credential to present.
_CHALLENGE = {"WWW-Authenticate": BEARER}

logger = logging.getLogger("agent_context.security")


def credential(authorization: str | None) -> str | None:
    """Extract the bearer credential from an Authorization header.

    Anything that is not a bearer scheme is treated as no credential at all
    rather than passed on, so a Basic header cannot reach the key parser.
    """
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != BEARER.lower():
        return None
    return value.strip() or None


async def current_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Resolve who is calling, or refuse the request.

    When a deployment has authentication switched off this returns the open
    principal rather than skipping the dependency, so every endpoint still runs
    the same code path and there is no branch that bypasses a check.
    """
    state = request.app.state
    if not hasattr(state, "auth") or not hasattr(state, "audit"):
        # Fail closed. An application whose authentication decision was never
        # made is misconfigured, and treating that as "open" would turn a
        # wiring mistake into an unauthenticated platform.
        logger.error("authentication_not_configured", extra={"http_path": request.url.path})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="authentication is not configured",
        )
    auth: AuthService | None = state.auth
    audit: AuditService = state.audit
    if auth is None:
        # Explicitly open, decided once at startup and reported by readiness.
        principal = unauthenticated_principal()
        provenance.set_principal(principal)
        return principal
    try:
        principal = await auth.authenticate(credential(authorization))
    except AuthenticationError as error:
        await audit.record(
            "authenticate",
            "denied",
            method=request.method,
            path=request.url.path,
            request_id=_request_id(request),
            reason=str(error),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_UNAUTHENTICATED_DETAIL,
            headers=_CHALLENGE,
        ) from error
    request.state.principal = principal
    provenance.set_principal(principal)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


async def authorize_scope(request: Request, principal: Principal, scope: Scope) -> None:
    """Enforce one scope when the required V1 target kind is runtime-selected."""
    audit: AuditService = request.app.state.audit
    try:
        principal.require_scope(scope)
    except AuthorizationError as error:
        _denied(principal, reason="scope", scope=scope, path=request.url.path)
        await audit.record(
            "authorize",
            "denied",
            principal=principal,
            scope=scope,
            method=request.method,
            path=request.url.path,
            request_id=_request_id(request),
            reason=str(error),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error


def requires(scope: Scope) -> Callable[[Request, Principal], Awaitable[Principal]]:
    """Build the dependency that enforces one scope.

    Returned as a dependency rather than checked inside a handler so the
    requirement is part of the route's declaration and shows up in the schema.
    """

    async def dependency(request: Request, principal: CurrentPrincipal) -> Principal:
        await authorize_scope(request, principal, scope)
        return principal

    return dependency


async def authorize_project(
    request: Request,
    principal: Principal,
    project_id: str | None,
) -> None:
    """Refuse a project this caller may not reach.

    Called by every endpoint that names a project, including the ones that take
    it in a body rather than a path, because the check has to follow the value
    and not the route.
    """
    audit: AuditService = request.app.state.audit
    try:
        principal.require_project(project_id)
    except AuthorizationError as error:
        _denied(principal, reason="project", scope=None, path=request.url.path)
        await audit.record(
            "authorize",
            "denied",
            principal=principal,
            project_id=project_id,
            method=request.method,
            path=request.url.path,
            request_id=_request_id(request),
            reason=str(error),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error


async def authorize_research_project_links(
    request: Request, principal: Principal, project_ids: tuple[str, ...] | list[str]
) -> None:
    """Require the existing V1 grant for every project linked to research data."""
    for project_id in project_ids:
        await authorize_project(request, principal, project_id)


async def authorize_profile(
    request: Request,
    principal: Principal,
    profile_id: str,
) -> None:
    """Refuse a creator profile outside the caller's explicit grant.

    Profile identifiers are independent from V1 project identifiers.  Checking
    them here keeps creator data multi-profile safe without ever widening a
    project grant or exposing a refused profile's existence.
    """
    audit: AuditService = request.app.state.audit
    try:
        principal.require_profile(profile_id)
    except AuthorizationError as error:
        _denied(principal, reason="profile", scope=None, path=request.url.path)
        await audit.record(
            "authorize",
            "denied",
            principal=principal,
            profile_id=profile_id,
            method=request.method,
            path=request.url.path,
            request_id=_request_id(request),
            reason=str(error),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error


async def record_write(
    request: Request,
    principal: Principal,
    action: AuditAction,
    project_id: str | None,
) -> None:
    """Record that this caller changed something.

    Only writes. Recording every read would bury the entries that matter under
    the ones that do not, and reads are already governed by the scope and
    project checks above — what an investigator needs afterwards is who added
    or corrected a piece of knowledge, not who looked at one.

    What is recorded is the decision, never the content: the trail says a
    context item was stored in a project, not what it said.
    """
    audit: AuditService = request.app.state.audit
    await audit.record(
        action,
        "allowed",
        principal=principal,
        project_id=project_id,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )


async def record_creator_write(
    request: Request,
    principal: Principal,
    profile_id: str,
) -> None:
    """Record a permitted creator-profile mutation without its preference data."""
    audit: AuditService = request.app.state.audit
    await audit.record(
        "creator.write",
        "allowed",
        principal=principal,
        profile_id=profile_id,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )


async def record_content_write(
    request: Request,
    principal: Principal,
    profile_id: str,
) -> None:
    """Record a permitted content mutation without recording its prose."""
    audit: AuditService = request.app.state.audit
    await audit.record(
        "content.write",
        "allowed",
        principal=principal,
        profile_id=profile_id,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )


async def record_research_write(
    request: Request, principal: Principal, research_project_id: str
) -> None:
    """Record an allowed Phase 1 research-project metadata mutation."""
    audit: AuditService = request.app.state.audit
    await audit.record(
        "research.write",
        "allowed",
        principal=principal,
        research_project_id=research_project_id,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )


async def record_content_approval(
    request: Request,
    principal: Principal,
    profile_id: str,
    *,
    outcome: AuditOutcome = "allowed",
    reason: str | None = None,
) -> None:
    """Record the explicit approval decision, never its reviewed content."""
    audit: AuditService = request.app.state.audit
    await audit.record(
        "content.approve",
        outcome,
        principal=principal,
        profile_id=profile_id,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
        reason=reason,
    )


async def record_content_publish(
    request: Request,
    principal: Principal,
    profile_id: str,
    *,
    outcome: AuditOutcome = "allowed",
    reason: str | None = None,
) -> None:
    """Record an explicit publication decision without logging content or credentials."""
    audit: AuditService = request.app.state.audit
    await audit.record(
        "content.publish",
        outcome,
        principal=principal,
        profile_id=profile_id,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
        reason=reason,
    )


def _denied(principal: Principal, *, reason: str, scope: Scope | None, path: str) -> None:
    """Record one refusal where an operator reads it.

    Written alongside the audit record rather than instead of it: the trail is
    the durable history, and this is what an operator watching a deployment
    sees — including when the trail is switched off or momentarily unwritable.

    The refused project is not among the fields. It is withheld from the caller
    because naming it confirms it exists, and a log line is a place that
    reasoning does not stop applying, since logs get shipped and shared.
    """
    logger.warning(
        "authorization_denied",
        extra={
            "key_id": principal.key_id,
            "auth_reason": reason,
            "auth_scope": scope,
            "http_path": path,
        },
    )


def _request_id(request: Request) -> str | None:
    """Return the request identifier the logging middleware generated."""
    value = request.headers.get("x-request-id")
    return value if value else getattr(request.state, "request_id", None)
