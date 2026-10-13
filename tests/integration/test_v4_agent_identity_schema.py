"""Real-MongoDB verification for V4 Phase 0 identity persistence."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId
from pymongo.errors import WriteError

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.indexes import INDEX_DEFINITIONS
from app.db.initializer import initialize_database
from app.db.migrations.v001_initial_schema import V1_COLLECTIONS
from app.db.migrations.v006_creator_content_schema import V2_CONTENT_COLLECTIONS
from app.db.migrations.v013_research_schema import V3_RESEARCH_COLLECTIONS
from app.db.migrations.v022_agent_identity_schema import V4_AGENT_COLLECTIONS
from app.models.agent import (
    AgentActivityDocument,
    AgentClientDocument,
    AgentSessionDocument,
    ProjectBindingDocument,
)
from app.repositories.agent_repository import (
    MongoAgentActivityRepository,
    MongoAgentClientRepository,
    MongoAgentSessionRepository,
    MongoProjectBindingRepository,
)
from app.repositories.errors import DuplicateDocumentError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]


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
async def test_v4_identity_schema_is_idempotent_and_preserves_v1_v2_v3_collections() -> None:
    suffix = str(ObjectId())[-8:]
    client_id = f"claude-{suffix}"
    session_id = f"session-{suffix}"
    project_id = f"project-{suffix}"
    binding_id = f"binding-{suffix}"
    event_id = f"event-{suffix}"
    now = datetime(2026, 10, 5, tzinfo=UTC)

    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            await initialize_database(database)

            names = set(await database.list_collection_names())
            assert set(V1_COLLECTIONS).issubset(names)
            assert set(V2_CONTENT_COLLECTIONS).issubset(names)
            assert set(V3_RESEARCH_COLLECTIONS).issubset(names)
            assert set(V4_AGENT_COLLECTIONS).issubset(names)
            assert await database["_schema_migrations"].count_documents({"version": 22}) == 1
            assert await database["_schema_migrations"].count_documents({"version": 23}) == 1
            assert await database["_schema_migrations"].count_documents({"version": 29}) == 1
            assert await database["_schema_migrations"].count_documents({"version": 30}) == 1
            for collection in V4_AGENT_COLLECTIONS:
                information = await database[collection].index_information()
                expected = {
                    definition.document["name"] for definition in INDEX_DEFINITIONS[collection]
                }
                assert expected.issubset(information)

            client = AgentClientDocument(
                client_id=client_id,
                client_type="claude_code",
                name="V4 schema client",
                owner_id="owner",
                scopes=["skills:read", "context:read"],
                project_ids=[project_id],
                metadata={"test": "v4-schema"},
                last_seen_at=now,
            )
            session = AgentSessionDocument(
                session_id=session_id,
                client_id=client_id,
                client_type="claude_code",
                project_id=project_id,
                repository=f"git@github.com:example/{project_id}.git",
                started_at=now,
                last_seen_at=now,
                metadata={"test": "v4-schema"},
            )
            binding = ProjectBindingDocument(
                binding_id=binding_id,
                project_id=project_id,
                binding_type="git_remote",
                value=f"git@github.com:example/{project_id}.git",
            )
            activity = AgentActivityDocument(
                event_id=event_id,
                client_id=client_id,
                client_type="claude_code",
                session_id=session_id,
                operation="tool_call",
                tool="search_context",
                project_id=project_id,
                resource_type="project_context",
                resource_id="retry-policy",
                status="succeeded",
                latency_ms=3.5,
            )
            for collection, document in (
                ("agent_clients", client),
                ("agent_sessions", session),
                ("project_bindings", binding),
                ("agent_activity", activity),
            ):
                await database[collection].insert_one(
                    document.model_dump(by_alias=True, mode="python")
                )

            assert await database["agent_clients"].count_documents({"client_id": client_id}) == 1
            assert await database["agent_sessions"].count_documents({"session_id": session_id}) == 1
            assert (
                await database["project_bindings"].count_documents({"binding_id": binding_id}) == 1
            )
            assert await database["agent_activity"].count_documents({"event_id": event_id}) == 1

            invalid = client.model_dump(by_alias=True, mode="python")
            invalid["_id"] = ObjectId()
            invalid["client_id"] = f"invalid-{suffix}"
            invalid["client_type"] = "unsupported-agent"
            with pytest.raises(WriteError):
                await database["agent_clients"].insert_one(invalid)
    finally:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            await database["agent_clients"].delete_many({"client_id": client_id})
            await database["agent_sessions"].delete_many({"session_id": session_id})
            await database["project_bindings"].delete_many({"binding_id": binding_id})
            await database["agent_activity"].delete_many({"event_id": event_id})


@pytest.mark.asyncio
async def test_v4_identity_repositories_paginate_and_reject_conflicting_project_mappings() -> None:
    suffix = str(ObjectId())[-8:]
    project_id = f"project-{suffix}"
    client_ids = [f"codex-{suffix}-a", f"codex-{suffix}-b"]
    binding_id = f"binding-{suffix}"
    session_id = f"session-{suffix}"
    event_id = f"event-{suffix}"
    now = datetime(2026, 10, 5, tzinfo=UTC)

    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            clients = MongoAgentClientRepository(database)
            sessions = MongoAgentSessionRepository(database)
            bindings = MongoProjectBindingRepository(database)
            activity = MongoAgentActivityRepository(database)

            for index, client_id in enumerate(client_ids):
                created_at = now + timedelta(seconds=index)
                await clients.create(
                    AgentClientDocument(
                        client_id=client_id,
                        client_type="codex",
                        name=f"V4 repository client {index}",
                        owner_id="owner",
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )
            first_page = await clients.list(owner_id="owner", limit=1)
            assert len(first_page.items) == 1
            assert first_page.next_cursor is not None
            second_page = await clients.list(
                owner_id="owner", limit=1, cursor=first_page.next_cursor
            )
            assert {client.client_id for client in (*first_page.items, *second_page.items)} == set(
                client_ids
            )

            binding = await bindings.create(
                ProjectBindingDocument(
                    binding_id=binding_id,
                    project_id=project_id,
                    binding_type="git_remote",
                    value=f"git@github.com:example/{project_id}.git",
                )
            )
            assert await bindings.get_mapping(binding.binding_type, binding.value) == binding
            with pytest.raises(DuplicateDocumentError):
                await bindings.create(
                    ProjectBindingDocument(
                        binding_id=f"conflict-{suffix}",
                        project_id=f"other-{suffix}",
                        binding_type="git_remote",
                        value=binding.value,
                    )
                )

            ended_at = now + timedelta(minutes=2)
            session = await sessions.create(
                AgentSessionDocument(
                    session_id=session_id,
                    client_id=client_ids[0],
                    client_type="codex",
                    project_id=project_id,
                    started_at=now,
                    last_seen_at=now + timedelta(minutes=1),
                    ended_at=ended_at,
                    status="ended",
                )
            )
            assert await sessions.get(session_id) == session
            recorded = await activity.create(
                AgentActivityDocument(
                    event_id=event_id,
                    client_id=client_ids[0],
                    client_type="codex",
                    session_id=session_id,
                    operation="tool_call",
                    tool="search_context",
                    project_id=project_id,
                    status="succeeded",
                )
            )
            assert await activity.get(event_id) == recorded
    finally:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            await database["agent_clients"].delete_many({"client_id": {"$in": client_ids}})
            await database["agent_sessions"].delete_many({"session_id": session_id})
            await database["project_bindings"].delete_many({"binding_id": binding_id})
            await database["agent_activity"].delete_many({"event_id": event_id})
