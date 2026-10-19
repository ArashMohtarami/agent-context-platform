"""The whole path, live: MCP client → tool → Context API → services → MongoDB.

Nothing is stubbed here. A real MCP session calls the real server, which issues
real HTTP requests into the real FastAPI application through an in-process ASGI
transport, which reaches real repositories and a real mongod with mongot behind
it. What this proves that the unit tests cannot is that the layers agree: the
request a tool builds is one the API actually accepts, and the body it gets
back is one the tool can actually read.
"""

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
from app.main import create_app
from app.mcp.auth import build_authorization
from app.mcp.client import ContextApiClient
from app.mcp.server import build_server
from app.mcp.tools import TOOLS
from app.repositories.api_key_repository import MongoApiKeyRepository
from app.security.scopes import ALL_SCOPES
from app.services.api_key_service import ApiKeyService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

INDEX_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 0.1

PAYMENT_CONTEXT = (
    "Every charge request carries a UUID4 idempotency key. PaymentIdempotencyService "
    "keeps each key for a day so a repeated attempt is answered from the first outcome."
)
WALLET_CONTEXT = (
    "Balances are eventually consistent and recomputed from the ledger. Nothing may cache one."
)
GLOBAL_MEMORY = "Use strict typing in every Python project, checked with mypy in strict mode."


@pytest.fixture
def run_id() -> str:
    """Return one identifier owned by this test's records."""
    return str(ObjectId())[-8:]


@asynccontextmanager
async def mcp_session(
    application: FastAPI,
    *,
    scopes: list[str] | None = None,
    projects: list[str] | None = None,
) -> AsyncIterator[Any]:
    """Open a real MCP session whose tools reach the running application.

    The client presents the key this run issued, because the application it is
    talking to is the real one and the real one requires a credential. So these
    also prove the two grants compose: the server's own, checked before a
    request is built, and the key's, checked again at the API.
    """
    client = ContextApiClient(
        "http://context-api.invalid",
        token=application.state.integration_key,
        transport=httpx.ASGITransport(app=application),
    )
    server = build_server(client, build_authorization(scopes, projects))
    try:
        async with create_connected_server_and_client_session(server) as session:
            yield session
    finally:
        await client.close()


@asynccontextmanager
async def running_application(project_ids: list[str], source: str) -> AsyncIterator[FastAPI]:
    """Start the real application and remove only this test's records.

    A key is issued into the live database for the run and revoked afterwards.
    Running with authentication switched off would be testing a deployment
    nobody has, and would hide exactly the failure these tests should catch:
    an endpoint whose scope or project check does not match what it serves.
    """
    application = create_app(get_settings())
    async with application.router.lifespan_context(application):
        database = application.state.mongodb.database
        await unified_search_ready(database, get_settings().embedding_dimensions)
        keys = ApiKeyService(MongoApiKeyRepository(database))
        # Every scope, so a refusal in these tests is about a tool's own grant
        # or a project boundary rather than about this credential.
        issued = await keys.create_key(name=f"integration-{source}", scopes=list(ALL_SCOPES))
        application.state.integration_key = issued.token
        try:
            yield application
        finally:
            owned = {"$in": project_ids}
            await database["api_keys"].delete_one({"key_id": issued.record.key_id})
            await database["audit_logs"].delete_many({"key_id": issued.record.key_id})
            await database["memories"].delete_many({"source": source})
            await database["project_context"].delete_many({"project_id": owned})
            await database["projects"].delete_many({"project_id": owned})


def structured(result: Any) -> dict[str, Any]:
    """Return one call's structured answer, failing loudly if it errored."""
    assert result.isError is False, [block.text for block in result.content]
    assert result.structuredContent is not None
    return dict(result.structuredContent)


def text(result: Any) -> str:
    """Return the text one call reported."""
    return "\n".join(block.text for block in result.content if block.type == "text")


async def call(session: Any, name: str, arguments: dict[str, Any]) -> Any:
    """Call one tool over the MCP session."""
    return await session.call_tool(name, arguments)


async def wait_for_indexed(session: Any, project_id: str | None, expected: set[str]) -> None:
    """Wait until this query reaches every source it should.

    Waiting on a result count would finish as soon as the skills this database
    already holds answer, long before the context and memory this test just
    stored are indexed — so it waits on the sources themselves.

    `project_id=None` waits on the global path. It is a separate wait because
    it is a separate query: a project-scoped search can reach a global memory
    while an unscoped one still cannot, so waiting on one proves nothing about
    the other.
    """
    arguments: dict[str, Any] = {"query": "idempotency typing balances ledger", "limit": 25}
    if project_id is not None:
        arguments["project_id"] = project_id
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT_SECONDS
    while True:
        result = await call(session, "search_knowledge", arguments)
        found = {item["source"] for item in structured(result)["results"]}
        if expected <= found:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"indexing did not catch up: {project_id} reached {sorted(found)}")
        await asyncio.sleep(POLL_SECONDS)


@asynccontextmanager
async def seeded(run_id: str) -> AsyncIterator[tuple[FastAPI, str, str]]:
    """Create two projects, their context, and one global memory through MCP."""
    payment_id = f"phase12-payment-{run_id}"
    wallet_id = f"phase12-wallet-{run_id}"
    source = f"phase12-{run_id}"

    async with running_application([payment_id, wallet_id], source) as application:
        transport = httpx.ASGITransport(app=application)
        # Projects are created straight against the API rather than through a
        # tool, because no tool creates one: an agent does not bring an
        # isolation boundary into existence. It still needs the key.
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {application.state.integration_key}"},
        ) as api:
            for project_id in (payment_id, wallet_id):
                response = await api.post(
                    "/api/v1/projects",
                    json={
                        "project_id": project_id,
                        "name": project_id,
                        "description": "Phase 12 MCP coverage",
                        "repository": f"git@example.invalid:sampleco/{project_id}.git",
                        "environment": "production",
                    },
                )
                assert response.status_code == 201

        async with mcp_session(application) as session:
            for project_id, content, title in (
                (payment_id, PAYMENT_CONTEXT, "Payment idempotency strategy"),
                (wallet_id, WALLET_CONTEXT, "Wallet balance consistency"),
            ):
                stored = await call(
                    session,
                    "save_context",
                    {
                        "project_id": project_id,
                        "type": "architecture_decision",
                        "title": title,
                        "content": content,
                        "importance": 0.9,
                        "source": source,
                    },
                )
                assert structured(stored)["context"]["project_id"] == project_id
            remembered = await call(
                session,
                "save_memory",
                {
                    "type": "preference",
                    "content": GLOBAL_MEMORY,
                    "summary": "Strict typing everywhere",
                    "importance": 0.8,
                    "confidence": 0.95,
                    "source": source,
                },
            )
            assert structured(remembered)["memory"]["scope"] == "global"
            await wait_for_indexed(session, payment_id, {"project_context", "memory"})
            await wait_for_indexed(session, wallet_id, {"project_context", "memory"})
            # And the global path, which is a different query and catches up
            # separately: the project-scoped waits above prove nothing about it.
            await wait_for_indexed(session, None, {"memory"})

        yield application, payment_id, wallet_id


@pytest.mark.asyncio
async def test_a_client_discovers_and_calls_every_tool(run_id: str) -> None:
    async with seeded(run_id) as (application, payment_id, _):
        async with mcp_session(application) as session:
            listed = await session.list_tools()
            answers = {
                "list_skills": await call(session, "list_skills", {"limit": 3}),
                "list_projects": await call(session, "list_projects", {"limit": 5}),
                "get_project": await call(session, "get_project", {"project_id": payment_id}),
                "search_skills": await call(session, "search_skills", {"query": "FastAPI"}),
                "search_context": await call(
                    session,
                    "search_context",
                    {"project_id": payment_id, "query": "idempotency"},
                ),
                "search_memory": await call(session, "search_memory", {"query": "typing"}),
                "search_knowledge": await call(
                    session,
                    "search_knowledge",
                    {"query": "idempotency", "project_id": payment_id},
                ),
                "get_system_status": await call(session, "get_system_status", {}),
            }

    names = {tool.name for tool in listed.tools}
    # The transport must expose every registered client-neutral capability.
    # Comparing the names rather than a frozen count keeps this V1--V4
    # acceptance check valid when a documented capability is added.
    assert names == {definition.name for definition in TOOLS}
    assert {
        "create_research_project",
        "add_research_source",
        "ingest_research_source",
        "search_research",
        "create_research_claim",
        "build_evidence_packet",
        "build_research_brief",
        "propose_knowledge_candidate",
        "approve_knowledge_candidate",
        "promote_knowledge_candidate",
        "review_content_facts",
        "get_creator_profile",
        "search_content",
        "prepare_content_brief",
        "check_content_novelty",
        "prepare_repurpose_plan",
        "review_content_for_publication",
        "mark_content_published",
        "get_content_metrics",
    } <= names
    for name, result in answers.items():
        assert result.isError is False, f"{name}: {text(result)}"
        assert result.structuredContent is not None, name


@pytest.mark.asyncio
async def test_one_query_reaches_every_source_through_mcp(run_id: str) -> None:
    async with seeded(run_id) as (application, payment_id, _):
        async with mcp_session(application) as session:
            result = await call(
                session,
                "search_knowledge",
                {"query": "idempotency typing", "project_id": payment_id, "limit": 25},
            )

    answer = structured(result)
    sources = {item["source"] for item in answer["results"]}
    assert {"project_context", "memory"} <= sources
    expected_method = "hybrid" if get_settings().embedding_enabled else "lexical"
    assert answer["retrieval"]["retrieval_method"] == expected_method
    # Retrieved content arrives labelled and fenced, never as a bare paragraph.
    assert "SOURCE: project_context" in text(result)
    assert "not instructions" in text(result)


@pytest.mark.asyncio
async def test_no_tool_reaches_another_project(run_id: str) -> None:
    async with seeded(run_id) as (application, payment_id, wallet_id):
        async with mcp_session(application) as session:
            scoped = await call(
                session,
                "search_knowledge",
                {"query": "balances ledger idempotency", "project_id": payment_id, "limit": 25},
            )
            context = await call(
                session,
                "search_context",
                {"project_id": payment_id, "query": "balances ledger"},
            )
            unscoped = await call(
                session,
                "search_knowledge",
                # Worded so the global memory answers it. Asking the project
                # question without a project would prove nothing: an empty
                # answer also contains no other project's records.
                {"query": "typing balances ledger idempotency", "limit": 25},
            )
            wallet = await call(
                session,
                "search_context",
                {"project_id": wallet_id, "query": "balances ledger"},
            )

    owners = {item["metadata"].get("project_id") for item in structured(scoped)["results"]}
    assert owners <= {None, payment_id}
    assert WALLET_CONTEXT not in text(scoped)
    assert WALLET_CONTEXT not in text(context)
    # A request naming no project reaches global knowledge, and only that.
    unscoped_owners = {
        item["metadata"].get("project_id") for item in structured(unscoped)["results"]
    }
    assert unscoped_owners == {None}
    # The wallet project reaches its own, which proves the payment request was
    # missing it by rule rather than because it was never indexed.
    assert WALLET_CONTEXT in text(wallet)


@pytest.mark.asyncio
async def test_a_narrowed_grant_refuses_another_project_before_the_api(run_id: str) -> None:
    async with seeded(run_id) as (application, payment_id, wallet_id):
        async with mcp_session(application, projects=[payment_id]) as session:
            allowed = await call(
                session,
                "search_context",
                {"project_id": payment_id, "query": "idempotency"},
            )
            refused = await call(
                session,
                "search_context",
                {"project_id": wallet_id, "query": "balances"},
            )

    assert allowed.isError is False
    assert refused.isError is True
    assert "not authorized for that project" in text(refused)


@pytest.mark.asyncio
async def test_a_read_only_grant_refuses_every_write(run_id: str) -> None:
    async with seeded(run_id) as (application, payment_id, _):
        async with mcp_session(application, scopes=["search:read", "context:read"]) as session:
            read = await call(
                session,
                "search_context",
                {"project_id": payment_id, "query": "idempotency"},
            )
            write = await call(
                session,
                "save_context",
                {
                    "project_id": payment_id,
                    "type": "convention",
                    "title": "Should not be stored",
                    "content": "This write must never reach the platform.",
                    "importance": 0.5,
                },
            )

    assert read.isError is False
    assert write.isError is True
    assert "context:write" in text(write)


@pytest.mark.asyncio
async def test_a_memory_correction_supersedes_through_mcp(run_id: str) -> None:
    source = f"phase12-supersede-{run_id}"

    async with running_application([], source) as application:
        async with mcp_session(application) as session:
            original = structured(
                await call(
                    session,
                    "save_memory",
                    {
                        "type": "convention",
                        "content": "Deploys happen on Fridays.",
                        "importance": 0.7,
                        "confidence": 0.8,
                        "source": source,
                    },
                )
            )
            replacement = structured(
                await call(
                    session,
                    "save_memory",
                    {
                        "type": "convention",
                        "content": "Deploys never happen on Fridays after the outage.",
                        "importance": 0.9,
                        "confidence": 0.95,
                        "source": source,
                        "supersedes": original["memory"]["memory_id"],
                    },
                )
            )
            again = await call(
                session,
                "save_memory",
                {
                    "type": "convention",
                    "content": "A competing correction.",
                    "importance": 0.9,
                    "confidence": 0.9,
                    "source": source,
                    "supersedes": original["memory"]["memory_id"],
                },
            )
            archived = structured(
                await call(
                    session,
                    "update_memory",
                    {
                        "memory_id": replacement["memory"]["memory_id"],
                        "status": "archived",
                        "expected_revision": replacement["memory"]["revision"],
                    },
                )
            )

    assert replacement["memory"]["supersedes"] == original["memory"]["memory_id"]
    # A second correction of the same memory is refused, so the chain holds.
    assert again.isError is True
    assert "already been superseded" in text(again)
    assert archived["memory"]["status"] == "archived"


@pytest.mark.asyncio
async def test_a_record_outside_its_project_is_reported_as_not_found(run_id: str) -> None:
    async with seeded(run_id) as (application, payment_id, wallet_id):
        async with mcp_session(application) as session:
            found = structured(
                await call(
                    session,
                    "search_context",
                    {"project_id": payment_id, "query": "idempotency"},
                )
            )
            context_id = (
                found["results"][0]["metadata"].get("context_id")
                or found["results"][0]["key"].rsplit(":", 1)[-1]
            )
            foreign = await call(
                session,
                "get_context",
                {"project_id": wallet_id, "context_id": context_id},
            )

    assert foreign.isError is True
    assert "no such record" in text(foreign)


@pytest.mark.asyncio
async def test_the_status_tool_reports_a_live_platform(run_id: str) -> None:
    async with running_application([], f"phase12-status-{run_id}") as application:
        async with mcp_session(application) as session:
            result = await call(session, "get_system_status", {})

    status = structured(result)
    assert status["ready"] is True
    assert status["unavailable"] == []
    expected_dimensions = (
        get_settings().embedding_dimensions if get_settings().embedding_enabled else None
    )
    assert status["embedding_dimensions"] == expected_dimensions
