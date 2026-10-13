"""V5 Phase 0 generic client-profile and Gemini identity contracts."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.agent import AgentClientDocument, AgentClientType
from app.models.client_profile import (
    GEMINI_DEFAULT_PROFILE_ID,
    GEMINI_DEFAULT_SCOPES,
    ClientProfileDocument,
    ClientProfileStatus,
    recommended_client_profiles,
)
from app.models.common import Page
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError
from app.services.client_profile_service import ClientProfileService


class Profiles:
    """Small in-memory profile store with the production revision contract."""

    def __init__(self) -> None:
        self.records: dict[str, ClientProfileDocument] = {}

    async def create(self, document: ClientProfileDocument) -> ClientProfileDocument:
        if document.profile_id in self.records:
            raise DuplicateDocumentError("duplicate")
        self.records[document.profile_id] = document
        return document

    async def get(self, profile_id: str) -> ClientProfileDocument | None:
        return self.records.get(profile_id)

    async def list(
        self, *, limit: int = 20, cursor: str | None = None
    ) -> Page[ClientProfileDocument]:
        del cursor
        return Page(items=tuple(self.records.values())[:limit])

    async def update(
        self, document: ClientProfileDocument, *, expected_revision: int
    ) -> ClientProfileDocument:
        current = self.records.get(document.profile_id)
        if current is None or current.revision != expected_revision:
            raise ConcurrencyConflictError("changed")
        stored = document.model_copy(update={"revision": expected_revision + 1})
        self.records[stored.profile_id] = stored
        return stored


class Clients:
    """In-memory V4 client storage used by profile assignment tests."""

    def __init__(self, *clients: AgentClientDocument) -> None:
        self.records = {client.client_id: client for client in clients}

    async def get(self, client_id: str) -> AgentClientDocument | None:
        return self.records.get(client_id)

    async def update(
        self, document: AgentClientDocument, *, expected_revision: int
    ) -> AgentClientDocument:
        current = self.records.get(document.client_id)
        if current is None or current.revision != expected_revision:
            raise ConcurrencyConflictError("changed")
        stored = document.model_copy(update={"revision": expected_revision + 1})
        self.records[stored.client_id] = stored
        return stored

    async def touch(self, client_id: str, seen_at: datetime) -> None:
        """Match the existing V4 authentication reader without changing state."""
        _ = client_id, seen_at


def gemini_client() -> AgentClientDocument:
    return AgentClientDocument(
        client_id="gemini-cli-main",
        client_type=AgentClientType.GEMINI_CLI,
        name="Gemini CLI",
        owner_id="operator",
        scopes=["search:read"],
        project_ids=["payments"],
    )


def existing_client(client_id: str, client_type: AgentClientType) -> AgentClientDocument:
    """Represent an unchanged V4 client beside the new Gemini identity."""
    return AgentClientDocument(
        client_id=client_id,
        client_type=client_type,
        name=client_id.replace("-", " ").title(),
        owner_id="operator",
        scopes=["skills:read", "search:read"],
        project_ids=["ledger"],
    )


def test_gemini_is_a_descriptive_client_type_and_client_profiles_hold_no_credentials() -> None:
    client = gemini_client()
    profile = ClientProfileDocument(
        profile_id="coding-agent-default",
        name="Default Coding Agent",
        scopes=["skills:read", "projects:read", "search:read"],
        rate_limit_profile="standard",
    )

    assert client.client_type is AgentClientType.GEMINI_CLI
    assert client.profile_id is None
    assert (
        profile.id
        == ClientProfileDocument(
            profile_id="coding-agent-default",
            name="Default Coding Agent",
            scopes=["skills:read", "projects:read", "search:read"],
        ).id
    )
    serialized = profile.model_dump(mode="json")
    assert "token" not in serialized
    assert "credential" not in serialized


def test_recommended_profiles_include_the_exact_gemini_default_and_no_high_risk_grants() -> None:
    profiles = recommended_client_profiles()

    assert [profile.profile_id for profile in profiles] == [
        "coding-agent-default",
        "coding-agent-readonly",
        "research-agent",
        "documentation-agent",
        "creator-agent",
    ]
    gemini_default = next(
        profile for profile in profiles if profile.profile_id == GEMINI_DEFAULT_PROFILE_ID
    )
    assert gemini_default.scopes == GEMINI_DEFAULT_SCOPES

    high_risk_scopes = {
        "agent:manage",
        "research:approve",
        "research:promote",
        "content:publish",
    }
    assert all(high_risk_scopes.isdisjoint(profile.scopes) for profile in profiles)


def test_existing_codex_and_claude_client_types_remain_valid() -> None:
    codex = existing_client("codex-main", AgentClientType.CODEX)
    claude = existing_client("claude-main", AgentClientType.CLAUDE_CODE)

    assert codex.client_type is AgentClientType.CODEX
    assert claude.client_type is AgentClientType.CLAUDE_CODE
    assert codex.profile_id is None
    assert claude.profile_id is None


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"scopes": ["search:read", "search:read"]}, "scopes must be unique"),
        ({"scopes": ["unknown:scope"]}, "unknown scopes"),
        ({"metadata": {"token": "do-not-store"}}, "must not include token"),
        ({"metadata": {"nested": {"not": "allowed"}}}, "small scalar"),
    ],
)
def test_client_profiles_reject_ambiguous_or_secret_like_configuration(
    values: dict[str, object], message: str
) -> None:
    profile_values: dict[str, object] = {
        "profile_id": "coding-agent-default",
        "name": "Default Coding Agent",
        "scopes": ["search:read"],
    }
    profile_values.update(values)
    with pytest.raises(ValidationError, match=message):
        ClientProfileDocument(**profile_values)


@pytest.mark.asyncio
async def test_profile_assignment_copies_a_non_escalating_scope_snapshot() -> None:
    clients = Clients(gemini_client())
    service = ClientProfileService(Profiles(), clients)
    profile = await service.create_profile(
        profile_id="coding-agent-default",
        name="Default Coding Agent",
        scopes=["skills:read", "projects:read", "search:read", "context:read"],
    )

    assigned = await service.assign_profile(
        profile.profile_id,
        client_id="gemini-cli-main",
        expected_revision=1,
        scope_overrides=["projects:read", "search:read"],
        project_overrides=["payments"],
    )

    assert assigned.profile_id == profile.profile_id
    assert assigned.scopes == ["projects:read", "search:read"]
    assert assigned.project_ids == ["payments"]
    assert assigned.revision == 2

    with pytest.raises(ValueError, match="subset"):
        await service.assign_profile(
            profile.profile_id,
            client_id="gemini-cli-main",
            expected_revision=2,
            scope_overrides=["research:promote"],
        )

    with pytest.raises(ValueError, match="project access"):
        await service.assign_profile(
            profile.profile_id,
            client_id="gemini-cli-main",
            expected_revision=2,
            project_overrides=["another-project"],
        )

    changed = await service.update_profile(
        profile.profile_id,
        expected_revision=1,
        scopes=["search:read"],
    )
    assert changed.scopes == ["search:read"]
    # Editing a template never silently changes a connected Gemini client.
    assert clients.records["gemini-cli-main"].scopes == ["projects:read", "search:read"]


@pytest.mark.asyncio
async def test_disabled_profiles_cannot_change_existing_clients() -> None:
    gemini = gemini_client()
    codex = existing_client("codex-main", AgentClientType.CODEX)
    claude = existing_client("claude-main", AgentClientType.CLAUDE_CODE)
    clients = Clients(gemini, codex, claude)
    service = ClientProfileService(Profiles(), clients)
    profile = await service.create_profile(
        profile_id="retired-profile",
        name="Retired profile",
        scopes=["search:read"],
        status=ClientProfileStatus.DISABLED,
    )

    with pytest.raises(ValueError, match="disabled"):
        await service.assign_profile(
            profile.profile_id,
            client_id=gemini.client_id,
            expected_revision=gemini.revision,
        )

    assert clients.records[codex.client_id] == codex
    assert clients.records[claude.client_id] == claude


@pytest.mark.asyncio
async def test_profile_assignment_uses_the_existing_client_revision_guard() -> None:
    clients = Clients(gemini_client())
    service = ClientProfileService(Profiles(), clients)
    await service.create_profile(
        profile_id="coding-agent-default",
        name="Default Coding Agent",
        scopes=["search:read"],
    )

    with pytest.raises(ConcurrencyConflictError):
        await service.assign_profile(
            "coding-agent-default",
            client_id="gemini-cli-main",
            expected_revision=2,
        )


def test_client_profile_timestamp_contract_is_preserved() -> None:
    created_at = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    profile = ClientProfileDocument(
        profile_id="readonly",
        name="Read only",
        scopes=["search:read"],
        created_at=created_at,
        updated_at=created_at,
    )

    assert profile.created_at == profile.updated_at
