"""Narrow pre-tenant credential resolution, never a domain repository.

Only the globally unique public key ID is looked up before authentication.
No caller-supplied organization/filter or list/export operation is exposed.
"""

from datetime import datetime

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.models.api_key import ApiKeyDocument
from app.security.tenant import TenantContext, tenant_scope


class MongoCredentialResolver:
    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        self._database = database

    async def get_by_key_id(self, key_id: str) -> ApiKeyDocument | None:
        raw = await self._database["api_keys"].find_one({"key_id": key_id})
        if raw is None or not isinstance(raw.get("org_id"), str):
            return None
        with tenant_scope(TenantContext(str(raw["org_id"]))):
            return ApiKeyDocument.model_validate(raw, context={"from_storage": True})

    async def organization_enabled(self, org_id: str) -> bool:
        control = await self._database["_tenant_control"].find_one({"_id": "installation"})
        organization = await self._database["organizations"].find_one({"org_id": org_id})
        return bool(
            control
            and control.get("schema_version") == 37
            and control.get("stage") == "isolated"
            and control.get("domain_traffic_enabled") is True
            and organization
            and organization.get("status") == "active"
        )

    async def cursor_secret(self) -> bytes:
        control = await self._database["_tenant_control"].find_one({"_id": "installation"})
        value = control.get("cursor_secret") if control else None
        if not isinstance(value, bytes) or len(value) < 32:
            raise ValueError("tenant cursor secret is not configured")
        return value

    async def touch(self, key_id: str, used_at: datetime) -> None:
        from app.security.tenant import require_tenant

        await self._database["api_keys"].update_one(
            {"key_id": key_id, "org_id": require_tenant().org_id},
            {"$set": {"last_used_at": used_at}},
        )
