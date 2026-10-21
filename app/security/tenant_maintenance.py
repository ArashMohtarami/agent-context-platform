"""Persisted cutover seal and request-local tenant/policy lifetime."""

from pymongo.asynchronous.database import AsyncDatabase
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.db.client_types import MongoDocument
from app.repositories.organization_repository import MongoOrganizationRepository
from app.security.tenant import (
    TenantContext,
    TenantContextRequired,
    current_tenant,
    establish_tenant,
    tenant_request_boundary,
)
from app.security.tenant_policy import policy_request_boundary


async def check_operator_context(
    database: AsyncDatabase[MongoDocument],
    *,
    enabled: bool = False,
    management_policy: bool = False,
    human_management: bool = False,
    managed_agents: bool = False,
) -> None:
    control = await database["_tenant_control"].find_one({"_id": "installation"})
    context = current_tenant()
    if context is None:
        if control is not None or enabled:
            raise TenantContextRequired("explicit --org-id is required on a V6 installation")
        return
    if (
        control
        and control.get("stage") == "managed"
        and control.get("worker_schema_versions")
        != ([42] if managed_agents else [41] if human_management else [40])
    ):
        raise TenantContextRequired(
            "operator worker schema does not match the managed installation"
        )
    if control and control.get("stage") == "managed" and not management_policy:
        raise TenantContextRequired("managed installation requires a policy-aware operator command")
    if control is None or control.get("stage") not in {"prepared", "isolated", "managed"}:
        raise TenantContextRequired("tenant operator writes require completed adoption")
    organization = await MongoOrganizationRepository(database).get(context.org_id)
    if organization is None or organization.status not in {"sealed", "active"}:
        raise TenantContextRequired(
            "operator organization must be explicitly provisioned and sealed"
        )

    secret = control.get("cursor_secret")
    if isinstance(secret, bytes):
        establish_tenant(TenantContext(context.org_id, secret))
    if human_management:
        from app.identity.repository import IdentityStore
        from app.security.policy.engine import PolicyEngine
        from app.security.tenant_policy import TenantPolicy, bind_policy

        bind_policy(
            TenantPolicy(
                context.org_id,
                "installation_operator",
                (),
                operator=True,
                engine=PolicyEngine(
                    IdentityStore(database), human_management=True, managed_agents=managed_agents
                ),
            )
        )


class TenantMaintenanceMiddleware:
    """Check persisted adoption state on every request, without a stale cache."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        with tenant_request_boundary(), policy_request_boundary():
            await self._call(scope, receive, send)

    async def _call(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and (scope["path"].startswith(("/api", "/mcp"))):
            request = Request(scope)
            database = getattr(request.app.state, "tenant_control_database", None)
            if database is not None:
                control = await database["_tenant_control"].find_one(
                    {"_id": "installation"},
                    {
                        "schema_version": 1,
                        "stage": 1,
                        "worker_schema_versions": 1,
                        "domain_traffic_enabled": 1,
                        "cursor_secret": 1,
                    },
                )
                managed = bool(
                    getattr(
                        getattr(request.app.state, "settings", None), "v6_management_policy", False
                    )
                )
                if (
                    control is not None
                    and control.get("stage") != "not_adopted"
                    and not (
                        control.get("schema_version") == 37
                        and control.get("stage") == ("managed" if managed else "isolated")
                        and control.get("worker_schema_versions")
                        == (
                            [
                                42
                                if getattr(
                                    getattr(request.app.state, "settings", None),
                                    "v6_managed_agents",
                                    False,
                                )
                                else 41
                                if getattr(
                                    getattr(request.app.state, "settings", None),
                                    "v6_human_management",
                                    False,
                                )
                                else 40
                            ]
                            if managed
                            else [37]
                        )
                        and control.get("domain_traffic_enabled") is True
                    )
                ):
                    response = JSONResponse(
                        {"detail": "Tenant domain traffic is sealed pending Phase 2 enforcement"},
                        status_code=503,
                    )
                    await response(scope, receive, send)
                    return
                if control is not None:
                    # Installation metadata already read for this request only.
                    # This secret signs org-bound cursors; it supplies no authority.
                    scope.setdefault("state", {})["acp_tenant_cursor_secret"] = control.get(
                        "cursor_secret"
                    )
        await self.app(scope, receive, send)
