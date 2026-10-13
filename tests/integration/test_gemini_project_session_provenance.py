"""V5 Phase 4 Gemini coverage over the shared V4 gateway paths."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
from app.mcp.middleware.authentication import GatewayAuthenticationMiddleware
from app.mcp.server import build_server
from app.models.agent import AgentClientType, ProjectBindingType
from app.models.client_profile import GEMINI_DEFAULT_SCOPES
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

GEMINI_CLIENT_ID = "gemini-cli-main"


class GatewayEmbedder:
    """Deterministic local embeddings keep the gateway integration offline."""

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
async def gemini_application_for(run_id: str) -> AsyncIterator[tuple[FastAPI, str, str, str, str]]:
    """Provision one Gemini client and isolated fixture projects through V4 services."""
    settings = get_settings().model_copy(
        update={"embedding_enabled": False, "retrieval_log_enabled": True}
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        application.state.embeddings = GatewayEmbedder()
        database = application.state.mongodb.database
        clients: AgentClientService = application.state.agent_client_service
        project_id = f"payment-service-{run_id}"
        private_project_id = f"private-service-{run_id}"
        research_project_id = f"gemini-provenance-{run_id}"
        projects = MongoProjectRepository(database)
        await projects.create(
            ProjectDocument(
                project_id=project_id,
                name="Payment Service",
                description="Gemini V5 Phase 4 fixture",
                repository=f"https://github.com/example/payment-service-{run_id}.git",
                environment="test",
            )
        )
        await projects.create(
            ProjectDocument(
                project_id=private_project_id,
                name="Private Service",
                description="Project deliberately outside the Gemini grant",
                repository=f"https://github.com/example/private-service-{run_id}.git",
                environment="test",
            )
        )
        bindings = ProjectBindingService(projects, MongoProjectBindingRepository(database))
        binding_ids = [
            (
                await bindings.create_binding(
                    project_id=project_id,
                    binding_type=ProjectBindingType.GIT_REMOTE,
                    value=f"git@github.com:example/payment-service-{run_id}.git",
                )
            ).binding_id,
            (
                await bindings.create_binding(
                    project_id=project_id,
                    binding_type=ProjectBindingType.REPOSITORY_NAME,
                    value=f"payment-service-{run_id}",
                )
            ).binding_id,
            (
                await bindings.create_binding(
                    project_id=project_id,
                    binding_type=ProjectBindingType.WORKSPACE_ALIAS,
                    value=f"payment-service-workspace-{run_id}",
                )
            ).binding_id,
            (
                await bindings.create_binding(
                    project_id=private_project_id,
                    binding_type=ProjectBindingType.REPOSITORY_NAME,
                    value=f"private-service-{run_id}",
                )
            ).binding_id,
        ]
        gemini = await clients.create_client(
            client_id=GEMINI_CLIENT_ID,
            client_type=AgentClientType.GEMINI_CLI,
            name="Gemini CLI Phase 4 integration",
            owner_id="gemini-phase4",
            scopes=list(GEMINI_DEFAULT_SCOPES),
            project_ids=[project_id],
        )
        credential = await clients.create_credential(gemini.client_id)
        try:
            yield application, credential.token, project_id, private_project_id, research_project_id
        finally:
            for collection in (
                "knowledge_candidates",
                "research_claims",
                "research_notes",
                "research_citations",
                "research_sources",
                "research_projects",
            ):
                await database[collection].delete_many({"research_project_id": research_project_id})
            await database["memories"].delete_many({"client_id": GEMINI_CLIENT_ID})
            await database["project_context"].delete_many({"project_id": project_id})
            await database["retrieval_logs"].delete_many({"client_id": GEMINI_CLIENT_ID})
            await database["agent_activity"].delete_many({"client_id": GEMINI_CLIENT_ID})
            await database["agent_sessions"].delete_many({"client_id": GEMINI_CLIENT_ID})
            await database["gateway_idempotency"].delete_many({"client_id": GEMINI_CLIENT_ID})
            await database["api_keys"].delete_many({"client_id": GEMINI_CLIENT_ID})
            await database["agent_clients"].delete_many({"client_id": GEMINI_CLIENT_ID})
            await database["project_bindings"].delete_many({"binding_id": {"$in": binding_ids}})
            await database["projects"].delete_many(
                {"project_id": {"$in": [project_id, private_project_id]}}
            )


def _session_data(result: Any) -> dict[str, Any]:
    assert result.structuredContent is not None
    data = result.structuredContent["data"]
    assert isinstance(data, dict)
    session = data["session"]
    assert isinstance(session, dict)
    return session


@pytest.mark.asyncio
async def test_gemini_uses_shared_project_sessions_and_actor_provenance(run_id: str) -> None:
    """Gemini reaches only client-neutral V4/V1/V3 MCP and persistence paths."""
    async with gemini_application_for(run_id) as (
        application,
        token,
        project_id,
        private_project_id,
        research_project_id,
    ):
        client = ContextApiClient(
            "http://context-api.invalid",
            token=token,
            transport=httpx.ASGITransport(app=application),
        )
        gateway = GatewayAuthenticationMiddleware(client)
        server = build_server(client, Authorization(), gateway=gateway)
        try:
            async with create_connected_server_and_client_session(server) as session:
                resolved = [
                    await session.call_tool("resolve_project", {"project_id": project_id}),
                    await session.call_tool(
                        "resolve_project",
                        {"git_remote": f"git@github.com:example/payment-service-{run_id}.git"},
                    ),
                    await session.call_tool(
                        "resolve_project",
                        {
                            "repository_url": (
                                f"https://github.com/example/payment-service-{run_id}.git"
                            )
                        },
                    ),
                    await session.call_tool(
                        "resolve_project",
                        {"repository_name": f"payment-service-{run_id}"},
                    ),
                    await session.call_tool(
                        "resolve_project",
                        {"workspace_alias": f"payment-service-workspace-{run_id}"},
                    ),
                ]
                forbidden = await session.call_tool(
                    "resolve_project", {"project_id": private_project_id}
                )
                begun = await session.call_tool(
                    "begin_agent_session",
                    {
                        "project_id": project_id,
                        "repository": f"git@github.com:example/payment-service-{run_id}.git",
                        "metadata": {"workspace": "payment-service"},
                    },
                )
                session_data = _session_data(begun)
                session_id = str(session_data["session_id"])
                updated = await session.call_tool(
                    "update_agent_session",
                    {
                        "session_id": session_id,
                        "expected_revision": int(session_data["revision"]),
                        "metadata": {"workspace": "payment-service", "mode": "provenance"},
                    },
                )
                updated_data = _session_data(updated)
                searched = await session.call_tool(
                    "search_knowledge",
                    {"query": "Gemini payment-service provenance", "project_id": project_id},
                )
                context = await session.call_tool(
                    "save_context",
                    {
                        "project_id": project_id,
                        "type": "architecture_decision",
                        "title": "Gemini session provenance",
                        "content": "The shared gateway records the authenticated Gemini actor.",
                        "importance": 0.8,
                        "external_id": f"gemini-context-{run_id}",
                    },
                )
                memory = await session.call_tool(
                    "save_memory",
                    {
                        "scope": "agent",
                        "type": "note",
                        "content": "Gemini session operational checkpoint.",
                        "importance": 0.4,
                        "confidence": 0.8,
                        "external_id": f"gemini-memory-{run_id}",
                    },
                )
                research_project = await session.call_tool(
                    "create_research_project",
                    {
                        "research_project_id": research_project_id,
                        "name": "Gemini provenance fixture",
                        "question": "How is Gemini actor provenance retained?",
                        "type": "technical_research",
                        "related_project_ids": [project_id],
                    },
                )
                note = await session.call_tool(
                    "save_research_note",
                    {
                        "research_project_id": research_project_id,
                        "note_id": f"gemini-note-{run_id}",
                        "title": "Gemini provenance note",
                        "body": "This is an explicit research working artifact.",
                    },
                )
                claim = await session.call_tool(
                    "create_research_claim",
                    {
                        "research_project_id": research_project_id,
                        "claim_id": f"gemini-claim-{run_id}",
                        "statement": "Gateway provenance is credential derived.",
                    },
                )
                candidate = await session.call_tool(
                    "propose_knowledge_candidate",
                    {
                        "research_project_id": research_project_id,
                        "candidate_id": f"gemini-candidate-{run_id}",
                        "candidate_type": "project_context",
                        "target_project_id": project_id,
                        "title": "Gemini provenance candidate",
                        "content": "Retain the authenticated Gemini actor on this candidate.",
                        "source_claim_ids": [f"gemini-claim-{run_id}"],
                    },
                )
                ended = await session.call_tool(
                    "end_agent_session",
                    {
                        "session_id": session_id,
                        "expected_revision": int(updated_data["revision"]),
                    },
                )
        finally:
            await client.close()

        database = application.state.mongodb.database
        stored_session = await database["agent_sessions"].find_one(
            {"session_id": session_id}, {"_id": 0}
        )
        retrieval_logs = (
            await database["retrieval_logs"]
            .find({"client_id": GEMINI_CLIENT_ID, "session_id": session_id}, {"_id": 0})
            .to_list(length=None)
        )
        activities = (
            await database["agent_activity"]
            .find({"client_id": GEMINI_CLIENT_ID, "session_id": session_id}, {"_id": 0})
            .to_list(length=None)
        )
        provenance_documents = [
            await database["project_context"].find_one(
                {"project_id": project_id, "external_id": f"gemini-context-{run_id}"}, {"_id": 0}
            ),
            await database["memories"].find_one(
                {"client_id": GEMINI_CLIENT_ID, "external_id": f"gemini-memory-{run_id}"},
                {"_id": 0},
            ),
            await database["research_projects"].find_one(
                {"research_project_id": research_project_id}, {"_id": 0}
            ),
            await database["research_notes"].find_one(
                {"research_project_id": research_project_id}, {"_id": 0}
            ),
            await database["research_claims"].find_one(
                {"research_project_id": research_project_id}, {"_id": 0}
            ),
            await database["knowledge_candidates"].find_one(
                {"research_project_id": research_project_id}, {"_id": 0}
            ),
        ]

    assert all(result.isError is False for result in resolved)
    assert all(result.structuredContent is not None for result in resolved)
    assert all(result.structuredContent["project_id"] == project_id for result in resolved)
    assert forbidden.isError is True
    assert forbidden.structuredContent is not None
    assert forbidden.structuredContent["error"]["code"] == "PROJECT_FORBIDDEN"
    assert all(
        result.isError is False
        for result in (
            begun,
            updated,
            searched,
            context,
            memory,
            research_project,
            note,
            claim,
            candidate,
            ended,
        )
    )
    assert stored_session is not None
    assert stored_session["client_id"] == GEMINI_CLIENT_ID
    assert stored_session["client_type"] == "gemini_cli"
    assert stored_session["status"] == "ended"
    assert stored_session["metadata"] == {"workspace": "payment-service", "mode": "provenance"}
    assert {"conversation", "messages", "prompt", "transcript"}.isdisjoint(stored_session)
    assert retrieval_logs
    assert all(log["client_type"] == "gemini_cli" for log in retrieval_logs)
    assert all(log["session_id"] == session_id for log in retrieval_logs)
    assert {event["tool"] for event in activities} >= {
        "begin_agent_session",
        "update_agent_session",
        "search_knowledge",
        "save_context",
        "save_memory",
        "end_agent_session",
    }
    assert all(event["client_type"] == "gemini_cli" for event in activities)
    assert all(event["session_id"] == session_id for event in activities)
    expected_actor = {
        "actor_type": "agent",
        "client_id": GEMINI_CLIENT_ID,
        "client_type": "gemini_cli",
    }
    assert all(document is not None for document in provenance_documents)
    assert all(document["actor"] == expected_actor for document in provenance_documents)
