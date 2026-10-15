"""V4 Phase 1 administration over agent identities and existing API keys."""

from collections.abc import Sequence
from typing import Protocol

from app.models.agent import AgentClientDocument, AgentClientStatus, AgentClientType
from app.models.common import Page
from app.security.tenant_service import tenant_service
from app.services.api_key_service import ApiKeyService, IssuedKey


class AgentClientNotFoundError(Exception):
    """The requested client identity is absent."""


class AgentClientExistsError(Exception):
    """The client identifier already belongs to a registered client."""


class AgentClientRepository(Protocol):
    async def create(self, document: AgentClientDocument) -> AgentClientDocument: ...
    async def get(self, client_id: str) -> AgentClientDocument | None: ...
    async def list(
        self, *, status: AgentClientStatus | None = ..., limit: int = ..., cursor: str | None = ...
    ) -> Page[AgentClientDocument]: ...
    async def update(
        self, document: AgentClientDocument, *, expected_revision: int
    ) -> AgentClientDocument: ...


@tenant_service
class AgentClientService:
    """Create and administer clients without exposing raw credentials again."""

    def __init__(self, clients: AgentClientRepository, keys: ApiKeyService) -> None:
        self._clients = clients
        self._keys = keys

    async def create_client(
        self,
        *,
        client_id: str,
        client_type: AgentClientType,
        name: str,
        owner_id: str,
        scopes: Sequence[str] = (),
        project_ids: Sequence[str] = (),
        metadata: dict[str, object] | None = None,
    ) -> AgentClientDocument:
        from app.repositories.errors import DuplicateDocumentError

        try:
            return await self._clients.create(
                AgentClientDocument(
                    project_mode="include" if project_ids else "all_in_org",
                    client_id=client_id,
                    client_type=client_type,
                    name=name,
                    owner_id=owner_id,
                    scopes=list(scopes),
                    project_ids=list(project_ids),
                    metadata=metadata or {},
                )
            )
        except DuplicateDocumentError as error:
            raise AgentClientExistsError("client_id is already registered") from error

    async def get_client(self, client_id: str) -> AgentClientDocument:
        client = await self._clients.get(client_id)
        if client is None:
            raise AgentClientNotFoundError("agent client was not found")
        return client

    async def list_clients(
        self, *, status: AgentClientStatus | None = None, limit: int = 20, cursor: str | None = None
    ) -> Page[AgentClientDocument]:
        return await self._clients.list(status=status, limit=limit, cursor=cursor)

    async def update_client(
        self,
        client_id: str,
        *,
        expected_revision: int,
        name: str | None = None,
        status: AgentClientStatus | None = None,
        scopes: Sequence[str] | None = None,
        project_ids: Sequence[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AgentClientDocument:
        client = await self.get_client(client_id)
        changes = {
            key: value
            for key, value in {
                "name": name,
                "status": status,
                "scopes": list(scopes) if scopes is not None else None,
                "project_ids": list(project_ids) if project_ids is not None else None,
                "metadata": metadata,
            }.items()
            if value is not None
        }
        if project_ids is not None:
            changes["project_mode"] = "include" if project_ids else "all_in_org"
        if not changes:
            raise ValueError("at least one client field must be updated")
        return await self._clients.update(
            client.model_copy(update=changes), expected_revision=expected_revision
        )

    async def create_credential(
        self,
        client_id: str,
        *,
        scopes: Sequence[str] | None = None,
        projects: Sequence[str] | None = None,
        expires_in_days: int | None = None,
    ) -> IssuedKey:
        client = await self.get_client(client_id)
        if client.status != "active":
            raise ValueError("credentials can only be issued to an active client")
        granted = list(client.scopes) if scopes is None else list(scopes)
        if not granted:
            raise ValueError("agent client needs at least one scope")
        if not set(granted).issubset(client.scopes):
            raise ValueError("credential scopes must be granted to the client")
        allowed_projects = list(client.project_ids) if projects is None else list(projects)
        if client.project_ids and not set(allowed_projects).issubset(client.project_ids):
            raise ValueError("credential projects must be allowed for the client")
        return await self._keys.create_key(
            name=f"agent:{client_id}",
            scopes=granted,
            projects=allowed_projects,
            client_id=client_id,
            expires_in_days=expires_in_days,
        )

    async def revoke_client(self, client_id: str) -> AgentClientDocument:
        client = await self.get_client(client_id)
        updated = await self._clients.update(
            client.model_copy(update={"status": AgentClientStatus.REVOKED}),
            expected_revision=client.revision,
        )
        cursor: str | None = None
        while True:
            page = await self._keys.list_keys(client_id=client_id, limit=50, cursor=cursor)
            for key in page.items:
                if key.status == "active":
                    await self._keys.revoke_key(key.key_id)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        return updated
