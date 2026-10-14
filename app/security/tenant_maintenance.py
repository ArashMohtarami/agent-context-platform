"""Fail closed during adoption; this is a seal, not Phase 2 authorization."""

from pymongo.asynchronous.database import AsyncDatabase
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.db.client_types import MongoDocument
from app.repositories.organization_repository import MongoOrganizationRepository
from app.security.tenant import TenantContextRequired, current_tenant


async def check_operator_context(
    database: AsyncDatabase[MongoDocument], *, enabled: bool = False
) -> None:
    control = await database["_tenant_control"].find_one({"_id": "installation"})
    context = current_tenant()
    if context is None:
        if control is not None or enabled:
            raise TenantContextRequired("explicit --org-id is required on a V6 installation")
        return
    if control is None or control.get("stage") != "prepared":
        raise TenantContextRequired("tenant operator writes require completed adoption")
    organization = await MongoOrganizationRepository(database).get(context.org_id)
    if organization is None or organization.status != "sealed":
        raise TenantContextRequired(
            "operator organization must be explicitly provisioned and sealed"
        )


class TenantMaintenanceMiddleware:
    """Check persisted adoption state on every request, without a stale cache."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and (
            scope["path"].startswith(("/api", "/mcp")) or scope["path"] == "/metrics"
        ):
            request = Request(scope)
            database = getattr(request.app.state, "tenant_control_database", None)
            if database is not None:
                control = await database["_tenant_control"].find_one({"_id": "installation"})
                if control is not None and control.get("stage") != "not_adopted":
                    response = JSONResponse(
                        {"detail": "Tenant domain traffic is sealed pending Phase 2 enforcement"},
                        status_code=503,
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
