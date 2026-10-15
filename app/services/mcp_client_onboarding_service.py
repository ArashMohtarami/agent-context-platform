"""Administrative composition for onboarding future MCP-compatible clients.

This service deliberately delegates identity, profile assignment, and
credential issuance to the existing V4/V5 services.  It does not expose an
MCP tool, touch a knowledge repository, or create a client-specific policy
path.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from app.integrations import DEFAULT_INTEGRATION_REGISTRY
from app.integrations.base import (
    ConnectionValidation,
    IntegrationInstructions,
    IntegrationRegistry,
    contains_literal_agent_context_token,
)
from app.models.agent import AgentClientDocument, AgentClientType
from app.models.client_profile import ClientProfileDocument
from app.security.tenant_service import tenant_service
from app.services.api_key_service import IssuedKey


class AgentClientAdministrator(Protocol):
    """The existing administrative surface used by onboarding."""

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
    ) -> AgentClientDocument: ...

    async def create_credential(
        self,
        client_id: str,
        *,
        scopes: Sequence[str] | None = None,
        projects: Sequence[str] | None = None,
        expires_in_days: int | None = None,
    ) -> IssuedKey: ...


class ClientProfileAdministrator(Protocol):
    """The profile surface already responsible for bounded scope snapshots."""

    async def get_profile(self, profile_id: str) -> ClientProfileDocument: ...

    async def assign_profile(
        self,
        profile_id: str,
        *,
        client_id: str,
        expected_revision: int,
        scope_overrides: Sequence[str] | None = None,
        project_overrides: Sequence[str] | None = None,
    ) -> AgentClientDocument: ...


@tenant_service
class McpClientOnboardingService:
    """Onboard a generic MCP client through existing administrative services."""

    def __init__(
        self,
        clients: AgentClientAdministrator,
        *,
        profiles: ClientProfileAdministrator | None = None,
        integrations: IntegrationRegistry = DEFAULT_INTEGRATION_REGISTRY,
    ) -> None:
        self._clients = clients
        self._profiles = profiles
        self._integrations = integrations

    async def register_mcp_client(
        self,
        *,
        integration_id: str,
        client_id: str,
        name: str,
        owner_id: str,
        project_ids: Sequence[str] = (),
        profile_id: str | None = None,
        scope_overrides: Sequence[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AgentClientDocument:
        """Register one client and optionally snapshot a reusable profile.

        A profile is consulted only during this administrative operation.  Its
        scopes are copied onto the V4 client, so later profile edits never
        silently alter a credential's authorization.
        """
        integration = self._integrations.get(integration_id)
        if profile_id is None:
            if scope_overrides is None or not scope_overrides:
                raise ValueError("registering a client requires a profile or explicit scopes")
            return await self._clients.create_client(
                client_id=client_id,
                client_type=integration.metadata.client_type,
                name=name,
                owner_id=owner_id,
                scopes=scope_overrides,
                project_ids=project_ids,
                metadata=metadata,
            )

        profiles = self._profiles
        if profiles is None:
            raise ValueError("a client profile service is required for profile assignment")
        profile = await profiles.get_profile(profile_id)
        effective_scopes = tuple(profile.scopes if scope_overrides is None else scope_overrides)
        extra_scopes = sorted(set(effective_scopes) - set(profile.scopes))
        if extra_scopes:
            raise ValueError("profile scope overrides must be a subset of profile scopes")
        created = await self._clients.create_client(
            client_id=client_id,
            client_type=integration.metadata.client_type,
            name=name,
            owner_id=owner_id,
            scopes=effective_scopes,
            project_ids=project_ids,
            metadata=metadata,
        )
        return await profiles.assign_profile(
            profile.profile_id,
            client_id=created.client_id,
            expected_revision=created.revision,
            scope_overrides=effective_scopes,
            project_overrides=tuple(project_ids) if project_ids else None,
        )

    async def assign_client_profile(
        self,
        *,
        client_id: str,
        profile_id: str,
        expected_revision: int,
        scope_overrides: Sequence[str] | None = None,
        project_overrides: Sequence[str] | None = None,
    ) -> AgentClientDocument:
        """Delegate a bounded profile assignment to the existing profile service."""
        if self._profiles is None:
            raise ValueError("a client profile service is required for profile assignment")
        return await self._profiles.assign_profile(
            profile_id,
            client_id=client_id,
            expected_revision=expected_revision,
            scope_overrides=scope_overrides,
            project_overrides=project_overrides,
        )

    async def issue_client_credential(
        self,
        *,
        client_id: str,
        scopes: Sequence[str] | None = None,
        projects: Sequence[str] | None = None,
        expires_in_days: int | None = None,
    ) -> IssuedKey:
        """Issue through the existing administrative credential service only."""
        return await self._clients.create_credential(
            client_id,
            scopes=scopes,
            projects=projects,
            expires_in_days=expires_in_days,
        )

    def generate_integration_instructions(self, integration_id: str) -> IntegrationInstructions:
        """Return client-safe instructions and reject accidental literal credentials."""
        instructions = self._integrations.get(integration_id).instructions()
        if contains_literal_agent_context_token(instructions.as_dict()):
            raise RuntimeError("integration instructions must not contain a credential")
        return instructions

    def validate_client_connection(
        self,
        integration_id: str,
        values: Mapping[str, str] | None = None,
    ) -> ConnectionValidation:
        """Run local, credential-redacting preflight diagnostics only."""
        return self._integrations.get(integration_id).validate(values)


__all__ = ["McpClientOnboardingService"]
