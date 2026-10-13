"""Real-MongoDB V5 Phase 0 client-profile and Gemini identity verification."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from bson import ObjectId

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.client_profile_schema import CLIENT_PROFILE_COLLECTION
from app.db.indexes import INDEX_DEFINITIONS
from app.db.initializer import initialize_database
from app.models.client_profile import GEMINI_DEFAULT_SCOPES
from app.repositories.agent_repository import MongoAgentClientRepository
from app.repositories.api_key_repository import MongoApiKeyRepository
from app.repositories.client_profile_repository import MongoClientProfileRepository
from app.services.agent_client_service import AgentClientService
from app.services.api_key_service import ApiKeyService
from app.services.auth_service import AuthService
from app.services.client_profile_service import ClientProfileService

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
async def test_v5_profile_assignment_keeps_gemini_on_existing_v4_authentication_path() -> None:
    suffix = str(ObjectId())[-8:]
    profile_id = f"coding-agent-{suffix}"
    client_id = "gemini-cli-main"
    key_id: str | None = None
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            profiles = MongoClientProfileRepository(database)
            clients = MongoAgentClientRepository(database)
            keys = MongoApiKeyRepository(database)
            profile_service = ClientProfileService(profiles, clients)
            client_service = AgentClientService(clients, ApiKeyService(keys))

            profile = await profile_service.create_profile(
                profile_id=profile_id,
                name="V5 coding agent",
                scopes=GEMINI_DEFAULT_SCOPES,
                rate_limit_profile="standard",
            )
            client = await client_service.create_client(
                client_id=client_id,
                client_type="gemini_cli",
                name="V5 Gemini integration",
                owner_id="integration",
                scopes=["search:read"],
                project_ids=["payments"],
            )
            assigned = await profile_service.assign_profile(
                profile.profile_id,
                client_id=client.client_id,
                expected_revision=client.revision,
                project_overrides=["payments"],
            )

            issued = await client_service.create_credential(client_id)
            key_id = issued.record.key_id
            stored_key = await keys.get_by_key_id(key_id)
            assert stored_key is not None
            assert stored_key.client_id == client_id
            assert stored_key.key_hash != issued.token
            assert issued.token not in stored_key.model_dump_json()

            principal = await AuthService(keys, clients).authenticate(issued.token)
            assert principal.client_id == client_id
            assert principal.client_type == "gemini_cli"
            assert principal.allows_scope("context:read")
            assert assigned.profile_id == profile_id
            assert assigned.scopes == profile.scopes
            assert assigned.project_ids == ["payments"]
            high_risk_scopes = {
                "agent:manage",
                "research:approve",
                "research:promote",
                "content:publish",
            }
            assert high_risk_scopes.isdisjoint(assigned.scopes)

            assert (
                await database[CLIENT_PROFILE_COLLECTION].count_documents(
                    {"profile_id": profile_id}
                )
                == 1
            )
            assert (
                await database["agent_clients"].count_documents(
                    {"client_id": client_id, "profile_id": profile_id}
                )
                == 1
            )
            assert await database["_schema_migrations"].count_documents({"version": 33}) == 1
            assert await database["_schema_migrations"].count_documents({"version": 34}) == 1
            information = await database[CLIENT_PROFILE_COLLECTION].index_information()
            expected_indexes = {
                definition.document["name"]
                for definition in INDEX_DEFINITIONS[CLIENT_PROFILE_COLLECTION]
            }
            assert expected_indexes.issubset(information)
    finally:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            if key_id is not None:
                await database["api_keys"].delete_many({"key_id": key_id})
            await database["agent_clients"].delete_many({"client_id": client_id})
            await database[CLIENT_PROFILE_COLLECTION].delete_many({"profile_id": profile_id})
