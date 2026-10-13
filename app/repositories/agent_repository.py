"""Repository access for V4 Phase 0 agent identity metadata.

These repositories provide persistence and bounded cursor pagination only.
They deliberately do not authenticate a client, resolve a project, create a
session from a gateway request, or expose credential administration.
"""

from collections.abc import Sequence
from datetime import datetime

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.models.agent import (
    AgentActivityDocument,
    AgentActivityStatus,
    AgentClientDocument,
    AgentClientStatus,
    AgentSessionDocument,
    AgentSessionStatus,
    ProjectBindingDocument,
    ProjectBindingType,
)
from app.models.common import Page, deterministic_object_id
from app.repositories.base import DEFAULT_PAGE_SIZE, MongoRepository


class MongoAgentClientRepository(MongoRepository[AgentClientDocument]):
    """Persist registered agent identities independently of their credentials."""

    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        super().__init__(database, "agent_clients", AgentClientDocument)

    async def create(self, document: AgentClientDocument) -> AgentClientDocument:
        return await self._create(document)

    async def get(self, client_id: str) -> AgentClientDocument | None:
        return await self._get({"_id": deterministic_object_id("agent-client", client_id)})

    async def list(
        self,
        *,
        owner_id: str | None = None,
        status: AgentClientStatus | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[AgentClientDocument]:
        filters: MongoDocument = {}
        if owner_id is not None:
            filters["owner_id"] = owner_id
        if status is not None:
            filters["status"] = status
        return await self._page(filters, limit=limit, cursor=cursor)

    async def update(
        self, document: AgentClientDocument, *, expected_revision: int
    ) -> AgentClientDocument:
        return await self._update(document, expected_revision=expected_revision)

    async def touch(self, client_id: str, seen_at: datetime) -> None:
        await self._collection.update_one(
            {"client_id": client_id}, {"$set": {"last_seen_at": seen_at}}
        )


class MongoAgentSessionRepository(MongoRepository[AgentSessionDocument]):
    """Persist lightweight session metadata without retaining conversations."""

    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        super().__init__(database, "agent_sessions", AgentSessionDocument)

    async def create(self, document: AgentSessionDocument) -> AgentSessionDocument:
        return await self._create(document)

    async def get(self, session_id: str) -> AgentSessionDocument | None:
        return await self._get({"session_id": session_id})

    async def update(
        self, document: AgentSessionDocument, *, expected_revision: int
    ) -> AgentSessionDocument:
        """Guard explicit lifecycle and association changes against lost updates."""
        return await self._update(document, expected_revision=expected_revision)

    async def touch_active(self, session_id: str, client_id: str, seen_at: datetime) -> bool:
        """Advance only a live session's heartbeat without overwriting its fields.

        A heartbeat is operational telemetry, not a caller-authored change. It
        intentionally leaves the revision alone so a concurrent heartbeat does
        not manufacture a revision conflict for an explicit metadata update.
        ``$max`` makes parallel gateway requests monotonic.
        """
        result = await self._collection.update_one(
            {
                "session_id": session_id,
                "client_id": client_id,
                "status": AgentSessionStatus.ACTIVE.value,
            },
            {"$max": {"last_seen_at": seen_at}, "$set": {"updated_at": seen_at}},
        )
        return result.matched_count == 1

    async def list(
        self,
        *,
        client_id: str | None = None,
        project_id: str | None = None,
        status: AgentSessionStatus | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[AgentSessionDocument]:
        filters: MongoDocument = {}
        if client_id is not None:
            filters["client_id"] = client_id
        if project_id is not None:
            filters["project_id"] = project_id
        if status is not None:
            filters["status"] = status
        return await self._page(filters, limit=limit, cursor=cursor)


class MongoProjectBindingRepository(MongoRepository[ProjectBindingDocument]):
    """Persist explicit, conflict-free repository-to-project mappings."""

    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        super().__init__(database, "project_bindings", ProjectBindingDocument)

    async def create(self, document: ProjectBindingDocument) -> ProjectBindingDocument:
        return await self._create(document)

    async def get(self, binding_id: str) -> ProjectBindingDocument | None:
        return await self._get({"_id": deterministic_object_id("project-binding", binding_id)})

    async def get_mapping(
        self, binding_type: ProjectBindingType, value: str
    ) -> ProjectBindingDocument | None:
        """Return the single project associated with an exact stable mapping."""
        return await self._get({"binding_type": binding_type, "value": value})

    async def find_mappings(
        self,
        binding_type: ProjectBindingType,
        value: str,
        *,
        project_ids: Sequence[str] | None = None,
    ) -> tuple[ProjectBindingDocument, ...]:
        """Return exact mappings, retaining an ambiguity-safe read path.

        The unique index normally makes this a one-item result.  Returning a
        sequence means a resolver can still report a legacy or manually
        corrupted ambiguous state instead of silently choosing one project.
        """
        return await self._find_exact((binding_type,), value, project_ids=project_ids)

    async def find_repository_mappings(
        self,
        value: str,
        *,
        project_ids: Sequence[str] | None = None,
    ) -> tuple[ProjectBindingDocument, ...]:
        """Find either stored SSH or HTTPS binding for one canonical repository."""
        filters: MongoDocument = {"repository_locator": value}
        if project_ids is not None:
            filters["project_id"] = {"$in": list(project_ids)}
        cursor = self._collection.find(filters)
        raw_documents = await cursor.to_list(length=None)
        return tuple(self._validate(raw) for raw in raw_documents)

    async def list(
        self,
        *,
        project_id: str | None = None,
        project_ids: Sequence[str] | None = None,
        binding_type: ProjectBindingType | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ProjectBindingDocument]:
        filters: MongoDocument = {}
        if project_id is not None:
            filters["project_id"] = project_id
        elif project_ids is not None:
            filters["project_id"] = {"$in": list(project_ids)}
        if binding_type is not None:
            filters["binding_type"] = binding_type
        return await self._page(filters, limit=limit, cursor=cursor)

    async def update(
        self, document: ProjectBindingDocument, *, expected_revision: int
    ) -> ProjectBindingDocument:
        """Update a binding with its current revision as a lost-update guard."""
        return await self._update(document, expected_revision=expected_revision)

    async def delete(self, binding_id: str, *, expected_revision: int) -> bool:
        """Delete only the exact revision the caller reviewed."""
        return await self._delete(
            deterministic_object_id("project-binding", binding_id),
            expected_revision=expected_revision,
        )

    async def _find_exact(
        self,
        binding_types: tuple[ProjectBindingType, ...],
        value: str,
        *,
        project_ids: Sequence[str] | None,
    ) -> tuple[ProjectBindingDocument, ...]:
        filters: MongoDocument = {
            "binding_type": {"$in": [binding_type.value for binding_type in binding_types]},
            "value": value,
        }
        if project_ids is not None:
            filters["project_id"] = {"$in": list(project_ids)}
        cursor = self._collection.find(filters)
        raw_documents = await cursor.to_list(length=None)
        return tuple(self._validate(raw) for raw in raw_documents)


class MongoAgentActivityRepository(MongoRepository[AgentActivityDocument]):
    """Append and paginate content-free agent activity records."""

    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        super().__init__(database, "agent_activity", AgentActivityDocument)

    async def create(self, document: AgentActivityDocument) -> AgentActivityDocument:
        return await self._create(document)

    async def get(self, event_id: str) -> AgentActivityDocument | None:
        return await self._get({"event_id": event_id})

    async def list(
        self,
        *,
        client_id: str | None = None,
        session_id: str | None = None,
        project_id: str | None = None,
        project_ids: Sequence[str] | None = None,
        tool: str | None = None,
        status: AgentActivityStatus | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[AgentActivityDocument]:
        filters: MongoDocument = {}
        if client_id is not None:
            filters["client_id"] = client_id
        if session_id is not None:
            filters["session_id"] = session_id
        if project_id is not None:
            filters["project_id"] = project_id
        elif project_ids is not None:
            # Events not attached to a project are global operational facts;
            # keep them visible beside only the projects in the caller's V1
            # grant rather than turning an unscoped activity read into all
            # project activity.
            filters["$or"] = [
                {"project_id": None},
                {"project_id": {"$in": list(project_ids)}},
            ]
        if tool is not None:
            filters["tool"] = tool
        if status is not None:
            filters["status"] = status
        return await self._page(filters, limit=limit, cursor=cursor)
