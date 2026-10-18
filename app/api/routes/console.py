"""Read-only verified self bootstrap for the Phase9 browser foundation."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.identity.browser import CurrentBrowser
from app.identity.console import bootstrap, security

router = APIRouter(prefix="/api/v6/me", tags=["console self bootstrap"])


async def enabled(request: Request, context: CurrentBrowser) -> None:
    engine = context.service.policy_engine
    if (
        not getattr(request.app.state.settings, "v6_console_enabled", False)
        or not engine
        or not engine.managed_agents
    ):
        raise HTTPException(404, "organization console is disabled")
    await engine.enabled()
    if not await context.service.store.find(
        "_managed_agents_control", {"_id": "installation", "schema_version": 42, "activated": True}
    ):
        raise HTTPException(404, "organization console is unavailable")


@router.get("/console")
async def console(request: Request, context: CurrentBrowser) -> dict[str, Any]:
    await enabled(request, context)
    return await bootstrap(context.service, context.row)


@router.get("/security")
async def own_security(request: Request, context: CurrentBrowser) -> dict[str, Any]:
    await enabled(request, context)
    return await security(context.service, context.row)
