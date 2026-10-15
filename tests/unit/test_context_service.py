"""Project-context lifecycle service tests independent of MongoDB."""

import pytest
from bson import ObjectId

from app.embeddings.errors import EmbeddingUnavailableError
from app.projects.errors import ContextNotFoundError, ProjectNotFoundError
from app.repositories.errors import ConcurrencyConflictError
from app.services.context_service import ContextService, embedding_text
from tests.project_doubles import (
    DIMENSIONS,
    MODEL,
    FakeContextRepositoryFactory,
    FakeEmbedder,
    FakeProjectRepository,
    context,
    project,
)

PROJECT = "sampleco-payment"
OTHER_PROJECT = "sampleco-wallet"


def build_service(
    *,
    projects: tuple[str, ...] = (PROJECT, OTHER_PROJECT),
    documents: tuple[object, ...] = (),
    embedder: FakeEmbedder | None = None,
) -> tuple[ContextService, FakeContextRepositoryFactory, FakeEmbedder | None]:
    """Compose the service over in-memory doubles."""
    repository = FakeProjectRepository([project(name) for name in projects])
    factory = FakeContextRepositoryFactory(documents)  # type: ignore[arg-type]
    resolved = FakeEmbedder() if embedder is None else embedder
    return ContextService(repository, factory, resolved), factory, resolved


@pytest.mark.asyncio
async def test_context_is_stored_with_every_documented_field() -> None:
    service, factory, _ = build_service()

    created = await service.create_context(
        PROJECT,
        context_type="architecture_decision",
        title="Payment idempotency strategy",
        content="Payment requests carry a UUID4 idempotency key.",
        importance=0.95,
        source="user",
        tags=["payments", "idempotency"],
    )

    assert created.project_id == PROJECT
    assert created.type == "architecture_decision"
    assert created.title == "Payment idempotency strategy"
    assert created.importance == 0.95
    assert created.tags == ["payments", "idempotency"]
    assert created.source == "user"
    assert created.status == "active"
    assert factory(PROJECT).documents[created.id] == created


@pytest.mark.asyncio
async def test_every_supported_context_type_is_accepted() -> None:
    service, _, _ = build_service()
    types = [
        "architecture",
        "architecture_decision",
        "business_rule",
        "api",
        "database",
        "infrastructure",
        "deployment",
        "security",
        "integration",
        "convention",
        "troubleshooting",
        "known_issue",
        "implementation",
        "constraint",
    ]

    for index, context_type in enumerate(types):
        created = await service.create_context(
            PROJECT,
            context_type=context_type,  # type: ignore[arg-type]
            title=f"Entry {index}",
            content=f"Content for {context_type}",
            importance=0.5,
            source="user",
        )
        assert created.type == context_type


@pytest.mark.asyncio
async def test_context_is_embedded_from_its_title_and_body() -> None:
    embedder = FakeEmbedder()
    service, _, _ = build_service(embedder=embedder)

    created = await service.create_context(
        PROJECT,
        context_type="api",
        title="Payment API",
        content="POST /api/v1/payments creates one payment.",
        importance=0.6,
        source="user",
    )

    assert embedder.texts == [
        embedding_text("Payment API", "POST /api/v1/payments creates one payment.")
    ]
    assert created.embedding is not None
    assert created.embedding_model == MODEL
    assert created.embedding_dimensions == DIMENSIONS
    assert created.embedding_generated_at is not None


@pytest.mark.asyncio
async def test_context_is_stored_unembedded_when_no_model_is_configured() -> None:
    repository = FakeProjectRepository([project(PROJECT)])
    factory = FakeContextRepositoryFactory()
    service = ContextService(repository, factory, None)

    created = await service.create_context(
        PROJECT,
        context_type="convention",
        title="Naming",
        content="Use snake_case for MongoDB fields.",
        importance=0.4,
        source="user",
    )

    assert created.has_embedding is False


@pytest.mark.asyncio
async def test_a_failing_embedder_stops_the_write() -> None:
    embedder = FakeEmbedder(EmbeddingUnavailableError("model is not loaded"))
    service, factory, _ = build_service(embedder=embedder)

    with pytest.raises(EmbeddingUnavailableError):
        await service.create_context(
            PROJECT,
            context_type="api",
            title="Payment API",
            content="POST /api/v1/payments creates one payment.",
            importance=0.6,
            source="user",
        )

    assert factory(PROJECT).documents == {}


@pytest.mark.asyncio
async def test_context_requires_a_project_that_exists() -> None:
    service, factory, _ = build_service()

    with pytest.raises(ProjectNotFoundError, match="unknown-project"):
        await service.create_context(
            "unknown-project",
            context_type="api",
            title="Orphan",
            content="This belongs to nothing.",
            importance=0.5,
            source="user",
        )

    assert factory("unknown-project").documents == {}


@pytest.mark.asyncio
async def test_repeating_a_create_with_the_same_key_returns_what_is_stored() -> None:
    service, factory, _ = build_service()

    first = await service.create_context(
        PROJECT,
        context_type="business_rule",
        title="Refund window",
        content="Refunds are accepted for 30 days.",
        importance=0.8,
        source="user",
        external_id="refund-window",
    )
    second = await service.create_context(
        PROJECT,
        context_type="business_rule",
        title="Refund window",
        content="Refunds are accepted for 60 days.",
        importance=0.8,
        source="user",
        external_id="refund-window",
    )

    assert second.id == first.id
    # The stored content is what the first write said, not the repeat's.
    assert second.content == "Refunds are accepted for 30 days."
    assert len(factory(PROJECT).documents) == 1


@pytest.mark.asyncio
async def test_one_project_cannot_read_another_project_s_context() -> None:
    stored = context(OTHER_PROJECT)
    service, _, _ = build_service(documents=(stored,))

    with pytest.raises(ContextNotFoundError):
        await service.get_context(PROJECT, stored.id)

    assert (await service.get_context(OTHER_PROJECT, stored.id)).id == stored.id


@pytest.mark.asyncio
async def test_missing_context_is_reported_as_missing() -> None:
    service, _, _ = build_service()

    with pytest.raises(ContextNotFoundError):
        await service.get_context(PROJECT, ObjectId())


@pytest.mark.asyncio
async def test_context_is_listed_with_lifecycle_and_metadata_filters() -> None:
    documents = (
        context(PROJECT, title="Active decision"),
        context(PROJECT, title="Archived decision", status="archived"),
        context(PROJECT, title="API note", context_type="api", tags=("api",), importance=0.2),
    )
    service, _, _ = build_service(documents=documents)

    active = await service.list_context(PROJECT)
    archived = await service.list_context(PROJECT, status="archived")
    by_type = await service.list_context(PROJECT, context_type="api", status=None)
    important = await service.list_context(PROJECT, status=None, minimum_importance=0.9)

    assert [item.title for item in active.items] == ["Active decision", "API note"]
    assert [item.title for item in archived.items] == ["Archived decision"]
    assert [item.title for item in by_type.items] == ["API note"]
    assert {item.title for item in important.items} == {"Active decision", "Archived decision"}


@pytest.mark.asyncio
async def test_an_update_changes_only_the_requested_fields() -> None:
    stored = context(PROJECT)
    service, _, _ = build_service(documents=(stored,))

    updated = await service.update_context(PROJECT, stored.id, importance=0.2)

    assert updated.importance == 0.2
    assert updated.title == stored.title
    assert updated.content == stored.content
    assert updated.tags == stored.tags
    assert updated.revision == stored.revision + 1


@pytest.mark.asyncio
async def test_changing_the_text_re_embeds_the_document() -> None:
    stored = context(PROJECT, embedded=True)
    embedder = FakeEmbedder()
    service, _, _ = build_service(documents=(stored,), embedder=embedder)

    updated = await service.update_context(
        PROJECT,
        stored.id,
        content="Payment requests now carry a ULID idempotency key.",
    )

    assert embedder.texts == [
        embedding_text(stored.title, "Payment requests now carry a ULID idempotency key.")
    ]
    assert updated.embedding != stored.embedding
    assert updated.embedding_generated_at is not None


@pytest.mark.asyncio
async def test_changing_metadata_alone_leaves_the_vector_alone() -> None:
    stored = context(PROJECT, embedded=True)
    embedder = FakeEmbedder()
    service, _, _ = build_service(documents=(stored,), embedder=embedder)

    updated = await service.update_context(PROJECT, stored.id, tags=["payments"])

    assert embedder.texts == []
    assert updated.embedding == stored.embedding


@pytest.mark.asyncio
async def test_lifecycle_transitions_are_applied() -> None:
    stored = context(PROJECT)
    service, _, _ = build_service(documents=(stored,))

    archived = await service.update_context(PROJECT, stored.id, status="archived")
    superseded = await service.update_context(
        PROJECT,
        stored.id,
        status="superseded",
        expected_revision=archived.revision,
    )

    assert archived.status == "archived"
    assert superseded.status == "superseded"


@pytest.mark.asyncio
async def test_a_stale_revision_is_refused_instead_of_overwriting() -> None:
    stored = context(PROJECT)
    service, _, _ = build_service(documents=(stored,))
    await service.update_context(PROJECT, stored.id, importance=0.3)

    with pytest.raises(ConcurrencyConflictError):
        await service.update_context(
            PROJECT,
            stored.id,
            importance=0.4,
            expected_revision=stored.revision,
        )


@pytest.mark.asyncio
async def test_one_project_cannot_update_another_project_s_context() -> None:
    stored = context(OTHER_PROJECT)
    service, _, _ = build_service(documents=(stored,))

    with pytest.raises(ContextNotFoundError):
        await service.update_context(PROJECT, stored.id, importance=0.1)


@pytest.mark.asyncio
async def test_an_update_with_nothing_to_change_leaves_the_document_alone() -> None:
    stored = context(PROJECT)
    service, _, _ = build_service(documents=(stored,))

    assert await service.update_context(PROJECT, stored.id) == stored


@pytest.mark.asyncio
async def test_an_invalid_update_is_rejected_before_storage() -> None:
    stored = context(PROJECT)
    service, factory, _ = build_service(documents=(stored,))

    with pytest.raises(ValueError, match="importance"):
        await service.update_context(PROJECT, stored.id, importance=5.0)

    assert factory(PROJECT).documents[stored.id] == stored


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
