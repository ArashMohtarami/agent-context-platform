"""Administrative client-profile management and safe profile assignment."""

from collections.abc import Sequence
from typing import Protocol

from app.models.agent import AgentClientDocument
from app.models.client_profile import ClientProfileDocument, ClientProfileStatus
from app.models.common import Page
from app.repositories.errors import DuplicateDocumentError
from app.security.tenant_service import tenant_service


class ClientProfileNotFoundError(Exception):
    """The requested reusable onboarding profile does not exist."""


class ClientProfileExistsError(Exception):
    """The supplied profile identifier is already registered."""


class ClientProfileRepository(Protocol):
    async def create(self, document: ClientProfileDocument) -> ClientProfileDocument: ...
    async def get(self, profile_id: str) -> ClientProfileDocument | None: ...
    async def list(
        self, *, limit: int = ..., cursor: str | None = ...
    ) -> Page[ClientProfileDocument]: ...
    async def update(
        self, document: ClientProfileDocument, *, expected_revision: int
    ) -> ClientProfileDocument: ...


class ProfileAssignableClientRepository(Protocol):
    async def get(self, client_id: str) -> AgentClientDocument | None: ...
    async def update(
        self, document: AgentClientDocument, *, expected_revision: int
    ) -> AgentClientDocument: ...


class ClientNotFoundForProfileError(Exception):
    """A profile assignment named no registered agent client."""


@tenant_service
class ClientProfileService:
    """Manage generic profiles without inventing a second authorization path.

    Assigning a profile records its identifier and copies a bounded scope
    snapshot into the existing V4 client. Credentials and gateway middleware
    therefore continue to authorize only the V4 client/credential scopes.
    """

    def __init__(
        self,
        profiles: ClientProfileRepository,
        clients: ProfileAssignableClientRepository,
    ) -> None:
        self._profiles = profiles
        self._clients = clients

    async def create_profile(
        self,
        *,
        profile_id: str,
        name: str,
        scopes: Sequence[str],
        description: str = "Generic MCP client profile",
        rate_limit_profile: str | None = None,
        status: ClientProfileStatus = ClientProfileStatus.ACTIVE,
        metadata: dict[str, object] | None = None,
    ) -> ClientProfileDocument:
        try:
            return await self._profiles.create(
                ClientProfileDocument(
                    profile_id=profile_id,
                    name=name,
                    description=description,
                    scopes=list(scopes),
                    rate_limit_profile=rate_limit_profile,
                    status=status,
                    metadata=metadata or {},
                )
            )
        except DuplicateDocumentError as error:
            raise ClientProfileExistsError("client profile_id is already registered") from error

    async def get_profile(self, profile_id: str) -> ClientProfileDocument:
        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise ClientProfileNotFoundError("client profile was not found")
        return profile

    async def list_profiles(
        self, *, limit: int = 20, cursor: str | None = None
    ) -> Page[ClientProfileDocument]:
        return await self._profiles.list(limit=limit, cursor=cursor)

    async def update_profile(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        name: str | None = None,
        description: str | None = None,
        scopes: Sequence[str] | None = None,
        rate_limit_profile: str | None = None,
        status: ClientProfileStatus | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ClientProfileDocument:
        profile = await self.get_profile(profile_id)
        changes = {
            key: value
            for key, value in {
                "name": name,
                "description": description,
                "scopes": list(scopes) if scopes is not None else None,
                "rate_limit_profile": rate_limit_profile,
                "status": status,
                "metadata": metadata,
            }.items()
            if value is not None
        }
        if not changes:
            raise ValueError("at least one client profile field must be updated")
        return await self._profiles.update(
            profile.model_copy(update=changes), expected_revision=expected_revision
        )

    async def assign_profile(
        self,
        profile_id: str,
        *,
        client_id: str,
        expected_revision: int,
        scope_overrides: Sequence[str] | None = None,
        project_overrides: Sequence[str] | None = None,
    ) -> AgentClientDocument:
        """Assign one profile, keeping V4 authorization a scope snapshot.

        Overrides can only reduce the profile's configured scope set.  This
        prevents a profile-assignment request from becoming a side channel for
        escalating a client beyond the reusable profile it names.
        """
        profile = await self.get_profile(profile_id)
        if profile.status is not ClientProfileStatus.ACTIVE:
            raise ValueError("disabled client profiles cannot be assigned")
        client = await self._clients.get(client_id)
        if client is None:
            raise ClientNotFoundForProfileError("agent client was not found")
        effective_scopes = list(profile.scopes)
        if scope_overrides is not None:
            effective_scopes = list(scope_overrides)
            extra_scopes = sorted(set(effective_scopes) - set(profile.scopes))
            if extra_scopes:
                raise ValueError("profile scope overrides must be a subset of profile scopes")
        effective_projects = list(client.project_ids)
        if project_overrides is not None:
            effective_projects = list(project_overrides)
            if client.project_ids:
                extra_projects = sorted(set(effective_projects) - set(client.project_ids))
                if extra_projects:
                    raise ValueError(
                        "profile project overrides must be a subset of client project access"
                    )
        # Validation on the persisted client rejects unknown/duplicate scopes.
        updated = client.model_copy(
            update={
                "profile_id": profile.profile_id,
                "scopes": effective_scopes,
                "project_ids": effective_projects,
            }
        )
        return await self._clients.update(updated, expected_revision=expected_revision)
