"""Application state the API dependencies require, for router-level tests.

The security dependencies fail closed: an application that never decided
whether it authenticates is refused rather than treated as open. That is the
behaviour worth having in production, and it means a test that builds a bare
router has to say which it is — so these say it, in one place.
"""

import inspect
from functools import wraps
from typing import Any

from fastapi import FastAPI

from app.api.concurrency import RevisionConflict, revision_conflict_handler
from app.security.principal import Principal
from app.security.scopes import ALL_SCOPES, Scope, canonical
from app.security.tenant_maintenance import TenantMaintenanceMiddleware
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService


class AsyncDependencyOverrides(dict):
    """Keep unit factories on the test loop; only production providers need threads."""

    def __setitem__(self, key, value):
        if not inspect.iscoroutinefunction(value):
            factory = value

            @wraps(factory)
            async def value(*args, **kwargs):
                return factory(*args, **kwargs)

        super().__setitem__(key, value)

    def update(self, values):
        for key, value in values.items():
            self[key] = value


def open_application(*routers: Any, **state: Any) -> FastAPI:
    """Build a router unit harness with an explicitly trusted test tenant.

    Production authentication is tested separately. This harness supplies the
    verified identity that router behavior tests previously left implicit.
    """
    application = FastAPI()
    application.dependency_overrides = AsyncDependencyOverrides()
    application.add_exception_handler(RevisionConflict, revision_conflict_handler)
    for router in routers:
        application.include_router(router)

    class TestTenantAuthenticator:
        async def authenticate(self, credential):
            return principal()

        async def authenticate_gateway(self, credential):
            return principal()

    application.state.auth = TestTenantAuthenticator()
    application.add_middleware(TenantMaintenanceMiddleware)
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
    application.dependency_overrides = AsyncDependencyOverrides()
    application.add_exception_handler(RevisionConflict, revision_conflict_handler)
    for router in routers:
        application.include_router(router)
    application.add_middleware(TenantMaintenanceMiddleware)
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
    return Principal(
        key_id=key_id,
        name=name,
        scopes=canonical(scopes),
        projects=projects,
        org_id="org-test",
        cursor_secret=b"test-only-cursor-secret-at-least32",
    )
