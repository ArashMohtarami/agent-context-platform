"""V5 Phase 6 client-neutral MCP compatibility contract over the real gateway."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from bson import ObjectId
from fastapi import FastAPI
from mcp.shared.memory import create_connected_server_and_client_session

from app.core.config import get_settings
from app.embeddings.types import EmbeddingVector
from app.main import create_app
from app.mcp.auth import Authorization
from app.mcp.client import ContextApiClient
from app.mcp.gateway_controls import ClientLimitPolicy, GatewayRateLimiter, McpGatewayControls
from app.mcp.middleware.authentication import GatewayAuthenticationMiddleware
from app.mcp.server import build_server
from app.models.agent import AgentClientType
from app.models.project import ProjectDocument
from app.repositories.project_repository import MongoProjectRepository
from app.services.agent_client_service import AgentClientService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

COMPATIBILITY_SCOPES = (
    "skills:read",
    "projects:read",
    "context:read",
    "context:write",
    "memory:read",
    "memory:write",
    "search:read",
    "agent:self",
)
READ_ONLY_SCOPES = ("projects:read", "search:read", "agent:self")
VARIANTS = (
    ("codex-like", AgentClientType.CODEX),
    ("claude-like", AgentClientType.CLAUDE_CODE),
    ("gemini-like", AgentClientType.GEMINI_CLI),
    ("custom", AgentClientType.CUSTOM),
)


class GatewayEmbedder:
    """Deterministic vectors keep the compatibility contract offline."""

    def __init__(self) -> None:
        settings = get_settings()
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimensions

    def _vector(self) -> EmbeddingVector:
        return EmbeddingVector(
            model=self._model,
            dimensions=self._dimensions,
            values=tuple(1.0 if index == 0 else 0.0 for index in range(self._dimensions)),
        )

    async def embed_query(self, text: str) -> EmbeddingVector:
        _ = text
        return self._vector()

    async def embed_chunks(self, contents: list[str]) -> tuple[EmbeddingVector, ...]:
        return tuple(self._vector() for _ in contents)


@dataclass(frozen=True, slots=True)
class CompatibilityFixture:
    """One independently credentialed client against one real shared application."""

    application: FastAPI
    client_id: str
    client_type: AgentClientType
    token: str
    read_only_token: str
    project_id: str


@pytest.fixture
def run_id() -> str:
    return str(ObjectId())[-8:]


@asynccontextmanager
async def compatibility_application(
    run_id: str,
    *,
    variant: str,
    client_type: AgentClientType,
) -> AsyncIterator[CompatibilityFixture]:
    """Provision a single normal and read-only credential through V4 services."""
    settings = get_settings().model_copy(
        update={"embedding_enabled": False, "retrieval_log_enabled": True}
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        application.state.embeddings = GatewayEmbedder()
        database = application.state.mongodb.database
        client_id = f"phase6-{variant}-{run_id}"
        project_id = f"phase6-project-{variant}-{run_id}"
        projects = MongoProjectRepository(database)
        await projects.create(
            ProjectDocument(
                project_id=project_id,
                name=f"Phase 6 {variant}",
                description="Generic MCP compatibility fixture",
                repository=f"https://example.invalid/{project_id}.git",
                environment="test",
            )
        )
        clients: AgentClientService = application.state.agent_client_service
        client = await clients.create_client(
            client_id=client_id,
            client_type=client_type,
            name=f"Phase 6 {variant}",
            owner_id="phase6-integration",
            scopes=COMPATIBILITY_SCOPES,
            project_ids=[project_id],
        )
        credential = await clients.create_credential(client.client_id)
        readonly = await clients.create_credential(
            client.client_id,
            scopes=READ_ONLY_SCOPES,
            projects=[project_id],
        )
        try:
            yield CompatibilityFixture(
                application=application,
                client_id=client.client_id,
                client_type=client.client_type,
                token=credential.token,
                read_only_token=readonly.token,
                project_id=project_id,
            )
        finally:
            await database["memories"].delete_many({"project_id": project_id})
            await database["project_context"].delete_many({"project_id": project_id})
            await database["retrieval_logs"].delete_many({"client_id": client_id})
            await database["agent_activity"].delete_many({"client_id": client_id})
            await database["agent_sessions"].delete_many({"client_id": client_id})
            await database["gateway_idempotency"].delete_many({"client_id": client_id})
            await database["api_keys"].delete_many({"client_id": client_id})
            await database["agent_clients"].delete_many({"client_id": client_id})
            await database["projects"].delete_many({"project_id": project_id})


async def call_tool(
    fixture: CompatibilityFixture,
    token: str,
    name: str,
    arguments: dict[str, object],
    *,
    controls: McpGatewayControls | None = None,
) -> Any:
    """Open one raw MCP connection to the shared server and call one tool."""
    client = ContextApiClient(
        "http://context-api.invalid",
        token=token,
        transport=httpx.ASGITransport(app=fixture.application),
    )
    server = build_server(
        client,
        Authorization(),
        gateway=GatewayAuthenticationMiddleware(client),
        controls=controls,
    )
    try:
        async with create_connected_server_and_client_session(server) as session:
            return await session.call_tool(name, arguments)
    finally:
        await client.close()


async def connect_and_discover(fixture: CompatibilityFixture) -> tuple[set[str], Any]:
    """Exercise MCP initialization and discovery before making a capability call."""
    client = ContextApiClient(
        "http://context-api.invalid",
        token=fixture.token,
        transport=httpx.ASGITransport(app=fixture.application),
    )
    server = build_server(
        client,
        Authorization(),
        gateway=GatewayAuthenticationMiddleware(client),
    )
    try:
        async with create_connected_server_and_client_session(server) as session:
            listed = await session.list_tools()
            capabilities = await session.call_tool("get_platform_capabilities", {})
    finally:
        await client.close()
    return {tool.name for tool in listed.tools}, capabilities


def error_code(result: Any) -> str:
    """Read the shared stable MCP error envelope."""
    assert result.isError is True
    assert result.structuredContent is not None
    error = result.structuredContent["error"]
    assert isinstance(error, dict)
    code = error["code"]
    assert isinstance(code, str)
    return code


@pytest.mark.asyncio
@pytest.mark.parametrize(("variant", "client_type"), VARIANTS, ids=[item[0] for item in VARIANTS])
async def test_generic_mcp_client_compatibility_contract(
    run_id: str,
    variant: str,
    client_type: AgentClientType,
) -> None:
    """All supported client identities obey one gateway contract and one store."""
    async with compatibility_application(
        run_id, variant=variant, client_type=client_type
    ) as fixture:
        tools, capabilities = await connect_and_discover(fixture)
        invalid = await call_tool(
            fixture, "not-a-real-agent-context-token", "get_platform_capabilities", {}
        )
        read = await call_tool(fixture, fixture.token, "list_projects", {})
        resolution = await call_tool(
            fixture, fixture.token, "resolve_project", {"project_id": fixture.project_id}
        )
        forbidden = await call_tool(
            fixture,
            fixture.read_only_token,
            "save_context",
            {
                "project_id": fixture.project_id,
                "type": "architecture_decision",
                "title": "Read-only client must not write",
                "content": "This must not persist.",
                "importance": 0.5,
            },
        )
        created = await call_tool(
            fixture,
            fixture.token,
            "save_context",
            {
                "project_id": fixture.project_id,
                "type": "architecture_decision",
                "title": f"{variant} compatibility write",
                "content": "A generic MCP client wrote through the shared gateway.",
                "importance": 0.8,
                "external_id": f"phase6-write-{run_id}",
            },
        )
        unknown = await call_tool(fixture, fixture.token, "not_a_platform_tool", {})

        memory = await call_tool(
            fixture,
            fixture.token,
            "save_memory",
            {
                "type": "convention",
                "content": "Guarded compatibility memory.",
                "importance": 0.7,
                "confidence": 0.9,
                "scope": "project",
                "project_id": fixture.project_id,
                "external_id": f"phase6-memory-{run_id}",
            },
        )
        assert memory.structuredContent is not None
        stored_memory = memory.structuredContent["memory"]
        assert isinstance(stored_memory, dict)
        memory_id = str(stored_memory["memory_id"])
        first_update = await call_tool(
            fixture,
            fixture.token,
            "update_memory",
            {
                "memory_id": memory_id,
                "project_id": fixture.project_id,
                "scope": "project",
                "content": "First guarded compatibility update.",
                "expected_revision": 1,
            },
        )
        stale_update = await call_tool(
            fixture,
            fixture.token,
            "update_memory",
            {
                "memory_id": memory_id,
                "project_id": fixture.project_id,
                "scope": "project",
                "content": "Stale compatibility update.",
                "expected_revision": 1,
            },
        )

        idempotency_key = f"phase6-idempotency-{run_id}"
        idempotent_arguments: dict[str, object] = {
            "project_id": fixture.project_id,
            "type": "convention",
            "title": "Idempotent generic client write",
            "content": "One retry creates one durable context record.",
            "importance": 0.8,
            "external_id": idempotency_key,
            "idempotency_key": idempotency_key,
        }
        first_idempotent = await call_tool(
            fixture, fixture.token, "save_context", idempotent_arguments
        )
        retried_idempotent = await call_tool(
            fixture, fixture.token, "save_context", idempotent_arguments
        )

        controls = McpGatewayControls(
            limiter=GatewayRateLimiter(
                ClientLimitPolicy(
                    requests_per_minute=1,
                    concurrent_mcp_calls=1,
                    writes_per_minute=1,
                    research_ingestions_per_hour=1,
                    long_running_jobs_per_client=1,
                )
            ),
            fast_timeout_seconds=10.0,
            standard_timeout_seconds=30.0,
            long_running_timeout_seconds=120.0,
        )
        rate_first = await call_tool(
            fixture, fixture.token, "get_platform_capabilities", {}, controls=controls
        )
        rate_limited = await call_tool(
            fixture, fixture.token, "get_platform_capabilities", {}, controls=controls
        )
        reconnected = await call_tool(fixture, fixture.token, "get_platform_capabilities", {})
        database = fixture.application.state.mongodb.database
        idempotent_count = await database["project_context"].count_documents(
            {"project_id": fixture.project_id, "external_id": idempotency_key}
        )
        activity_client_types = {
            activity["client_type"]
            for activity in await database["agent_activity"]
            .find({"client_id": fixture.client_id}, {"_id": 0, "client_type": 1})
            .to_list(None)
        }
        collection_names = set(await database.list_collection_names())

    assert {"get_platform_capabilities", "list_projects", "save_context"} <= tools
    assert capabilities.isError is False
    assert capabilities.structuredContent is not None
    capability_meta = capabilities.structuredContent["meta"]
    assert capability_meta["client_id"] == fixture.client_id
    assert isinstance(capability_meta["request_id"], str)
    assert capability_meta["project_id"] is None
    assert activity_client_types == {client_type.value}
    assert invalid.isError is True
    assert error_code(invalid) == "UNAUTHENTICATED"
    assert read.isError is False
    assert resolution.isError is False
    assert resolution.structuredContent is not None
    assert resolution.structuredContent["project_id"] == fixture.project_id
    assert error_code(forbidden) == "FORBIDDEN"
    assert created.isError is False
    assert error_code(unknown) == "NOT_FOUND"
    assert memory.isError is False
    assert first_update.isError is False
    assert error_code(stale_update) == "REVISION_CONFLICT"
    assert first_idempotent.isError is False
    assert retried_idempotent.isError is False
    assert first_idempotent.structuredContent is not None
    assert retried_idempotent.structuredContent is not None
    assert (
        first_idempotent.structuredContent["context"]["context_id"]
        == retried_idempotent.structuredContent["context"]["context_id"]
    )
    assert idempotent_count == 1
    assert rate_first.isError is False
    assert error_code(rate_limited) == "RATE_LIMITED"
    assert reconnected.isError is False
    assert not collection_names & {
        "codex_context",
        "claude_context",
        "gemini_context",
        "custom_context",
        "codex_memories",
        "claude_memories",
        "gemini_memories",
        "custom_memories",
    }
