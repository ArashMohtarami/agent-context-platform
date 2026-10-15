"""V4 Phase 0 repository boundaries and cursor pagination."""

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.models.agent import (
    AgentActivityDocument,
    AgentClientDocument,
    AgentSessionDocument,
    ProjectBindingDocument,
    ProjectBindingType,
)
from app.repositories.agent_repository import (
    MongoAgentActivityRepository,
    MongoAgentClientRepository,
    MongoAgentSessionRepository,
    MongoProjectBindingRepository,
)


def _database(collection: MagicMock) -> AsyncDatabase[MongoDocument]:
    database = MagicMock()
    database.__getitem__.return_value = collection
    return cast(AsyncDatabase[MongoDocument], database)


def _client(index: int) -> AgentClientDocument:
    created_at = datetime(2026, 10, 5, 9, index, tzinfo=UTC)
    return AgentClientDocument(
        org_id="org-test",
        project_mode="include" if [] else "all_in_org",
        client_id=f"client-{index}",
        client_type="codex",
        name=f"Codex {index}",
        owner_id="owner",
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.mark.asyncio
async def test_client_repository_pages_by_cursor_with_identity_filters() -> None:
    collection = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    clients = [_client(1), _client(2)]
    cursor.to_list = AsyncMock(
        return_value=[client.model_dump(by_alias=True, mode="python") for client in clients]
    )
    collection.find.return_value = cursor
    repository = MongoAgentClientRepository(_database(collection))

    page = await repository.list(owner_id="owner", status="active", limit=1)

    assert [client.client_id for client in page.items] == ["client-1"]
    assert page.next_cursor is not None
    assert collection.find.call_args.args[0] == {
        "owner_id": "owner",
        "status": "active",
        "org_id": "org-test",
    }
    cursor.limit.assert_called_once_with(2)


@pytest.mark.asyncio
async def test_identity_repositories_use_narrow_exact_filters() -> None:
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=None)
    database = _database(collection)

    clients = MongoAgentClientRepository(database)
    sessions = MongoAgentSessionRepository(database)
    bindings = MongoProjectBindingRepository(database)
    activity = MongoAgentActivityRepository(database)

    assert await clients.get("codex-main") is None
    assert collection.find_one.await_args.args[0] == {
        "client_id": "codex-main",
        "org_id": "org-test",
    }

    assert await sessions.get("session-1") is None
    assert collection.find_one.await_args.args[0] == {
        "session_id": "session-1",
        "org_id": "org-test",
    }

    assert (
        await bindings.get_mapping(ProjectBindingType.GIT_REMOTE, "git@example.test:payments.git")
        is None
    )
    assert collection.find_one.await_args.args[0] == {
        "binding_type": "git_remote",
        "value": "git@example.test:payments.git",
        "org_id": "org-test",
    }

    assert await activity.get("event-1") is None
    assert collection.find_one.await_args.args[0] == {"event_id": "event-1", "org_id": "org-test"}


@pytest.mark.asyncio
async def test_repository_binding_lookup_uses_the_cross_protocol_canonical_index() -> None:
    collection = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    collection.find.return_value = cursor
    repository = MongoProjectBindingRepository(_database(collection))

    matches = await repository.find_repository_mappings("github.com/example/payment-service")

    assert matches == ()
    assert collection.find.call_args.args[0] == {
        "repository_locator": "github.com/example/payment-service",
        "org_id": "org-test",
    }


@pytest.mark.asyncio
async def test_session_binding_and_activity_repositories_preserve_serializable_documents() -> None:
    collection = MagicMock()
    collection.insert_one = AsyncMock()
    database = _database(collection)
    now = datetime(2026, 10, 5, tzinfo=UTC)

    session = AgentSessionDocument(
        session_id="session-1",
        client_id="codex-main",
        client_type="codex",
        started_at=now,
        last_seen_at=now + timedelta(seconds=1),
    )
    binding = ProjectBindingDocument(
        binding_id="payments-remote",
        project_id="payments",
        binding_type="git_remote",
        value="git@example.test:payments.git",
    )
    activity = AgentActivityDocument(
        event_id="event-1",
        client_id="codex-main",
        client_type="codex",
        operation="tool_call",
        status="succeeded",
    )

    created_session = await MongoAgentSessionRepository(database).create(session)
    created_binding = await MongoProjectBindingRepository(database).create(binding)
    created_activity = await MongoAgentActivityRepository(database).create(activity)

    assert created_session.session_id == session.session_id
    assert created_binding.binding_id == binding.binding_id
    assert created_activity.event_id == activity.event_id
    assert collection.insert_one.await_count == 3


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
