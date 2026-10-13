"""Recording authorization decisions.

What is recorded is who asked, for what, and what was decided. Never what came
back: an audit trail that copied retrieved knowledge would double what a leak
exposes while telling an investigator nothing the records themselves cannot.

An audit write never fails the request it describes. A refusal that was
correctly refused should not become a 500 because the trail was unwritable, and
a success should not be undone by one either — the failure is logged for the
operator instead.
"""

import logging
from typing import Protocol

from app.models.audit_log import AuditAction, AuditLogDocument, AuditOutcome
from app.provenance import actor_from_principal
from app.provenance import current as current_actor
from app.security.principal import Principal

logger = logging.getLogger("agent_context.security")


class AuditWriter(Protocol):
    """The one write an audit trail needs."""

    async def create(self, document: AuditLogDocument) -> AuditLogDocument: ...


class AuditService:
    """Append authorization decisions to the trail."""

    def __init__(self, records: AuditWriter | None) -> None:
        self._records = records

    async def record(
        self,
        action: AuditAction,
        outcome: AuditOutcome,
        *,
        principal: Principal | None = None,
        project_id: str | None = None,
        profile_id: str | None = None,
        research_project_id: str | None = None,
        scope: str | None = None,
        method: str | None = None,
        path: str | None = None,
        request_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Record one decision, surviving a trail that cannot be written."""
        if self._records is None:
            return
        try:
            await self._records.create(
                AuditLogDocument(
                    action=action,
                    outcome=outcome,
                    key_id=principal.key_id if principal else "unauthenticated",
                    key_name=principal.name if principal else None,
                    project_id=project_id,
                    profile_id=profile_id,
                    research_project_id=research_project_id,
                    scope=scope,
                    method=method,
                    path=path,
                    request_id=request_id,
                    reason=reason,
                    actor=(
                        actor_from_principal(principal)
                        if principal is not None
                        else current_actor()
                    ),
                )
            )
        # Deliberately broad: an audit record is a description of something
        # that already happened, and no failure to describe it is worth
        # changing the outcome it describes.
        except Exception as error:
            logger.warning(
                "audit_write_failed",
                extra={"auth_reason": type(error).__name__, "key_id": _key_id(principal)},
            )


def _key_id(principal: Principal | None) -> str:
    """Name the caller for a log line, without a credential."""
    return principal.key_id if principal else "unauthenticated"
