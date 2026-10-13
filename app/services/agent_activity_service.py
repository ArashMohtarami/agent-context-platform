"""Business service for content-free, actor-derived agent activity events."""

from collections.abc import Sequence
from typing import Protocol
from uuid import uuid4

from app.models.agent import AgentActivityDocument, AgentActivityStatus, AgentClientType
from app.models.common import Page
from app.security.principal import Principal


class AgentActivityRepository(Protocol):
    """The append-only write the activity service requires."""

    async def create(self, document: AgentActivityDocument) -> AgentActivityDocument: ...

    async def list(
        self,
        *,
        client_id: str | None = None,
        session_id: str | None = None,
        project_id: str | None = None,
        project_ids: Sequence[str] | None = None,
        tool: str | None = None,
        status: AgentActivityStatus | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[AgentActivityDocument]: ...


class AgentActivityService:
    """Store gateway activity only for an authenticated registered client."""

    def __init__(self, activities: AgentActivityRepository) -> None:
        self._activities = activities

    async def record(
        self,
        principal: Principal,
        *,
        operation: str,
        tool: str | None,
        project_id: str | None,
        status: AgentActivityStatus,
        latency_ms: float,
        session_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> None:
        """Append one event using identity derived from the bearer credential.

        Legacy V1--V3 keys have no client linkage, so they deliberately do not
        gain a fabricated identity or write an activity record in this phase.
        """
        if principal.client_id is None or principal.client_type is None:
            return
        await self._activities.create(
            AgentActivityDocument(
                event_id=uuid4().hex,
                client_id=principal.client_id,
                client_type=AgentClientType(principal.client_type),
                operation=operation,
                tool=tool,
                project_id=project_id,
                session_id=session_id,
                resource_type=resource_type,
                resource_id=resource_id,
                status=status,
                latency_ms=latency_ms,
            )
        )

    async def list(
        self,
        *,
        client_id: str | None = None,
        session_id: str | None = None,
        project_id: str | None = None,
        project_ids: Sequence[str] | None = None,
        tool: str | None = None,
        status: AgentActivityStatus | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[AgentActivityDocument]:
        """Return bounded, content-free events after the route authorizes scope."""
        return await self._activities.list(
            client_id=client_id,
            session_id=session_id,
            project_id=project_id,
            project_ids=project_ids,
            tool=tool,
            status=status,
            limit=limit,
            cursor=cursor,
        )
