"""V4 Phase 12 named multi-agent acceptance fixture.

The wider Phase 12 scenarios intentionally live in focused integration suites:
remote HTTP transport, gateway behavior, project resolution, and backup/restart
all need different infrastructure conditions.  This test provisions the exact
identities from the acceptance specification against live MongoDB, proves their
credential isolation and deterministic project bindings, and cleans up only
the documents it owns.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.models.agent import AgentClientType, ProjectBindingType
from app.models.project import ProjectDocument
from app.repositories.agent_repository import (
    MongoAgentClientRepository,
    MongoProjectBindingRepository,
)
from app.repositories.api_key_repository import MongoApiKeyRepository
from app.repositories.project_repository import MongoProjectRepository
from app.security.principal import AuthenticationError
from app.services.agent_client_service import AgentClientService
from app.services.api_key_service import ApiKeyService
from app.services.auth_service import AuthService
from app.services.project_binding_service import ProjectBindingService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

CODEX_CLIENT_ID = "codex-main"
CLAUDE_CLIENT_ID = "claude-code-main"
PAYMENT_SERVICE = "payment-service"
CLAUDE_DEFAULT_SCOPES = (
    "skills:read",
    "projects:read",
    "context:read",
    "context:write",
    "memory:read",
    "memory:write",
    "search:read",
    "research:read",
    "research:write",
    "agent:self",
    "session:write",
    "audit:self:read",
)


@asynccontextmanager
async def initialized_mongodb() -> AsyncIterator[MongoDBClient]:
    mongodb = MongoDBClient(get_settings())
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        yield mongodb
    finally:
        await mongodb.close()


@pytest.mark.asyncio
async def test_phase12_named_clients_have_independent_credentials_and_payment_binding() -> None:
    """Provision the documented identities without retaining any plaintext secret."""
    binding_ids: list[str] = []
    key_ids: list[str] = []
    client_ids = (CODEX_CLIENT_ID, CLAUDE_CLIENT_ID)
    created_project = False

    async with initialized_mongodb() as mongodb:
        database = mongodb.database
        existing_clients = await database["agent_clients"].count_documents(
            {"client_id": {"$in": client_ids}}
        )
        if existing_clients:
            pytest.skip(
                "Phase 12 named acceptance client identities already exist outside this test"
            )

        projects = MongoProjectRepository(database)
        bindings = ProjectBindingService(projects, MongoProjectBindingRepository(database))
        agents = AgentClientService(
            MongoAgentClientRepository(database), ApiKeyService(MongoApiKeyRepository(database))
        )
        try:
            if await projects.get(PAYMENT_SERVICE) is None:
                await projects.create(
                    ProjectDocument(
                        project_id=PAYMENT_SERVICE,
                        name="Payment Service",
                        description="V4 Phase 12 multi-agent acceptance fixture",
                        repository="https://github.com/agent-context-fixtures/payment-service.git",
                        environment="test",
                    )
                )
                created_project = True
            for binding_type, value in (
                (
                    ProjectBindingType.GIT_REMOTE,
                    "git@github.com:agent-context-fixtures/payment-service.git",
                ),
                (ProjectBindingType.REPOSITORY_NAME, "payment-service"),
                (ProjectBindingType.WORKSPACE_ALIAS, "payment-service-workspace"),
            ):
                binding = await bindings.create_binding(
                    project_id=PAYMENT_SERVICE,
                    binding_type=binding_type,
                    value=value,
                )
                binding_ids.append(binding.binding_id)

            codex = await agents.create_client(
                client_id=CODEX_CLIENT_ID,
                client_type=AgentClientType.CODEX,
                name="Codex Phase 12 acceptance client",
                owner_id="phase12-integration",
                scopes=CLAUDE_DEFAULT_SCOPES,
                project_ids=[PAYMENT_SERVICE],
            )
            claude = await agents.create_client(
                client_id=CLAUDE_CLIENT_ID,
                client_type=AgentClientType.CLAUDE_CODE,
                name="Claude Code Phase 12 acceptance client",
                owner_id="phase12-integration",
                scopes=CLAUDE_DEFAULT_SCOPES,
                project_ids=[PAYMENT_SERVICE],
            )
            codex_key = await agents.create_credential(codex.client_id)
            claude_key = await agents.create_credential(claude.client_id)
            key_ids.extend((codex_key.record.key_id, claude_key.record.key_id))

            auth = AuthService(
                MongoApiKeyRepository(database), MongoAgentClientRepository(database)
            )
            codex_principal = await auth.authenticate(codex_key.token)
            claude_principal = await auth.authenticate(claude_key.token)
            ssh = await bindings.resolve_project(
                git_remote="git@github.com:agent-context-fixtures/payment-service.git",
                allowed_project_ids=claude_principal.projects,
            )
            https = await bindings.resolve_project(
                repository_url="https://github.com/agent-context-fixtures/payment-service.git",
                allowed_project_ids=claude_principal.projects,
            )
            name = await bindings.resolve_project(
                repository_name="payment-service", allowed_project_ids=claude_principal.projects
            )
            workspace = await bindings.resolve_project(
                workspace_hint="payment-service-workspace",
                allowed_project_ids=claude_principal.projects,
            )

            assert codex_principal.client_id == CODEX_CLIENT_ID
            assert claude_principal.client_id == CLAUDE_CLIENT_ID
            assert codex_key.token != claude_key.token
            assert claude_key.token not in claude_key.record.model_dump_json()
            assert set(CLAUDE_DEFAULT_SCOPES) == set(claude_principal.scopes)
            assert not {
                "agent:manage",
                "research:approve",
                "research:promote",
                "content:publish",
            } & set(claude_principal.scopes)
            assert all(
                outcome.project_id == PAYMENT_SERVICE for outcome in (ssh, https, name, workspace)
            )

            await agents.revoke_client(claude.client_id)
            with pytest.raises(AuthenticationError):
                await auth.authenticate(claude_key.token)
            assert (await auth.authenticate(codex_key.token)).client_id == CODEX_CLIENT_ID
        finally:
            await database["api_keys"].delete_many({"key_id": {"$in": key_ids}})
            await database["agent_clients"].delete_many({"client_id": {"$in": client_ids}})
            await database["project_bindings"].delete_many({"binding_id": {"$in": binding_ids}})
            if created_project:
                await database["projects"].delete_many({"project_id": PAYMENT_SERVICE})
