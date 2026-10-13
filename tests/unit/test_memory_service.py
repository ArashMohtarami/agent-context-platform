"""Memory lifecycle service tests independent of MongoDB."""

import pytest
from bson import ObjectId

from app.embeddings.errors import EmbeddingUnavailableError
from app.memory.errors import MemoryNotFoundError, MemorySupersessionError
from app.projects.errors import ProjectNotFoundError
from app.repositories.errors import ConcurrencyConflictError
from app.services.memory_service import MemoryService, embedding_text, memory_scope
from tests.memory_doubles import (
    DIMENSIONS,
    MODEL,
    FakeMemoryRepositoryFactory,
    memory,
)
from tests.project_doubles import FakeEmbedder, FakeProjectRepository, project

PROJECT = "sampleco-payment"
OTHER_PROJECT = "sampleco-wallet"
CLAUDE = "claude-code-main"
CODEX = "codex-main"


def build_service(
    *,
    documents: tuple[object, ...] = (),
    embedder: FakeEmbedder | None = None,
    projects: tuple[str, ...] = (PROJECT, OTHER_PROJECT),
) -> tuple[MemoryService, FakeMemoryRepositoryFactory, FakeEmbedder]:
    """Compose the service over in-memory doubles."""
    repository = FakeProjectRepository([project(name) for name in projects])
    factory = FakeMemoryRepositoryFactory(documents)  # type: ignore[arg-type]
    resolved = FakeEmbedder() if embedder is None else embedder
    return MemoryService(repository, factory, resolved), factory, resolved


def test_scope_is_derived_from_the_project_a_memory_names() -> None:
    assert memory_scope(None) == "global"
    assert memory_scope(PROJECT) == "project"
    assert memory_scope(None, client_id=CLAUDE, requested_scope="agent") == "agent"
    assert (
        memory_scope(PROJECT, client_id=CLAUDE, requested_scope="project_agent") == "project_agent"
    )
    with pytest.raises(ValueError, match="requires an authenticated client"):
        memory_scope(None, requested_scope="agent")
    with pytest.raises(ValueError, match="invalid project_id"):
        memory_scope(PROJECT, client_id=CLAUDE, requested_scope="agent")


@pytest.mark.asyncio
async def test_a_global_memory_is_stored_with_every_documented_field() -> None:
    service, factory, _ = build_service()

    created = await service.create_memory(
        memory_type="preference",
        content="Use strict typing in Python projects.",
        summary="Strict typing everywhere",
        importance=0.8,
        confidence=0.95,
        source="user",
        tags=["python", "typing"],
    )

    assert created.scope == "global"
    assert created.project_id is None
    assert created.type == "preference"
    assert created.summary == "Strict typing everywhere"
    assert created.importance == 0.8
    assert created.confidence == 0.95
    assert created.source == "user"
    assert created.tags == ["python", "typing"]
    assert created.status == "active"
    assert created.supersedes is None
    assert created.superseded_by is None
    assert factory(None).documents[created.id] == created


@pytest.mark.asyncio
async def test_a_project_memory_is_stored_in_its_own_scope() -> None:
    service, factory, _ = build_service()

    created = await service.create_memory(
        memory_type="convention",
        content="Payment requests always carry an idempotency key.",
        importance=0.9,
        confidence=0.9,
        source="user",
        project_id=PROJECT,
    )

    assert created.scope == "project"
    assert created.project_id == PROJECT
    assert created.id in factory(PROJECT).documents
    assert created.id not in factory(None).documents


@pytest.mark.asyncio
async def test_agent_memory_is_isolated_and_can_reuse_an_external_id() -> None:
    service, factory, _ = build_service()

    claude = await service.create_memory(
        memory_type="note",
        content="Claude has already inspected the payment retry tests.",
        importance=0.4,
        confidence=0.9,
        source="mcp",
        scope="agent",
        client_id=CLAUDE,
        external_id="retry-tests",
    )
    codex = await service.create_memory(
        memory_type="note",
        content="Codex is currently applying the migration.",
        importance=0.4,
        confidence=0.9,
        source="mcp",
        scope="agent",
        client_id=CODEX,
        external_id="retry-tests",
    )

    assert claude.scope == "agent"
    assert claude.client_id == CLAUDE
    assert codex.id != claude.id
    assert claude.id in factory("agent", None, CLAUDE).documents
    with pytest.raises(MemoryNotFoundError):
        await service.get_memory(claude.id, scope="agent", client_id=CODEX)


@pytest.mark.asyncio
async def test_project_agent_memory_requires_both_owners() -> None:
    service, factory, _ = build_service()

    created = await service.create_memory(
        memory_type="note",
        content="Claude is working through payment-service's release checklist.",
        importance=0.4,
        confidence=0.9,
        source="mcp",
        scope="project_agent",
        project_id=PROJECT,
        client_id=CLAUDE,
    )

    assert created.scope == "project_agent"
    assert created.project_id == PROJECT
    assert created.client_id == CLAUDE
    assert created.id in factory("project_agent", PROJECT, CLAUDE).documents


@pytest.mark.asyncio
async def test_project_memory_requires_a_project_that_exists() -> None:
    service, factory, _ = build_service()

    with pytest.raises(ProjectNotFoundError, match="unknown-project"):
        await service.create_memory(
            memory_type="convention",
            content="Orphaned memory.",
            importance=0.5,
            confidence=0.5,
            source="user",
            project_id="unknown-project",
        )

    assert factory("unknown-project").documents == {}


@pytest.mark.asyncio
async def test_memory_is_embedded_from_its_summary_and_body() -> None:
    embedder = FakeEmbedder()
    service, _, _ = build_service(embedder=embedder)

    created = await service.create_memory(
        memory_type="preference",
        content="Use strict typing in Python projects.",
        summary="Strict typing",
        importance=0.8,
        confidence=0.9,
        source="user",
    )

    assert embedder.texts == [
        embedding_text("Strict typing", "Use strict typing in Python projects.")
    ]
    assert created.embedding is not None
    assert created.embedding_model == MODEL
    assert created.embedding_dimensions == DIMENSIONS


@pytest.mark.asyncio
async def test_memory_without_a_summary_is_embedded_from_its_body_alone() -> None:
    embedder = FakeEmbedder()
    service, _, _ = build_service(embedder=embedder)

    await service.create_memory(
        memory_type="preference",
        content="Prefer production-grade solutions.",
        importance=0.6,
        confidence=0.8,
        source="user",
    )

    assert embedder.texts == ["Prefer production-grade solutions."]


@pytest.mark.asyncio
async def test_a_failing_embedder_stops_the_write() -> None:
    embedder = FakeEmbedder(EmbeddingUnavailableError("model is not loaded"))
    service, factory, _ = build_service(embedder=embedder)

    with pytest.raises(EmbeddingUnavailableError):
        await service.create_memory(
            memory_type="preference",
            content="Use strict typing.",
            importance=0.5,
            confidence=0.5,
            source="user",
        )

    assert factory(None).documents == {}


@pytest.mark.asyncio
async def test_repeating_a_create_with_the_same_key_returns_what_is_stored() -> None:
    service, factory, _ = build_service()

    first = await service.create_memory(
        memory_type="preference",
        content="Use strict typing.",
        importance=0.8,
        confidence=0.9,
        source="user",
        external_id="strict-typing",
    )
    second = await service.create_memory(
        memory_type="preference",
        content="Something else entirely.",
        importance=0.8,
        confidence=0.9,
        source="user",
        external_id="strict-typing",
    )

    assert second.id == first.id
    assert second.content == "Use strict typing."
    assert len(factory(None).documents) == 1


@pytest.mark.asyncio
async def test_a_memory_is_read_back_from_its_own_scope() -> None:
    stored = memory()
    service, _, _ = build_service(documents=(stored,))

    assert (await service.get_memory(stored.id)).content == stored.content


@pytest.mark.asyncio
async def test_one_project_cannot_read_another_project_s_memory() -> None:
    stored = memory(OTHER_PROJECT)
    service, _, _ = build_service(documents=(stored,))

    with pytest.raises(MemoryNotFoundError):
        await service.get_memory(stored.id, project_id=PROJECT)

    assert (await service.get_memory(stored.id, project_id=OTHER_PROJECT)).id == stored.id


@pytest.mark.asyncio
async def test_a_project_cannot_read_project_memory_as_global() -> None:
    stored = memory(PROJECT)
    service, _, _ = build_service(documents=(stored,))

    with pytest.raises(MemoryNotFoundError):
        await service.get_memory(stored.id)


@pytest.mark.asyncio
async def test_missing_memory_is_reported_as_missing() -> None:
    service, _, _ = build_service()

    with pytest.raises(MemoryNotFoundError):
        await service.get_memory(ObjectId())


@pytest.mark.asyncio
async def test_memory_is_listed_with_lifecycle_and_metadata_filters() -> None:
    documents = (
        memory(content="Active preference"),
        memory(content="Archived preference", status="archived"),
        memory(content="Low value note", memory_type="note", tags=("note",), importance=0.1),
    )
    service, _, _ = build_service(documents=documents)

    active = await service.list_memories()
    archived = await service.list_memories(status="archived")
    by_type = await service.list_memories(memory_type="note", status=None)
    important = await service.list_memories(status=None, minimum_importance=0.5)

    assert [item.content for item in active.items] == ["Active preference", "Low value note"]
    assert [item.content for item in archived.items] == ["Archived preference"]
    assert [item.content for item in by_type.items] == ["Low value note"]
    assert {item.content for item in important.items} == {
        "Active preference",
        "Archived preference",
    }


@pytest.mark.asyncio
async def test_an_update_changes_only_the_requested_fields() -> None:
    stored = memory()
    service, _, _ = build_service(documents=(stored,))

    updated = await service.update_memory(stored.id, confidence=0.4)

    assert updated.confidence == 0.4
    assert updated.content == stored.content
    assert updated.importance == stored.importance
    assert updated.revision == stored.revision + 1


@pytest.mark.asyncio
async def test_changing_the_text_re_embeds_the_memory() -> None:
    stored = memory(embedded=True)
    embedder = FakeEmbedder()
    service, _, _ = build_service(documents=(stored,), embedder=embedder)

    updated = await service.update_memory(stored.id, content="Use strict typing and mypy.")

    assert embedder.texts == ["Use strict typing and mypy."]
    assert updated.embedding != stored.embedding


@pytest.mark.asyncio
async def test_changing_metadata_alone_leaves_the_vector_alone() -> None:
    stored = memory(embedded=True)
    embedder = FakeEmbedder()
    service, _, _ = build_service(documents=(stored,), embedder=embedder)

    updated = await service.update_memory(stored.id, importance=0.2)

    assert embedder.texts == []
    assert updated.embedding == stored.embedding


@pytest.mark.asyncio
async def test_lifecycle_transitions_are_applied() -> None:
    stored = memory()
    service, _, _ = build_service(documents=(stored,))

    archived = await service.update_memory(stored.id, status="archived")
    reactivated = await service.update_memory(
        stored.id,
        status="active",
        expected_revision=archived.revision,
    )

    assert archived.status == "archived"
    assert reactivated.status == "active"


@pytest.mark.asyncio
async def test_a_stale_revision_is_refused_instead_of_overwriting() -> None:
    stored = memory()
    service, _, _ = build_service(documents=(stored,))
    await service.update_memory(stored.id, importance=0.3)

    with pytest.raises(ConcurrencyConflictError):
        await service.update_memory(
            stored.id,
            importance=0.4,
            expected_revision=stored.revision,
        )


@pytest.mark.asyncio
async def test_one_project_cannot_update_another_project_s_memory() -> None:
    stored = memory(OTHER_PROJECT)
    service, _, _ = build_service(documents=(stored,))

    with pytest.raises(MemoryNotFoundError):
        await service.update_memory(stored.id, project_id=PROJECT, importance=0.1)


@pytest.mark.asyncio
async def test_an_update_with_nothing_to_change_leaves_the_memory_alone() -> None:
    stored = memory()
    service, _, _ = build_service(documents=(stored,))

    assert await service.update_memory(stored.id) == stored


@pytest.mark.asyncio
async def test_conflicting_knowledge_supersedes_rather_than_deletes() -> None:
    stored = memory(content="Deploys happen on Fridays.")
    service, factory, _ = build_service(documents=(stored,))

    replacement = await service.create_memory(
        memory_type="convention",
        content="Deploys never happen on Fridays.",
        importance=0.9,
        confidence=0.95,
        source="user",
        supersedes=stored.id,
    )
    replaced = await service.get_memory(stored.id)

    assert replacement.supersedes == stored.id
    assert replacement.status == "active"
    # The old memory is still stored, still readable, and points forward.
    assert replaced.status == "superseded"
    assert replaced.superseded_by == replacement.id
    assert replaced.content == "Deploys happen on Fridays."
    assert len(factory(None).documents) == 2


@pytest.mark.asyncio
async def test_superseding_works_inside_a_project_scope() -> None:
    stored = memory(PROJECT, content="Retries are unlimited.")
    service, _, _ = build_service(documents=(stored,))

    replacement = await service.create_memory(
        memory_type="convention",
        content="Retries stop after three attempts.",
        importance=0.9,
        confidence=0.9,
        source="user",
        project_id=PROJECT,
        supersedes=stored.id,
    )
    replaced = await service.get_memory(stored.id, project_id=PROJECT)

    assert replacement.project_id == PROJECT
    assert replaced.superseded_by == replacement.id


@pytest.mark.asyncio
async def test_a_memory_cannot_supersede_one_from_another_scope() -> None:
    stored = memory(OTHER_PROJECT)
    service, factory, _ = build_service(documents=(stored,))

    with pytest.raises(MemoryNotFoundError, match="supersede"):
        await service.create_memory(
            memory_type="convention",
            content="A correction from the wrong project.",
            importance=0.5,
            confidence=0.5,
            source="user",
            project_id=PROJECT,
            supersedes=stored.id,
        )

    assert factory(PROJECT).documents == {}
    assert (await service.get_memory(stored.id, project_id=OTHER_PROJECT)).status == "active"


@pytest.mark.asyncio
async def test_superseding_an_unknown_memory_is_refused() -> None:
    service, factory, _ = build_service()

    with pytest.raises(MemoryNotFoundError, match="supersede"):
        await service.create_memory(
            memory_type="convention",
            content="A correction to nothing.",
            importance=0.5,
            confidence=0.5,
            source="user",
            supersedes=ObjectId(),
        )

    assert factory(None).documents == {}


@pytest.mark.asyncio
async def test_a_memory_cannot_be_superseded_twice() -> None:
    stored = memory(content="Deploys happen on Fridays.")
    service, _, _ = build_service(documents=(stored,))
    await service.create_memory(
        memory_type="convention",
        content="Deploys never happen on Fridays.",
        importance=0.9,
        confidence=0.9,
        source="user",
        supersedes=stored.id,
    )

    with pytest.raises(MemorySupersessionError, match="already been superseded"):
        await service.create_memory(
            memory_type="convention",
            content="A second, competing correction.",
            importance=0.9,
            confidence=0.9,
            source="user",
            supersedes=stored.id,
        )


@pytest.mark.asyncio
async def test_an_already_superseded_memory_cannot_be_replaced_again() -> None:
    stored = memory(status="superseded")
    service, _, _ = build_service(documents=(stored,))

    with pytest.raises(MemorySupersessionError, match="already been superseded"):
        await service.create_memory(
            memory_type="convention",
            content="A correction to stale knowledge.",
            importance=0.5,
            confidence=0.5,
            source="user",
            supersedes=stored.id,
        )


@pytest.mark.asyncio
async def test_a_replacement_survives_a_failure_to_mark_the_old_memory() -> None:
    stored = memory(content="Deploys happen on Fridays.")
    service, factory, _ = build_service(documents=(stored,))
    repository = factory(None)

    # Someone edits the old memory between the read and the link being written.
    original_update = repository.update

    async def conflicting_update(document: object, *, expected_revision: int) -> object:
        if getattr(document, "id", None) == stored.id:
            raise ConcurrencyConflictError("document was concurrently modified")
        return await original_update(document, expected_revision=expected_revision)  # type: ignore[arg-type]

    repository.update = conflicting_update  # type: ignore[assignment,method-assign]

    with pytest.raises(MemorySupersessionError, match="was stored"):
        await service.create_memory(
            memory_type="convention",
            content="Deploys never happen on Fridays.",
            importance=0.9,
            confidence=0.9,
            source="user",
            supersedes=stored.id,
        )

    # The new knowledge was kept and the old memory is still readable: the
    # failure costs a link, never a memory.
    assert len(repository.documents) == 2
    assert repository.documents[stored.id].status == "active"
