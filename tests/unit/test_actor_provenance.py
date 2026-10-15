"""Actor provenance follows trusted request identity across V1--V3 writes."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app import provenance
from app.db.client_types import MongoDocument
from app.models.actor import ActorProvenance, ActorType
from app.models.content import ContentIdeaDocument
from app.models.research import CandidateType, KnowledgeCandidateDocument
from app.repositories.content_repository import MongoContentIdeaRepository
from app.repositories.context_repository import MongoProjectContextRepository
from app.repositories.memory_repository import MongoMemoryRepository
from app.repositories.research_repository import MongoKnowledgeCandidateRepository
from app.schemas.content import ContentIdeaResponse
from app.schemas.context import ContextResponse
from app.schemas.memory import MemoryResponse
from app.schemas.research import KnowledgeCandidateResponse
from app.security.principal import Principal
from app.services.audit_service import AuditService
from tests.memory_doubles import memory
from tests.project_doubles import context
from tests.security_doubles import FakeAuditRepository

PROJECT = "payment-service"
RESEARCH_PROJECT = "payment-research"


def _database(collection: MagicMock) -> AsyncDatabase[MongoDocument]:
    database = MagicMock()
    database.__getitem__.return_value = collection
    return cast(AsyncDatabase[MongoDocument], database)


def _agent(client_id: str, client_type: str) -> Principal:
    return Principal(
        key_id=f"{client_id}-key",
        name=client_id,
        scopes=("context:write", "memory:write", "content:write", "research:write"),
        client_id=client_id,
        client_type=client_type,
    )  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_agent_identity_overrides_spoofed_actor_for_v1_v2_and_v3_writes() -> None:
    collection = MagicMock()
    collection.insert_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)
    database = _database(collection)
    spoofed = ActorProvenance(
        actor_type=ActorType.AGENT,
        client_id="claude-code-main",
        client_type="claude_code",
    )
    actor_token = provenance.begin()
    try:
        provenance.set_principal(_agent("codex-main", "codex"))
        context_repository = MongoProjectContextRepository(database, PROJECT)
        v1_context = await context_repository.create(
            context(PROJECT).model_copy(update={"actor": spoofed})
        )
        updated_context_payload = v1_context.model_copy(update={"revision": 2}).model_dump(
            by_alias=True, mode="python"
        )
        collection.find_one_and_update = AsyncMock(return_value=updated_context_payload)
        updated_context = await context_repository.update(
            v1_context.model_copy(update={"actor": spoofed}), expected_revision=1
        )
        v1_memory = await MongoMemoryRepository(database, project_id=None).create(
            memory().model_copy(update={"actor": spoofed})
        )
        v2_idea = await MongoContentIdeaRepository(database, "personal-brand").create(
            ContentIdeaDocument(
                profile_id="personal-brand",
                idea_id="payments",
                title="Payment retries",
                description="A durable idempotency explanation.",
                platforms=["linkedin"],
                origin={"type": "memory"},
                priority=0.7,
                actor=spoofed,
            )
        )
        v3_candidate = await MongoKnowledgeCandidateRepository(database, RESEARCH_PROJECT).create(
            KnowledgeCandidateDocument(
                research_project_id=RESEARCH_PROJECT,
                candidate_id="retry-knowledge",
                candidate_type=CandidateType.MEMORY,
                title="Retry knowledge",
                content="Idempotency keys make payment retries safe.",
                source_claim_ids=["retry-claim"],
                authorization={"project_ids": [PROJECT], "owner_key_id": "owner"},
                actor=spoofed,
            )
        )
    finally:
        provenance.end(actor_token)

    for document in (v1_context, updated_context, v1_memory, v2_idea, v3_candidate):
        assert document.actor.actor_type is ActorType.AGENT
        assert document.actor.client_id == "codex-main"
        assert document.actor.client_type == "codex"
    for response in (
        ContextResponse.from_document(v1_context),
        MemoryResponse.from_document(v1_memory),
        ContentIdeaResponse.from_document(v2_idea),
        KnowledgeCandidateResponse.from_document(v3_candidate),
    ):
        assert response.actor.client_id == "codex-main"
    payloads = [entry.args[0] for entry in collection.insert_one.await_args_list]
    assert all(payload["actor"]["client_id"] == "codex-main" for payload in payloads)
    assert all("token" not in repr(payload) for payload in payloads)
    update = collection.find_one_and_update.await_args.args[1]["$set"]
    assert update["actor"]["client_id"] == "codex-main"


@pytest.mark.asyncio
async def test_human_and_unattributed_audit_writes_have_explicit_actor_types() -> None:
    records = FakeAuditRepository()
    service = AuditService(records)
    collection = MagicMock()
    collection.insert_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)
    database = _database(collection)
    actor_token = provenance.begin()
    try:
        await service.record("context.write", "allowed")
        human = Principal(
            key_id="admin-key",
            name="Administrator",
            scopes=("context:write",),
        )
        provenance.set_principal(human)
        durable_context = await MongoProjectContextRepository(database, PROJECT).create(
            context(PROJECT)
        )
        await service.record(
            "context.write",
            "allowed",
            principal=human,
        )
    finally:
        provenance.end(actor_token)

    assert durable_context.actor.actor_type is ActorType.HUMAN
    assert records.records[0].actor.actor_type is ActorType.SYSTEM
    assert records.records[1].actor.actor_type is ActorType.HUMAN


async def test_actor_model_rejects_incomplete_or_non_agent_client_identity() -> None:
    with pytest.raises(ValueError, match="requires"):
        ActorProvenance(actor_type=ActorType.AGENT)
    with pytest.raises(ValueError, match="only an agent"):
        ActorProvenance(actor_type=ActorType.HUMAN, client_id="codex-main")


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
