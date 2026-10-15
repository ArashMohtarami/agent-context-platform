"""V5 Phase 6 generic MCP client onboarding contracts."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.integrations import DEFAULT_INTEGRATION_REGISTRY
from app.integrations.base import MCP_URL_ENV, TOKEN_ENV, contains_literal_agent_context_token
from app.models.agent import AgentClientDocument, AgentClientType
from app.models.client_profile import ClientProfileDocument
from app.services.api_key_service import IssuedKey
from app.services.mcp_client_onboarding_service import McpClientOnboardingService
from tests.security_doubles import issued_key


class Agents:
    """Record administrative calls without exposing a credential through MCP."""

    def __init__(self) -> None:
        self.records: dict[str, AgentClientDocument] = {}
        self.credential_requests: list[dict[str, object]] = []

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
        record = AgentClientDocument(
            org_id="org-test",
            project_mode="include" if list(project_ids) else "all_in_org",
            client_id=client_id,
            client_type=client_type,
            name=name,
            owner_id=owner_id,
            scopes=list(scopes),
            project_ids=list(project_ids),
            metadata=metadata or {},
        )
        self.records[client_id] = record
        return record

    async def create_credential(
        self,
        client_id: str,
        *,
        scopes: Sequence[str] | None = None,
        projects: Sequence[str] | None = None,
        expires_in_days: int | None = None,
    ) -> IssuedKey:
        self.credential_requests.append(
            {
                "client_id": client_id,
                "scopes": None if scopes is None else tuple(scopes),
                "projects": None if projects is None else tuple(projects),
                "expires_in_days": expires_in_days,
            }
        )
        record, token = issued_key(scopes=("search:read",))
        return IssuedKey(record=record.model_copy(update={"client_id": client_id}), token=token)


class Profiles:
    """Record bounded profile assignment through the existing client record."""

    def __init__(self, agents: Agents) -> None:
        self._agents = agents
        self.profile = ClientProfileDocument(
            profile_id="coding-agent-default",
            name="Coding agent",
            scopes=["search:read", "context:read", "context:write"],
        )
        self.assignments: list[dict[str, object]] = []

    async def get_profile(self, profile_id: str) -> ClientProfileDocument:
        assert profile_id == self.profile.profile_id
        return self.profile

    async def assign_profile(
        self,
        profile_id: str,
        *,
        client_id: str,
        expected_revision: int,
        scope_overrides: Sequence[str] | None = None,
        project_overrides: Sequence[str] | None = None,
    ) -> AgentClientDocument:
        self.assignments.append(
            {
                "profile_id": profile_id,
                "client_id": client_id,
                "expected_revision": expected_revision,
                "scope_overrides": None if scope_overrides is None else tuple(scope_overrides),
                "project_overrides": (
                    None if project_overrides is None else tuple(project_overrides)
                ),
            }
        )
        current = self._agents.records[client_id]
        return current.model_copy(
            update={
                "profile_id": profile_id,
                "scopes": list(scope_overrides or self.profile.scopes),
                "project_ids": list(project_overrides or current.project_ids),
                "revision": expected_revision + 1,
            }
        )


def test_registry_describes_all_supported_client_types_without_client_specific_tools() -> None:
    records = DEFAULT_INTEGRATION_REGISTRY.list()

    assert [item.integration_id for item in records] == [
        "claude-code",
        "codex",
        "custom",
        "gemini-cli",
    ]
    assert {item.client_type for item in records} == {
        AgentClientType.CODEX,
        AgentClientType.CLAUDE_CODE,
        AgentClientType.GEMINI_CLI,
        AgentClientType.CUSTOM,
    }
    assert all("search logic" not in " ".join(item.compatibility).lower() for item in records)


def test_instruction_generation_is_safe_and_existing_client_docs_are_addressable() -> None:
    for metadata in DEFAULT_INTEGRATION_REGISTRY.list():
        instructions = DEFAULT_INTEGRATION_REGISTRY.get(metadata.integration_id).instructions()
        payload = instructions.as_dict()
        rendered = json.dumps(payload)

        assert instructions.metadata == metadata
        assert instructions.metadata.documentation_path
        assert Path(instructions.metadata.documentation_path).is_file()
        assert contains_literal_agent_context_token(payload) is False
        assert "acp_" not in rendered
        assert "token:" not in rendered.lower()


def test_remote_diagnostics_are_local_redacting_and_reject_provider_credentials() -> None:
    token = issued_key(scopes=("search:read",))[1]
    diagnostics = DEFAULT_INTEGRATION_REGISTRY.get("gemini-cli").validate(
        {MCP_URL_ENV: "https://agents.example.test/mcp", TOKEN_ENV: token}
    )
    refused = DEFAULT_INTEGRATION_REGISTRY.get("custom").validate(
        {MCP_URL_ENV: "https://agents.example.test/mcp", TOKEN_ENV: "AIza-provider-key"}
    )

    assert diagnostics.ready is True
    assert diagnostics.as_dict()["network_checked"] is False
    assert token not in json.dumps(diagnostics.as_dict())
    assert refused.ready is False
    assert {check.status for check in refused.checks} == {"ok", "invalid"}


@pytest.mark.asyncio
async def test_onboarding_composes_existing_admin_services_without_a_new_credential_path() -> None:
    agents = Agents()
    profiles = Profiles(agents)
    service = McpClientOnboardingService(agents, profiles=profiles)

    registered = await service.register_mcp_client(
        integration_id="custom",
        client_id="review-agent",
        name="Review agent",
        owner_id="operator",
        project_ids=("payment-service",),
        profile_id="coding-agent-default",
        scope_overrides=("search:read", "context:read"),
    )
    issued = await service.issue_client_credential(
        client_id=registered.client_id,
        scopes=("search:read",),
        projects=("payment-service",),
        expires_in_days=30,
    )

    assert registered.client_type is AgentClientType.CUSTOM
    assert registered.profile_id == "coding-agent-default"
    assert registered.scopes == ["search:read", "context:read"]
    assert profiles.assignments == [
        {
            "profile_id": "coding-agent-default",
            "client_id": "review-agent",
            "expected_revision": 1,
            "scope_overrides": ("search:read", "context:read"),
            "project_overrides": ("payment-service",),
        }
    ]
    assert agents.credential_requests == [
        {
            "client_id": "review-agent",
            "scopes": ("search:read",),
            "projects": ("payment-service",),
            "expires_in_days": 30,
        }
    ]
    assert issued.record.client_id == "review-agent"
    assert (
        service.generate_integration_instructions("custom").metadata.client_type
        is AgentClientType.CUSTOM
    )

    with pytest.raises(ValueError, match="subset"):
        await service.register_mcp_client(
            integration_id="custom",
            client_id="escalated-agent",
            name="Escalated agent",
            owner_id="operator",
            profile_id="coding-agent-default",
            scope_overrides=("research:promote",),
        )


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing domain behavior uses an explicit test organization."""
    yield
