"""Content-free best-effort human studio audit, without a machine identity.

Only this server-owned append uses the raw collection. It shares the current
human session/policy fence; it grants neither audit reads nor corpus authority.
The legacy key_id sentinel means no API key; actor identifies the verified human.
"""

from typing import Any

from fastapi import Request

from app.models.audit_log import AuditAction, AuditLogDocument, AuditOutcome
from app.models.common import validate_identifier
from app.security.tenant import require_tenant
from app.security.tenant_policy import current_action, current_policy
from app.services.audit_service import AuditService


class StudioAuditWriter:
    def __init__(self, database: Any, policy: Any) -> None:
        self.database, self.policy = database, policy

    async def create(self, document: AuditLogDocument) -> AuditLogDocument:
        if document.org_id != require_tenant().org_id or document.org_id != self.policy.org_id:
            raise ValueError("audit ownership differs from the verified session")
        async with self.policy.engine.domain_fence(self.policy) as tx:
            await self.database.audit_logs.insert_one(
                document.model_dump(by_alias=True), session=tx
            )
        return document


async def record(request: Request, outcome: AuditOutcome) -> None:
    policy = current_policy()
    action = current_action()
    if (
        not policy
        or not policy.managed
        or policy.subject_kind != "human"
        or not policy.engine
        or request.method not in {"POST", "PATCH"}
        or action not in {"creator:write", "content:write", "content:publish"}
    ):
        return
    audit_action: AuditAction = (
        "content.publish"
        if action == "content:publish"
        else "creator.write"
        if action == "creator:write"
        else "content.approve"
        if request.url.path.endswith("/approve")
        else "content.write"
    )
    profile_id = request.path_params.get("profile_id")
    try:
        values = await request.json()
        profile_id = validate_identifier(profile_id or values.get("profile_id", ""))
    except (ValueError, TypeError, AttributeError):
        profile_id = None
    audit = AuditService(StudioAuditWriter(request.app.state.mongodb.database, policy))
    await audit.record(
        audit_action,
        outcome,
        profile_id=profile_id,
        method=request.method,
        path=request.scope["route"].path,
        reason="creator studio operation " + outcome,
    )
