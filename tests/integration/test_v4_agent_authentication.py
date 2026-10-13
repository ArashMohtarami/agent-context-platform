"""Live MongoDB verification of V4 Phase 1 client-linked API credentials."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from bson import ObjectId

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.indexes import INDEX_DEFINITIONS
from app.db.initializer import initialize_database
from app.models.agent import AgentClientDocument
from app.repositories.agent_repository import MongoAgentClientRepository
from app.repositories.api_key_repository import MongoApiKeyRepository
from app.security.principal import AuthenticationError
from app.services.agent_client_service import AgentClientService
from app.services.api_key_service import ApiKeyService
from app.services.auth_service import AuthService

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
async def test_codex_and_claude_credentials_are_independent_and_revoke_independently() -> None:
    suffix = str(ObjectId())[-8:]
    codex_id = f"codex-v4-{suffix}"
    claude_id = f"claude-v4-{suffix}"
    client_ids = [codex_id, claude_id]
    key_ids: list[str] = []
    try:
        async with initialized_mongodb() as mongodb:
            database = mongodb.database
            clients = MongoAgentClientRepository(database)
            api_keys = MongoApiKeyRepository(database)
            service = AgentClientService(clients, ApiKeyService(api_keys))

            codex = await service.create_client(
                client_id=codex_id,
                client_type="codex",
                name="V4 Codex integration",
                owner_id="integration",
                scopes=["search:read", "context:read"],
                project_ids=["payments"],
            )
            claude = await service.create_client(
                client_id=claude_id,
                client_type="claude_code",
                name="V4 Claude Code integration",
                owner_id="integration",
                scopes=["search:read"],
                project_ids=["wallet"],
            )
            assert isinstance(codex, AgentClientDocument)
            assert isinstance(claude, AgentClientDocument)

            codex_credential = await service.create_credential(codex_id)
            claude_credential = await service.create_credential(claude_id)
            key_ids.extend([codex_credential.record.key_id, claude_credential.record.key_id])
            stored_codex = await api_keys.get_by_key_id(codex_credential.record.key_id)
            assert stored_codex is not None
            assert stored_codex.client_id == codex_id
            assert stored_codex.key_hash != codex_credential.token
            assert codex_credential.token not in stored_codex.model_dump_json()

            auth = AuthService(api_keys, clients)
            codex_principal = await auth.authenticate(codex_credential.token)
            claude_principal = await auth.authenticate(claude_credential.token)
            assert codex_principal.client_id == codex_id
            assert codex_principal.allows_project("payments")
            assert not codex_principal.allows_project("wallet")
            assert claude_principal.client_id == claude_id

            await service.revoke_client(claude_id)

            with pytest.raises(AuthenticationError):
                await auth.authenticate(claude_credential.token)
            assert (await auth.authenticate(codex_credential.token)).client_id == codex_id
            revoked = await api_keys.get_by_key_id(claude_credential.record.key_id)
            assert revoked is not None
            assert revoked.status == "revoked"
            assert revoked.revoked_at is not None
            seen = await clients.get(codex_id)
            assert seen is not None
            assert seen.last_seen_at is not None

            indexes = await database["api_keys"].index_information()
            definitions = INDEX_DEFINITIONS["api_keys"]
            expected = {definition.document["name"] for definition in definitions}
            assert expected.issubset(indexes)
            assert await database["_schema_migrations"].count_documents({"version": 24}) == 1
    finally:
        async with initialized_mongodb() as mongodb:
            await mongodb.database["api_keys"].delete_many({"key_id": {"$in": key_ids}})
            await mongodb.database["agent_clients"].delete_many({"client_id": {"$in": client_ids}})
