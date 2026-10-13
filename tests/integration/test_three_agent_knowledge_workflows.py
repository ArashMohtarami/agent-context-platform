"""V5 Phase 5 acceptance coverage for the shared three-agent knowledge platform."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
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
from app.models.client_profile import GEMINI_DEFAULT_SCOPES
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

PROJECT_ID = "payment-service"
CODEX_CLIENT_ID = "codex-main"
CLAUDE_CLIENT_ID = "claude-code-main"
GEMINI_CLIENT_ID = "gemini-cli-main"
SEARCH_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 0.1


class GatewayEmbedder:
    """Use deterministic local vectors so this test never downloads a model."""

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
class AgentFixture:
    """One independent client identity and its one-time test credential."""

    client_id: str
    client_type: AgentClientType
    token: str


@dataclass(frozen=True, slots=True)
class ThreeAgentFixture:
    """The shared app, project, and three independently authenticated clients."""

    application: FastAPI
    project_id: str
    codex: AgentFixture
    claude: AgentFixture
    gemini: AgentFixture


@pytest.fixture
def run_id() -> str:
    return str(ObjectId())[-8:]


@asynccontextmanager
async def three_agent_application(run_id: str) -> AsyncIterator[ThreeAgentFixture]:
    """Provision the Phase 5 fixtures without adding client-specific storage."""
    settings = get_settings().model_copy(
        update={"embedding_enabled": False, "retrieval_log_enabled": True}
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        application.state.embeddings = GatewayEmbedder()
        database = application.state.mongodb.database
        client_ids = (CODEX_CLIENT_ID, CLAUDE_CLIENT_ID, GEMINI_CLIENT_ID)
        if await database["agent_clients"].count_documents({"client_id": {"$in": client_ids}}):
            pytest.skip("the named V5 Phase 5 client fixture already exists outside this test")

        projects = MongoProjectRepository(database)
        project_created = False
        if await projects.get(PROJECT_ID) is None:
            await projects.create(
                ProjectDocument(
                    project_id=PROJECT_ID,
                    name="Payment Service",
                    description="V5 Phase 5 three-agent knowledge fixture",
                    repository="https://github.com/agent-context-fixtures/payment-service.git",
                    environment="test",
                )
            )
            project_created = True

        clients: AgentClientService = application.state.agent_client_service
        created: list[AgentFixture] = []
        try:
            for client_id, client_type, name in (
                (CODEX_CLIENT_ID, AgentClientType.CODEX, "Codex Phase 5 fixture"),
                (CLAUDE_CLIENT_ID, AgentClientType.CLAUDE_CODE, "Claude Code Phase 5 fixture"),
                (GEMINI_CLIENT_ID, AgentClientType.GEMINI_CLI, "Gemini CLI Phase 5 fixture"),
            ):
                client = await clients.create_client(
                    client_id=client_id,
                    client_type=client_type,
                    name=name,
                    owner_id="phase5-integration",
                    scopes=list(GEMINI_DEFAULT_SCOPES),
                    project_ids=[PROJECT_ID],
                )
                credential = await clients.create_credential(client.client_id)
                created.append(
                    AgentFixture(
                        client_id=client.client_id,
                        client_type=client.client_type,
                        token=credential.token,
                    )
                )
            yield ThreeAgentFixture(
                application=application,
                project_id=PROJECT_ID,
                codex=created[0],
                claude=created[1],
                gemini=created[2],
            )
        finally:
            await database["memories"].delete_many({"tags": f"phase5-{run_id}"})
            await database["project_context"].delete_many(
                {"project_id": PROJECT_ID, "tags": f"phase5-{run_id}"}
            )
            await database["retrieval_logs"].delete_many({"client_id": {"$in": client_ids}})
            await database["agent_activity"].delete_many({"client_id": {"$in": client_ids}})
            await database["agent_sessions"].delete_many({"client_id": {"$in": client_ids}})
            await database["gateway_idempotency"].delete_many({"client_id": {"$in": client_ids}})
            await database["api_keys"].delete_many({"client_id": {"$in": client_ids}})
            await database["agent_clients"].delete_many({"client_id": {"$in": client_ids}})
            if project_created:
                await database["projects"].delete_many({"project_id": PROJECT_ID})


async def call_tool(
    fixture: ThreeAgentFixture,
    agent: AgentFixture,
    tool: str,
    arguments: dict[str, object],
    *,
    controls: McpGatewayControls | None = None,
) -> Any:
    """Invoke one shared MCP tool as one independent credentialed client."""
    client = ContextApiClient(
        "http://context-api.invalid",
        token=agent.token,
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
            return await session.call_tool(tool, arguments)
    finally:
        await client.close()


def _results(result: Any) -> list[dict[str, Any]]:
    assert result.isError is False
    assert result.structuredContent is not None
    records = result.structuredContent["results"]
    assert isinstance(records, list)
    assert all(isinstance(item, dict) for item in records)
    return records


def _contains(result: Any, content: str) -> bool:
    return any(item.get("content") == content for item in _results(result))


async def wait_for_content(
    fixture: ThreeAgentFixture,
    agent: AgentFixture,
    tool: str,
    arguments: dict[str, object],
    expected_content: str,
) -> Any:
    """Wait for mongot to index one known record before asserting visibility."""
    deadline = monotonic() + SEARCH_TIMEOUT_SECONDS
    while True:
        result = await call_tool(fixture, agent, tool, arguments)
        if _contains(result, expected_content):
            return result
        if monotonic() >= deadline:
            raise AssertionError(f"{tool} did not return the expected shared record")
        await asyncio.sleep(POLL_SECONDS)


def _actor(agent: AgentFixture) -> dict[str, str]:
    return {
        "actor_type": "agent",
        "client_id": agent.client_id,
        "client_type": agent.client_type.value,
    }


@pytest.mark.asyncio
async def test_three_agents_share_project_knowledge_but_not_operational_memory(
    run_id: str,
) -> None:
    """Exercise scenarios A--F against V1/V4's one shared persistence path."""
    tag = f"phase5-{run_id}"
    async with three_agent_application(run_id) as fixture:
        codex_context = "Codex shared payment-service retry convention."
        gemini_context = "Gemini shared payment-service timeout convention."
        claude_project_memory = "Claude shared payment-service settlement checkpoint."
        agent_memory = {
            fixture.codex.client_id: "Codex private payment-service scratchpad.",
            fixture.claude.client_id: "Claude private payment-service scratchpad.",
            fixture.gemini.client_id: "Gemini private payment-service scratchpad.",
        }
        gemini_project_agent = "Gemini project-local payment-service scratchpad."

        codex_saved = await call_tool(
            fixture,
            fixture.codex,
            "save_context",
            {
                "project_id": fixture.project_id,
                "type": "architecture_decision",
                "title": "Codex shared context",
                "content": codex_context,
                "importance": 0.8,
                "tags": [tag, "codex-shared-context"],
                "external_id": f"phase5-codex-context-{run_id}",
            },
        )
        gemini_sees_codex = await wait_for_content(
            fixture,
            fixture.gemini,
            "search_context",
            {
                "project_id": fixture.project_id,
                "query": "Codex shared retry convention",
                "tags": [tag, "codex-shared-context"],
            },
            codex_context,
        )
        claude_sees_codex = await wait_for_content(
            fixture,
            fixture.claude,
            "search_context",
            {
                "project_id": fixture.project_id,
                "query": "Codex shared retry convention",
                "tags": [tag, "codex-shared-context"],
            },
            codex_context,
        )

        gemini_saved = await call_tool(
            fixture,
            fixture.gemini,
            "save_context",
            {
                "project_id": fixture.project_id,
                "type": "convention",
                "title": "Gemini shared context",
                "content": gemini_context,
                "importance": 0.8,
                "tags": [tag, "gemini-shared-context"],
                "external_id": f"phase5-gemini-context-{run_id}",
            },
        )
        codex_sees_gemini = await wait_for_content(
            fixture,
            fixture.codex,
            "search_context",
            {
                "project_id": fixture.project_id,
                "query": "Gemini shared timeout convention",
                "tags": [tag, "gemini-shared-context"],
            },
            gemini_context,
        )
        claude_sees_gemini = await wait_for_content(
            fixture,
            fixture.claude,
            "search_context",
            {
                "project_id": fixture.project_id,
                "query": "Gemini shared timeout convention",
                "tags": [tag, "gemini-shared-context"],
            },
            gemini_context,
        )

        claude_memory_saved = await call_tool(
            fixture,
            fixture.claude,
            "save_memory",
            {
                "type": "convention",
                "content": claude_project_memory,
                "importance": 0.8,
                "confidence": 0.9,
                "scope": "project",
                "project_id": fixture.project_id,
                "tags": [tag, "claude-project-memory"],
                "external_id": f"phase5-claude-project-memory-{run_id}",
            },
        )
        gemini_sees_claude_memory = await wait_for_content(
            fixture,
            fixture.gemini,
            "search_memory",
            {
                "query": "Claude shared settlement checkpoint",
                "project_id": fixture.project_id,
                "scopes": ["project"],
                "tags": [tag, "claude-project-memory"],
            },
            claude_project_memory,
        )
        codex_sees_claude_memory = await wait_for_content(
            fixture,
            fixture.codex,
            "search_memory",
            {
                "query": "Claude shared settlement checkpoint",
                "project_id": fixture.project_id,
                "scopes": ["project"],
                "tags": [tag, "claude-project-memory"],
            },
            claude_project_memory,
        )

        agent_fixtures = (fixture.codex, fixture.claude, fixture.gemini)
        for agent in agent_fixtures:
            saved = await call_tool(
                fixture,
                agent,
                "save_memory",
                {
                    "type": "note",
                    "content": agent_memory[agent.client_id],
                    "importance": 0.4,
                    "confidence": 0.8,
                    "scope": "agent",
                    "tags": [tag, f"{agent.client_id}-agent-memory"],
                    "external_id": f"phase5-{agent.client_id}-agent-memory-{run_id}",
                },
            )
            assert saved.isError is False

        for owner in agent_fixtures:
            own = await wait_for_content(
                fixture,
                owner,
                "search_memory",
                {
                    "query": agent_memory[owner.client_id],
                    "tags": [tag, f"{owner.client_id}-agent-memory"],
                },
                agent_memory[owner.client_id],
            )
            assert _contains(own, agent_memory[owner.client_id])
            for other in agent_fixtures:
                if other.client_id == owner.client_id:
                    continue
                hidden = await call_tool(
                    fixture,
                    other,
                    "search_memory",
                    {
                        "query": agent_memory[owner.client_id],
                        "tags": [tag, f"{owner.client_id}-agent-memory"],
                    },
                )
                assert not _contains(hidden, agent_memory[owner.client_id])

        project_agent_saved = await call_tool(
            fixture,
            fixture.gemini,
            "save_memory",
            {
                "type": "note",
                "content": gemini_project_agent,
                "importance": 0.4,
                "confidence": 0.8,
                "scope": "project_agent",
                "project_id": fixture.project_id,
                "tags": [tag, "gemini-project-agent-memory"],
                "external_id": f"phase5-gemini-project-agent-memory-{run_id}",
            },
        )
        gemini_sees_project_agent = await wait_for_content(
            fixture,
            fixture.gemini,
            "search_memory",
            {
                "query": gemini_project_agent,
                "project_id": fixture.project_id,
                "tags": [tag, "gemini-project-agent-memory"],
            },
            gemini_project_agent,
        )
        gemini_without_project = await call_tool(
            fixture,
            fixture.gemini,
            "search_memory",
            {
                "query": gemini_project_agent,
                "tags": [tag, "gemini-project-agent-memory"],
            },
        )
        assert not _contains(gemini_without_project, gemini_project_agent)
        for other in (fixture.codex, fixture.claude):
            hidden = await call_tool(
                fixture,
                other,
                "search_memory",
                {
                    "query": gemini_project_agent,
                    "project_id": fixture.project_id,
                    "tags": [tag, "gemini-project-agent-memory"],
                },
            )
            assert not _contains(hidden, gemini_project_agent)

        database = fixture.application.state.mongodb.database
        contexts = (
            await database["project_context"]
            .find(
                {"project_id": fixture.project_id, "tags": tag},
                {"_id": 0, "external_id": 1, "actor": 1},
            )
            .to_list(length=None)
        )
        memories = (
            await database["memories"]
            .find({"tags": tag}, {"_id": 0, "external_id": 1, "actor": 1})
            .to_list(length=None)
        )
        collections = set(await database.list_collection_names())

    assert all(
        result.isError is False
        for result in (
            codex_saved,
            gemini_sees_codex,
            claude_sees_codex,
            gemini_saved,
            codex_sees_gemini,
            claude_sees_gemini,
            claude_memory_saved,
            gemini_sees_claude_memory,
            codex_sees_claude_memory,
            project_agent_saved,
            gemini_sees_project_agent,
        )
    )
    assert {item["external_id"]: item["actor"] for item in contexts} == {
        f"phase5-codex-context-{run_id}": _actor(fixture.codex),
        f"phase5-gemini-context-{run_id}": _actor(fixture.gemini),
    }
    expected_memory_actors = {
        f"phase5-claude-project-memory-{run_id}": _actor(fixture.claude),
        f"phase5-{fixture.codex.client_id}-agent-memory-{run_id}": _actor(fixture.codex),
        f"phase5-{fixture.claude.client_id}-agent-memory-{run_id}": _actor(fixture.claude),
        f"phase5-{fixture.gemini.client_id}-agent-memory-{run_id}": _actor(fixture.gemini),
        f"phase5-gemini-project-agent-memory-{run_id}": _actor(fixture.gemini),
    }
    assert {item["external_id"]: item["actor"] for item in memories} == expected_memory_actors
    assert not collections & {
        "codex_context",
        "claude_context",
        "gemini_context",
        "codex_memories",
        "claude_memories",
        "gemini_memories",
    }


@pytest.mark.asyncio
async def test_three_agents_enforce_concurrency_idempotency_and_rate_isolation(run_id: str) -> None:
    """Exercise scenarios G--I over the same shared V4 gateway controls."""
    tag = f"phase5-{run_id}"
    async with three_agent_application(run_id) as fixture:
        initial_content = "Three agents read this shared payment-service memory revision."
        saved = await call_tool(
            fixture,
            fixture.codex,
            "save_memory",
            {
                "type": "convention",
                "content": initial_content,
                "importance": 0.8,
                "confidence": 0.9,
                "scope": "project",
                "project_id": fixture.project_id,
                "tags": [tag, "concurrent-memory"],
                "external_id": f"phase5-concurrent-memory-{run_id}",
            },
        )
        assert saved.structuredContent is not None
        stored_memory = saved.structuredContent["memory"]
        assert isinstance(stored_memory, dict)
        memory_id = str(stored_memory["memory_id"])

        reads = await asyncio.gather(
            *(
                wait_for_content(
                    fixture,
                    agent,
                    "search_memory",
                    {
                        "query": initial_content,
                        "project_id": fixture.project_id,
                        "scopes": ["project"],
                        "tags": [tag, "concurrent-memory"],
                    },
                    initial_content,
                )
                for agent in (fixture.codex, fixture.claude, fixture.gemini)
            )
        )
        revisions = {
            int(
                next(
                    item["metadata"]["revision"]
                    for item in _results(result)
                    if item["metadata"].get("memory_id") == memory_id
                )
            )
            for result in reads
        }
        assert revisions == {1}

        updates = await asyncio.gather(
            *(
                call_tool(
                    fixture,
                    agent,
                    "update_memory",
                    {
                        "memory_id": memory_id,
                        "project_id": fixture.project_id,
                        "scope": "project",
                        "content": f"{agent.client_id} won the guarded update.",
                        "expected_revision": 1,
                    },
                )
                for agent in (fixture.codex, fixture.claude, fixture.gemini)
            )
        )
        successes = [result for result in updates if result.isError is False]
        conflicts = [result for result in updates if result.isError is True]
        database = fixture.application.state.mongodb.database
        persisted = await database["memories"].find_one(
            {"_id": ObjectId(memory_id)},
            {"_id": 0, "content": 1, "revision": 1, "actor": 1},
        )

        idempotency_key = f"phase5-gemini-retry-{run_id}"
        idempotent_arguments: dict[str, object] = {
            "project_id": fixture.project_id,
            "type": "architecture_decision",
            "title": "Gemini idempotent context",
            "content": "Gemini retries create exactly one durable context record.",
            "importance": 0.8,
            "tags": [tag, "gemini-idempotency"],
            "external_id": idempotency_key,
            "idempotency_key": idempotency_key,
        }
        first = await call_tool(fixture, fixture.gemini, "save_context", idempotent_arguments)
        retry = await call_tool(fixture, fixture.gemini, "save_context", idempotent_arguments)
        assert first.structuredContent is not None
        assert retry.structuredContent is not None
        first_context = first.structuredContent["context"]
        retry_context = retry.structuredContent["context"]
        assert isinstance(first_context, dict)
        assert isinstance(retry_context, dict)
        durable_contexts = await database["project_context"].count_documents(
            {"project_id": fixture.project_id, "external_id": idempotency_key}
        )
        idempotent_context = await database["project_context"].find_one(
            {"project_id": fixture.project_id, "external_id": idempotency_key},
            {"_id": 0, "actor": 1},
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
        gemini_first = await call_tool(
            fixture, fixture.gemini, "get_platform_capabilities", {}, controls=controls
        )
        gemini_limited = await call_tool(
            fixture, fixture.gemini, "get_platform_capabilities", {}, controls=controls
        )
        codex_unaffected = await call_tool(
            fixture, fixture.codex, "get_platform_capabilities", {}, controls=controls
        )
        claude_unaffected = await call_tool(
            fixture, fixture.claude, "get_platform_capabilities", {}, controls=controls
        )

    assert saved.isError is False
    assert len(successes) == 1
    assert len(conflicts) == 2
    assert all(result.structuredContent is not None for result in conflicts)
    assert all(
        result.structuredContent["error"]["code"] == "REVISION_CONFLICT" for result in conflicts
    )
    assert persisted is not None
    assert persisted["revision"] == 2
    successful_update_actors = {
        f"{agent.client_id} won the guarded update.": _actor(agent)
        for agent in (fixture.codex, fixture.claude, fixture.gemini)
    }
    assert persisted["content"] in successful_update_actors
    # A real concurrent conditional update has one winner, not necessarily the
    # first coroutine listed above. Its persisted provenance must name that
    # winner; assuming Codex always wins makes this acceptance check flaky.
    assert persisted["actor"] == successful_update_actors[persisted["content"]]
    assert first.isError is False
    assert retry.isError is False
    assert retry_context["context_id"] == first_context["context_id"]
    assert durable_contexts == 1
    assert idempotent_context == {"actor": _actor(fixture.gemini)}
    assert gemini_first.isError is False
    assert gemini_limited.isError is True
    assert gemini_limited.structuredContent is not None
    assert gemini_limited.structuredContent["error"]["code"] == "RATE_LIMITED"
    assert codex_unaffected.isError is False
    assert claude_unaffected.isError is False
