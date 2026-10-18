"""Session-only V6 creator studio; domain implementation remains V2/V3."""

from fastapi import APIRouter

from app.creator.console import items, planning, profiles

router = APIRouter(prefix="/api/v6/organizations/{org_id}/creator", tags=["human creator studio"])
router.include_router(profiles.router)
router.include_router(planning.router)
router.include_router(items.router)
