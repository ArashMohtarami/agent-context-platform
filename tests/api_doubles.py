"""Application state the API dependencies require, for router-level tests.

The security dependencies fail closed: an application that never decided
whether it authenticates is refused rather than treated as open. That is the
behaviour worth having in production, and it means a test that builds a bare
router has to say which it is — so these say it, in one place.
"""

from typing import Any

from fastapi import FastAPI

from app.api.concurrency import RevisionConflict, revision_conflict_handler
from app.security.principal import Principal
from app.security.scopes import ALL_SCOPES, Scope, canonical
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService


def open_application(*routers: Any, **state: Any) -> FastAPI:
    """Build an application that answers without a credential.

    This is the deployment shape `API_AUTH_ENABLED=false` produces, which is
    what a router test wants: the router's own behaviour, with authentication
    decided elsewhere and tested there.
    """
    application = FastAPI()
    application.add_exception_handler(RevisionConflict, revision_conflict_handler)
    for router in routers:
        application.include_router(router)
    application.state.auth = None
    application.state.audit = AuditService(None)
    for name, value in state.items():
        setattr(application.state, name, value)
    return application


def authenticated_application(
    *routers: Any,
    auth: AuthService,
    audit: AuditService | None = None,
    **state: Any,
) -> FastAPI:
    """Build an application that requires a credential."""
    application = FastAPI()
    application.add_exception_handler(RevisionConflict, revision_conflict_handler)
    for router in routers:
        application.include_router(router)
    application.state.auth = auth
    application.state.audit = audit if audit is not None else AuditService(None)
    for name, value in state.items():
        setattr(application.state, name, value)
    return application


def principal(
    *,
    key_id: str = "0123456789abcdef",
    name: str = "test key",
    scopes: tuple[Scope, ...] = ALL_SCOPES,
    projects: tuple[str, ...] | None = None,
) -> Principal:
    """Build one authenticated caller."""
    return Principal(key_id=key_id, name=name, scopes=canonical(scopes), projects=projects)
