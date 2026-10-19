"""Real MCP gateway workflow with independent Codex and Claude credentials."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from bson import ObjectId
from fastapi import FastAPI
from mcp.shared.memory import create_connected_server_and_client_session
from v6_legacy_integration import unified_search_ready

from app.core.config import get_settings
from app.embeddings.types import EmbeddingVector
from app.main import create_app
from app.mcp.auth import Authorization
from app.mcp.client import ContextApiClient
from app.mcp.errors import RevisionConflictToolError
from app.mcp.middleware.authentication import GatewayAuthenticationMiddleware
from app.mcp.server import build_server
from app.models.agent import AgentClientType, ProjectBindingType
from app.models.project import ProjectDocument
from app.repositories.agent_repository import MongoProjectBindingRepository
from app.repositories.project_repository import MongoProjectRepository
from app.services.agent_client_service import AgentClientService
from app.services.project_binding_service import ProjectBindingService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

INDEX_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 0.1


class GatewayEmbedder:
    """Deterministic local embeddings keep gateway integration offline."""

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


@pytest.fixture
def run_id() -> str:
    return str(ObjectId())[-8:]


@asynccontextmanager
async def application_for(run_id: str) -> AsyncIterator[tuple[FastAPI, str, str, str, str, str]]:
    """Create and later remove two V4 clients and their independent credentials."""
    # The gateway contract is independent of model download. A deterministic
    # provider below supplies vectors after startup, while this disables the
    # production provider's external-model lifecycle inside CI.
    settings = get_settings().model_copy(
        update={"embedding_enabled": False, "retrieval_log_enabled": True}
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        application.state.embeddings = GatewayEmbedder()
        await unified_search_ready(
            application.state.mongodb.database, settings.embedding_dimensions
        )
        clients: AgentClientService = application.state.agent_client_service
        codex_id = f"phase2-codex-{run_id}"
        claude_id = f"phase2-claude-{run_id}"
        project_id = f"phase4-payment-{run_id}"
        projects = MongoProjectRepository(application.state.mongodb.database)
        await projects.create(
            ProjectDocument(
                project_id=project_id,
                name="Phase 4 Payment Service",
                description="MCP project-resolution integration fixture",
                repository=f"https://github.com/example/payment-service-{run_id}.git",
                environment="test",
            )
        )
        bindings = ProjectBindingService(
            projects,
            MongoProjectBindingRepository(application.state.mongodb.database),
        )
        binding = await bindings.create_binding(
            project_id=project_id,
            binding_type=ProjectBindingType.GIT_REMOTE,
            value=f"git@github.com:example/payment-service-{run_id}.git",
        )
        codex = await clients.create_client(
            client_id=codex_id,
            client_type=AgentClientType.CODEX,
            name="Phase 2 Codex",
            owner_id="phase2-owner",
            scopes=[
                "search:read",
                "projects:read",
                "context:read",
                "context:write",
                "memory:read",
                "memory:write",
                "agent:self",
                "session:write",
            ],
            project_ids=[project_id],
        )
        claude = await clients.create_client(
            client_id=claude_id,
            client_type=AgentClientType.CLAUDE_CODE,
            name="Phase 2 Claude",
            owner_id="phase2-owner",
            scopes=[
                "search:read",
                "projects:read",
                "context:read",
                "context:write",
                "memory:read",
                "memory:write",
                "agent:self",
                "session:write",
            ],
            project_ids=[project_id],
        )
        codex_key = await clients.create_credential(codex.client_id)
        claude_key = await clients.create_credential(claude.client_id)
        try:
            yield (
                application,
                codex.client_id,
                codex_key.token,
                claude.client_id,
                claude_key.token,
                project_id,
            )
        finally:
            database = application.state.mongodb.database
            await database["memories"].delete_many(
                {
                    "$or": [
                        {"client_id": {"$in": [codex.client_id, claude.client_id]}},
                        {"tags": f"phase5-{run_id}"},
                    ]
                }
            )
            await database["project_context"].delete_many({"project_id": project_id})
            await database["project_bindings"].delete_many({"binding_id": binding.binding_id})
            await database["projects"].delete_many({"project_id": project_id})
            await database["agent_activity"].delete_many(
                {"client_id": {"$in": [codex.client_id, claude.client_id]}}
            )
            await database["agent_sessions"].delete_many(
                {"client_id": {"$in": [codex.client_id, claude.client_id]}}
            )
            await database["gateway_idempotency"].delete_many(
                {"client_id": {"$in": [codex.client_id, claude.client_id]}}
            )
            await database["retrieval_logs"].delete_many(
                {"client_id": {"$in": [codex.client_id, claude.client_id]}}
            )
            await database["api_keys"].delete_many(
                {"client_id": {"$in": [codex.client_id, claude.client_id]}}
            )
            await database["agent_clients"].delete_many(
                {"client_id": {"$in": [codex.client_id, claude.client_id]}}
            )


async def call_through_gateway(
    application: FastAPI, token: str, tool: str, arguments: dict[str, object]
) -> Any:
    """Use a raw MCP protocol fixture, not a tool handler shortcut."""
    client = ContextApiClient(
        "http://context-api.invalid",
        token=token,
        transport=httpx.ASGITransport(app=application),
    )
    server = build_server(
        client,
        Authorization(),
        gateway=GatewayAuthenticationMiddleware(client),
    )
    try:
        async with create_connected_server_and_client_session(server) as session:
            return await session.call_tool(tool, arguments)
    finally:
        await client.close()


async def run_session_workflow(
    application: FastAPI,
    token: str,
    project_id: str,
) -> tuple[Any, Any, Any, Any]:
    """Keep one MCP gateway instance alive so a session binds later calls."""
    client = ContextApiClient(
        "http://context-api.invalid",
        token=token,
        transport=httpx.ASGITransport(app=application),
    )
    server = build_server(
        client,
        Authorization(),
        gateway=GatewayAuthenticationMiddleware(client),
    )
    try:
        async with create_connected_server_and_client_session(server) as session:
            begun = await session.call_tool(
                "begin_agent_session",
                {
                    "project_id": project_id,
                    "repository": "git@github.com:example/payment-service.git",
                    "metadata": {"workspace": "payment-service"},
                },
            )
            assert begun.structuredContent is not None
            begin_data = begun.structuredContent["data"]
            assert isinstance(begin_data, dict)
            session_data = begin_data["session"]
            assert isinstance(session_data, dict)
            session_id = str(session_data["session_id"])
            revision = int(session_data["revision"])
            searched = await session.call_tool(
                "search_knowledge",
                {"query": "agent session payment service", "project_id": project_id},
            )
            saved = await session.call_tool(
                "save_context",
                {
                    "project_id": project_id,
                    "type": "architecture_decision",
                    "title": "Session correlation fixture",
                    "content": "This context write is correlated with one Claude session.",
                    "importance": 0.7,
                    "external_id": f"phase9-context-{session_id}",
                },
            )
            ended = await session.call_tool(
                "end_agent_session",
                {"session_id": session_id, "expected_revision": revision},
            )
    finally:
        await client.close()
    return begun, searched, saved, ended


async def discover_and_get_capabilities(
    application: FastAPI, token: str
) -> tuple[set[str], dict[str, object]]:
    """Use a raw MCP client to verify discovery and invocation together."""
    client = ContextApiClient(
        "http://context-api.invalid",
        token=token,
        transport=httpx.ASGITransport(app=application),
    )
    server = build_server(
        client,
        Authorization(),
        gateway=GatewayAuthenticationMiddleware(client),
    )
    try:
        async with create_connected_server_and_client_session(server) as session:
            listed = await session.list_tools()
            result = await session.call_tool("get_platform_capabilities", {})
    finally:
        await client.close()

    assert result.isError is False
    assert result.structuredContent is not None
    return {tool.name for tool in listed.tools}, dict(result.structuredContent)


async def call_context_api(
    application: FastAPI,
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, object] | None = None,
    json: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Exercise the same bearer-authenticated API client used by the gateway."""
    client = ContextApiClient(
        "http://context-api.invalid",
        token=token,
        transport=httpx.ASGITransport(app=application),
    )
    try:
        return await client.request(method, path, params=params, json=json)
    finally:
        await client.close()


async def wait_for_memory_content(
    application: FastAPI,
    token: str,
    arguments: dict[str, object],
    expected_content: str,
) -> Any:
    """Wait for mongot to observe a gateway write before asserting visibility."""
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    while True:
        result = await call_through_gateway(application, token, "search_memory", arguments)
        structured = result.structuredContent
        results = [] if structured is None else structured.get("results", [])
        if isinstance(results, list) and any(
            isinstance(item, dict) and item.get("content") == expected_content for item in results
        ):
            return result
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(
                f"memory was not indexed within {INDEX_TIMEOUT_SECONDS:.0f} seconds"
            )
        await asyncio.sleep(POLL_SECONDS)


@pytest.mark.asyncio
async def test_client_scoped_idempotency_replays_retries_without_duplicate_context(
    run_id: str,
) -> None:
    """A Claude retry is replayed, while Codex owns an independent retry key space."""
    async with application_for(run_id) as (
        application,
        codex_id,
        codex_token,
        claude_id,
        claude_token,
        project_id,
    ):
        key = f"phase10-context-{run_id}"
        arguments = {
            "project_id": project_id,
            "type": "architecture_decision",
            "title": "Bounded client retries",
            "content": "A retry key prevents duplicate gateway context writes.",
            "importance": 0.8,
            "idempotency_key": key,
        }
        first = await call_through_gateway(application, claude_token, "save_context", arguments)
        retry = await call_through_gateway(application, claude_token, "save_context", arguments)

        assert first.isError is False
        assert retry.isError is False
        assert first.structuredContent is not None
        assert retry.structuredContent is not None
        first_context = first.structuredContent["context"]
        retry_context = retry.structuredContent["context"]
        assert isinstance(first_context, dict)
        assert isinstance(retry_context, dict)
        assert retry_context["context_id"] == first_context["context_id"]
        database = application.state.mongodb.database
        assert await database["project_context"].count_documents({"project_id": project_id}) == 1

        incompatible = await call_through_gateway(
            application,
            claude_token,
            "save_context",
            {**arguments, "title": "Different write under the same key"},
        )
        assert incompatible.isError is True
        assert incompatible.structuredContent is not None
        assert incompatible.structuredContent["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert await database["project_context"].count_documents({"project_id": project_id}) == 1

        codex = await call_through_gateway(
            application,
            codex_token,
            "save_context",
            {**arguments, "title": "Codex owns the same opaque key independently"},
        )
        assert codex.isError is False
        assert await database["project_context"].count_documents({"project_id": project_id}) == 2
        records = (
            await database["gateway_idempotency"]
            .find({"client_id": {"$in": [claude_id, codex_id]}})
            .to_list(length=None)
        )
        assert len(records) == 2
        assert all(key not in str(record) for record in records)


@pytest.mark.asyncio
async def test_codex_and_claude_share_one_gateway_and_activity_isolated_by_client(
    run_id: str,
) -> None:
    async with application_for(run_id) as (
        application,
        codex_id,
        codex_token,
        claude_id,
        claude_token,
        project_id,
    ):
        codex = await call_through_gateway(
            application,
            codex_token,
            "search_knowledge",
            {"query": "gateway identity"},
        )
        claude = await call_through_gateway(
            application,
            claude_token,
            "search_knowledge",
            {"query": "gateway identity"},
        )
        codex_resolution = await call_through_gateway(
            application,
            codex_token,
            "resolve_project",
            {"git_remote": f"git@github.com:example/payment-service-{run_id}.git"},
        )
        claude_resolution = await call_through_gateway(
            application,
            claude_token,
            "resolve_project",
            {"repository_url": f"https://github.com/example/payment-service-{run_id}.git"},
        )
        events = [
            document
            async for document in application.state.mongodb.database["agent_activity"].find(
                {"client_id": {"$in": [codex_id, claude_id]}}, {"_id": 0}
            )
        ]

    assert codex.isError is False
    assert claude.isError is False
    assert codex_resolution.isError is False
    assert claude_resolution.isError is False
    assert codex_resolution.structuredContent is not None
    assert codex_resolution.structuredContent["project_id"] == project_id
    assert claude_resolution.structuredContent is not None
    assert claude_resolution.structuredContent["project_id"] == project_id
    assert {(event["client_id"], event["client_type"], event["tool"]) for event in events} == {
        (codex_id, "codex", "search_knowledge"),
        (claude_id, "claude_code", "search_knowledge"),
        (codex_id, "codex", "resolve_project"),
        (claude_id, "claude_code", "resolve_project"),
    }


@pytest.mark.asyncio
async def test_codex_and_claude_discover_an_identical_capability_contract(
    run_id: str,
) -> None:
    async with application_for(run_id) as (
        application,
        codex_id,
        codex_token,
        claude_id,
        claude_token,
        _,
    ):
        codex_tools, codex_capabilities = await discover_and_get_capabilities(
            application, codex_token
        )
        claude_tools, claude_capabilities = await discover_and_get_capabilities(
            application, claude_token
        )

    assert codex_tools == claude_tools
    assert "get_platform_capabilities" in codex_tools
    assert codex_capabilities["data"] == claude_capabilities["data"]
    assert codex_capabilities["meta"]["client_id"] == codex_id
    assert claude_capabilities["meta"]["client_id"] == claude_id
    assert codex_capabilities["meta"]["request_id"] != claude_capabilities["meta"]["request_id"]


@pytest.mark.asyncio
async def test_claude_session_correlates_search_and_context_write_without_storing_conversation(
    run_id: str,
) -> None:
    """One shared MCP gateway carries a Claude session through /search and /context."""
    async with application_for(run_id) as (
        application,
        _codex_id,
        _codex_token,
        claude_id,
        claude_token,
        project_id,
    ):
        begun, searched, saved, ended = await run_session_workflow(
            application,
            claude_token,
            project_id,
        )
        assert begun.structuredContent is not None
        begin_data = begun.structuredContent["data"]
        assert isinstance(begin_data, dict)
        session = begin_data["session"]
        assert isinstance(session, dict)
        session_id = str(session["session_id"])
        events = [
            item
            async for item in application.state.mongodb.database["agent_activity"].find(
                {"client_id": claude_id, "session_id": session_id},
                {"_id": 0, "tool": 1, "session_id": 1},
            )
        ]
        logs = [
            item
            async for item in application.state.mongodb.database["retrieval_logs"].find(
                {"client_id": claude_id, "session_id": session_id},
                {"_id": 0, "session_id": 1, "query": 1},
            )
        ]
        stored_session = await application.state.mongodb.database["agent_sessions"].find_one(
            {"session_id": session_id},
            {"_id": 0},
        )

    assert begun.isError is False
    assert searched.isError is False
    assert saved.isError is False
    assert ended.isError is False
    assert {item["tool"] for item in events} >= {
        "begin_agent_session",
        "search_knowledge",
        "save_context",
        "end_agent_session",
    }
    assert logs and all(item["session_id"] == session_id for item in logs)
    assert stored_session is not None
    assert stored_session["status"] == "ended"
    assert {"conversation", "messages", "prompt", "transcript"}.isdisjoint(stored_session)


@pytest.mark.asyncio
async def test_gateway_keeps_agent_memory_private_while_sharing_project_memory(
    run_id: str,
) -> None:
    """Claude and Codex use the same tools, with identity from credentials."""
    async with application_for(run_id) as (
        application,
        codex_id,
        codex_token,
        claude_id,
        claude_token,
        project_id,
    ):
        claude_agent_save = await call_through_gateway(
            application,
            claude_token,
            "save_memory",
            {
                "type": "note",
                "content": "Claude has checked the payment-service retry fixture.",
                "importance": 0.4,
                "confidence": 0.9,
                "scope": "agent",
                "external_id": f"phase5-claude-{run_id}",
                "tags": [f"phase5-{run_id}"],
            },
        )
        claude_agent_search = await wait_for_memory_content(
            application,
            claude_token,
            {
                "query": "payment-service retry fixture",
                "scopes": ["agent"],
                "tags": [f"phase5-{run_id}"],
            },
            "Claude has checked the payment-service retry fixture.",
        )
        codex_agent_search = await call_through_gateway(
            application,
            codex_token,
            "search_memory",
            {
                "query": "payment-service retry fixture",
                "scopes": ["agent"],
                "tags": [f"phase5-{run_id}"],
            },
        )
        project_save = await call_through_gateway(
            application,
            claude_token,
            "save_memory",
            {
                "type": "convention",
                "content": "Payment-service requests require idempotency keys.",
                "importance": 0.9,
                "confidence": 0.95,
                "scope": "project",
                "project_id": project_id,
                "external_id": f"phase5-project-{run_id}",
                "tags": [f"phase5-{run_id}"],
            },
        )
        claude_project_search = await wait_for_memory_content(
            application,
            claude_token,
            {
                "query": "Payment-service idempotency keys",
                "project_id": project_id,
                "scopes": ["project"],
                "tags": [f"phase5-{run_id}"],
            },
            "Payment-service requests require idempotency keys.",
        )
        codex_project_search = await call_through_gateway(
            application,
            codex_token,
            "search_memory",
            {
                "query": "Payment-service idempotency keys",
                "project_id": project_id,
                "scopes": ["project"],
                "tags": [f"phase5-{run_id}"],
            },
        )

    assert claude_agent_save.isError is False
    assert claude_agent_search.isError is False
    assert codex_agent_search.isError is False
    assert project_save.isError is False
    assert claude_project_search.isError is False
    assert codex_project_search.isError is False
    assert claude_agent_search.structuredContent is not None
    assert codex_agent_search.structuredContent is not None
    assert claude_project_search.structuredContent is not None
    assert codex_project_search.structuredContent is not None
    assert [item["content"] for item in claude_agent_search.structuredContent["results"]] == [
        "Claude has checked the payment-service retry fixture."
    ]
    assert claude_agent_search.structuredContent["results"][0]["metadata"]["revision"] == 1
    assert codex_agent_search.structuredContent["results"] == []
    project_content = "Payment-service requests require idempotency keys."
    assert project_content in [
        item["content"] for item in claude_project_search.structuredContent["results"]
    ]
    assert project_content in [
        item["content"] for item in codex_project_search.structuredContent["results"]
    ]
    assert claude_id != codex_id


@pytest.mark.asyncio
async def test_codex_and_claude_context_writes_persist_actor_and_safe_activity(
    run_id: str,
) -> None:
    """One client-neutral MCP write path attributes each client independently."""
    async with application_for(run_id) as (
        application,
        codex_id,
        codex_token,
        claude_id,
        claude_token,
        project_id,
    ):
        codex = await call_through_gateway(
            application,
            codex_token,
            "save_context",
            {
                "project_id": project_id,
                "type": "architecture_decision",
                "title": "Codex provenance fixture",
                "content": "Codex wrote this durable context through the shared MCP gateway.",
                "importance": 0.8,
                "external_id": f"phase6-codex-{run_id}",
            },
        )
        claude = await call_through_gateway(
            application,
            claude_token,
            "save_context",
            {
                "project_id": project_id,
                "type": "architecture_decision",
                "title": "Claude provenance fixture",
                "content": "Claude wrote this durable context through the shared MCP gateway.",
                "importance": 0.8,
                "external_id": f"phase6-claude-{run_id}",
            },
        )
        contexts = [
            document
            async for document in application.state.mongodb.database["project_context"].find(
                {
                    "project_id": project_id,
                    "external_id": {"$in": [f"phase6-codex-{run_id}", f"phase6-claude-{run_id}"]},
                },
                {"_id": 0, "external_id": 1, "actor": 1},
            )
        ]
        events = [
            document
            async for document in application.state.mongodb.database["agent_activity"].find(
                {"client_id": {"$in": [codex_id, claude_id]}, "tool": "save_context"},
                {"_id": 0},
            )
        ]

    assert codex.isError is False
    assert claude.isError is False
    assert {item["actor"]["client_id"] for item in contexts} == {codex_id, claude_id}
    assert {item["actor"]["client_type"] for item in contexts} == {"codex", "claude_code"}
    assert {item["client_id"] for item in events} == {codex_id, claude_id}
    assert {item["resource_type"] for item in events} == {"context"}
    assert all(item.get("resource_id") for item in events)
    assert all("content" not in item for item in events)


@pytest.mark.asyncio
async def test_codex_update_then_claude_stale_update_never_overwrites_context(
    run_id: str,
) -> None:
    """Two clients read revision 5; only the first guarded update may commit."""
    async with application_for(run_id) as (
        application,
        _codex_id,
        codex_token,
        _claude_id,
        claude_token,
        project_id,
    ):
        created = await call_context_api(
            application,
            codex_token,
            "POST",
            "/api/v1/context",
            json={
                "project_id": project_id,
                "type": "architecture_decision",
                "title": "Original concurrency fixture",
                "content": "Both agents initially read this version.",
                "importance": 0.8,
                "source": "user",
                "external_id": f"phase7-context-{run_id}",
            },
        )
        context_id = str(created["context_id"])
        prepared = created
        for expected_revision in range(1, 5):
            prepared = await call_context_api(
                application,
                codex_token,
                "PATCH",
                f"/api/v1/context/{context_id}",
                params={"project_id": project_id},
                json={
                    "title": f"Preparation revision {expected_revision + 1}",
                    "expected_revision": expected_revision,
                },
            )
        codex_read = await call_context_api(
            application,
            codex_token,
            "GET",
            f"/api/v1/context/{context_id}",
            params={"project_id": project_id},
        )
        claude_read = await call_context_api(
            application,
            claude_token,
            "GET",
            f"/api/v1/context/{context_id}",
            params={"project_id": project_id},
        )

        codex_updated = await call_context_api(
            application,
            codex_token,
            "PATCH",
            f"/api/v1/context/{context_id}",
            params={"project_id": project_id},
            json={
                "title": "Codex guarded update",
                "expected_revision": codex_read["revision"],
            },
        )
        with pytest.raises(RevisionConflictToolError) as stale:
            await call_context_api(
                application,
                claude_token,
                "PATCH",
                f"/api/v1/context/{context_id}",
                params={"project_id": project_id},
                json={
                    "title": "Claude stale update",
                    "expected_revision": claude_read["revision"],
                },
            )
        persisted = await application.state.mongodb.database["project_context"].find_one(
            {"_id": ObjectId(context_id)},
            {"_id": 0, "title": 1, "revision": 1},
        )

    assert created["revision"] == 1
    assert prepared["revision"] == 5
    assert codex_read["revision"] == claude_read["revision"] == 5
    assert codex_updated["revision"] == 6
    assert stale.value.code == "REVISION_CONFLICT"
    assert stale.value.message == "Resource changed since it was retrieved."
    assert persisted == {"title": "Codex guarded update", "revision": 6}


@pytest.mark.asyncio
async def test_mcp_memory_conflict_keeps_the_codex_write_and_exposes_a_safe_error(
    run_id: str,
) -> None:
    """The actual MCP transport returns a structured conflict, never a silent retry."""
    async with application_for(run_id) as (
        application,
        _codex_id,
        codex_token,
        _claude_id,
        claude_token,
        project_id,
    ):
        saved = await call_through_gateway(
            application,
            codex_token,
            "save_memory",
            {
                "type": "convention",
                "content": "Original MCP concurrency fixture.",
                "importance": 0.8,
                "confidence": 0.9,
                "scope": "project",
                "project_id": project_id,
                "external_id": f"phase7-memory-{run_id}",
                "tags": [f"phase5-{run_id}"],
            },
        )
        assert saved.structuredContent is not None
        memory = saved.structuredContent["memory"]
        assert isinstance(memory, dict)
        memory_id = str(memory["memory_id"])
        assert memory["revision"] == 1

        codex_updated = await call_through_gateway(
            application,
            codex_token,
            "update_memory",
            {
                "memory_id": memory_id,
                "content": "Codex MCP guarded update.",
                "project_id": project_id,
                "scope": "project",
                "expected_revision": 1,
            },
        )
        claude_stale = await call_through_gateway(
            application,
            claude_token,
            "update_memory",
            {
                "memory_id": memory_id,
                "content": "Claude MCP stale update.",
                "project_id": project_id,
                "scope": "project",
                "expected_revision": 1,
            },
        )
        persisted = await application.state.mongodb.database["memories"].find_one(
            {"_id": ObjectId(memory_id)},
            {"_id": 0, "content": 1, "revision": 1},
        )

    assert codex_updated.isError is False
    assert codex_updated.structuredContent is not None
    assert codex_updated.structuredContent["memory"]["revision"] == 2
    assert claude_stale.isError is True
    assert claude_stale.structuredContent == {
        "error": {
            "code": "REVISION_CONFLICT",
            "message": "Resource changed since it was retrieved.",
        }
    }
    assert persisted == {"content": "Codex MCP guarded update.", "revision": 2}
