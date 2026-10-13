"""FastAPI MongoDB and embedding startup and shutdown lifecycle tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.db.errors import SearchUnavailableError
from app.embeddings.errors import EmbeddingUnavailableError
from app.embeddings.types import EmbeddingModelInfo
from app.main import create_app, start_embeddings

MODEL_INFO = EmbeddingModelInfo(
    model="stub-embedding-model",
    dimensions=384,
    device="cpu",
    normalized=True,
)
CONTENT_INDEX_ACTIONS = ("created", "created", "created")
RESEARCH_INDEX_ACTIONS = ("created", "created", "created", "created", "created")


def build_mongodb_manager() -> MagicMock:
    """Build a MongoDB client double with async lifecycle methods."""
    manager = MagicMock()
    manager.connect = AsyncMock()
    manager.close = AsyncMock()
    manager.database = MagicMock()
    return manager


def build_embedding_service() -> MagicMock:
    """Build an embedding service double with async lifecycle methods."""
    embeddings = MagicMock()
    embeddings.model = "stub-embedding-model"
    embeddings.start = AsyncMock(return_value=MODEL_INFO)
    embeddings.close = AsyncMock()
    return embeddings


@pytest.mark.asyncio
async def test_lifespan_starts_and_closes_mongodb_and_embeddings(settings: Settings) -> None:
    manager = build_mongodb_manager()
    embeddings = build_embedding_service()

    with (
        patch("app.main.MongoDBClient", return_value=manager),
        patch("app.main.initialize_database", new=AsyncMock()) as initialize_database,
        patch("app.main.build_embedding_service", return_value=embeddings),
        patch(
            "app.main.ensure_vector_indexes",
            new=AsyncMock(return_value="created"),
        ) as ensure_vector_indexes,
        patch("app.main.ensure_text_indexes", new=AsyncMock(return_value="created")),
        patch("app.main.ensure_context_text_index", new=AsyncMock(return_value="created")),
        patch("app.main.ensure_context_vector_index", new=AsyncMock(return_value="created")),
        patch("app.main.ensure_memory_text_index", new=AsyncMock(return_value="created")),
        patch("app.main.ensure_memory_vector_index", new=AsyncMock(return_value="created")),
        patch(
            "app.main.ensure_content_text_indexes",
            new=AsyncMock(return_value=CONTENT_INDEX_ACTIONS),
        ) as ensure_content_text_indexes,
        patch(
            "app.main.ensure_research_text_indexes",
            new=AsyncMock(return_value=RESEARCH_INDEX_ACTIONS),
        ) as ensure_research_text_indexes,
        patch(
            "app.main.ensure_content_vector_indexes",
            new=AsyncMock(return_value=CONTENT_INDEX_ACTIONS),
        ) as ensure_content_vector_indexes,
        patch(
            "app.main.ensure_research_vector_indexes",
            new=AsyncMock(return_value=RESEARCH_INDEX_ACTIONS),
        ) as ensure_research_vector_indexes,
        patch("app.main.configure_logging") as configure_logging,
    ):
        application = create_app(settings)
        async with application.router.lifespan_context(application):
            assert application.state.mongodb is manager
            assert application.state.settings is settings
            assert application.state.embeddings is embeddings

    configure_logging.assert_called_once_with(settings.log_level)
    manager.connect.assert_awaited_once_with()
    initialize_database.assert_awaited_once_with(manager.database)
    embeddings.start.assert_awaited_once_with()
    ensure_vector_indexes.assert_awaited_once_with(manager.database, dimensions=384)
    ensure_content_text_indexes.assert_awaited_once_with(manager.database)
    ensure_research_text_indexes.assert_awaited_once_with(manager.database)
    ensure_content_vector_indexes.assert_awaited_once_with(manager.database, dimensions=384)
    ensure_research_vector_indexes.assert_awaited_once_with(manager.database, dimensions=384)
    # V3 index setup is additive; existing V1/V2 startup calls remain intact.
    embeddings.close.assert_awaited_once_with()
    manager.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_lifespan_runs_without_an_embedding_service(settings: Settings) -> None:
    manager = build_mongodb_manager()

    with (
        patch("app.main.MongoDBClient", return_value=manager),
        patch("app.main.initialize_database", new=AsyncMock()),
        patch("app.main.build_embedding_service", return_value=None),
        patch("app.main.ensure_vector_indexes", new=AsyncMock()) as ensure_vector_indexes,
        patch("app.main.ensure_text_indexes", new=AsyncMock(return_value="created")),
        patch("app.main.ensure_context_text_index", new=AsyncMock(return_value="created")),
        patch("app.main.ensure_context_vector_index", new=AsyncMock(return_value="created")),
        patch("app.main.ensure_memory_text_index", new=AsyncMock(return_value="created")),
        patch("app.main.ensure_memory_vector_index", new=AsyncMock(return_value="created")),
        patch(
            "app.main.ensure_content_text_indexes",
            new=AsyncMock(return_value=CONTENT_INDEX_ACTIONS),
        ) as ensure_content_text_indexes,
        patch(
            "app.main.ensure_research_text_indexes",
            new=AsyncMock(return_value=RESEARCH_INDEX_ACTIONS),
        ),
        patch("app.main.configure_logging"),
    ):
        application = create_app(settings)
        async with application.router.lifespan_context(application):
            assert application.state.embeddings is None

    ensure_vector_indexes.assert_not_awaited()
    ensure_content_text_indexes.assert_awaited_once_with(manager.database)
    manager.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_an_unusable_model_is_logged_instead_of_stopping_startup(
    settings: Settings,
) -> None:
    manager = build_mongodb_manager()
    embeddings = build_embedding_service()
    embeddings.start = AsyncMock(side_effect=EmbeddingUnavailableError("missing weights"))

    with (
        patch("app.main.MongoDBClient", return_value=manager),
        patch("app.main.initialize_database", new=AsyncMock()),
        patch("app.main.build_embedding_service", return_value=embeddings),
        patch("app.main.ensure_vector_indexes", new=AsyncMock()) as ensure_vector_indexes,
        patch("app.main.ensure_text_indexes", new=AsyncMock(return_value="created")),
        patch("app.main.ensure_context_text_index", new=AsyncMock(return_value="created")),
        patch("app.main.ensure_context_vector_index", new=AsyncMock(return_value="created")),
        patch("app.main.ensure_memory_text_index", new=AsyncMock(return_value="created")),
        patch("app.main.ensure_memory_vector_index", new=AsyncMock(return_value="created")),
        patch(
            "app.main.ensure_content_text_indexes",
            new=AsyncMock(return_value=CONTENT_INDEX_ACTIONS),
        ) as ensure_content_text_indexes,
        patch(
            "app.main.ensure_research_text_indexes",
            new=AsyncMock(return_value=RESEARCH_INDEX_ACTIONS),
        ),
        patch("app.main.configure_logging"),
    ):
        application = create_app(settings)
        async with application.router.lifespan_context(application):
            assert application.state.embeddings is embeddings

    ensure_vector_indexes.assert_not_awaited()
    ensure_content_text_indexes.assert_awaited_once_with(manager.database)
    embeddings.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_startup_logs_credential_free_embedding_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    embeddings = build_embedding_service()
    embeddings.start = AsyncMock(side_effect=EmbeddingUnavailableError("missing weights"))

    with (
        caplog.at_level("WARNING", logger="agent_context.startup"),
        patch("app.main.ensure_vector_indexes", new=AsyncMock()) as ensure_vector_indexes,
    ):
        await start_embeddings(embeddings, MagicMock())

    record = caplog.records[-1]
    assert record.message == "embedding_model_unavailable"
    assert record.embedding_model == "stub-embedding-model"
    assert record.embedding_error == "EmbeddingUnavailableError"
    ensure_vector_indexes.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_vector_index_is_reconciled_with_the_loaded_model_dimensions() -> None:
    embeddings = build_embedding_service()
    database = MagicMock()

    with (
        patch(
            "app.main.ensure_vector_indexes",
            new=AsyncMock(return_value="created"),
        ) as ensure_vector_indexes,
        patch(
            "app.main.ensure_context_vector_index",
            new=AsyncMock(return_value="created"),
        ) as ensure_context_vector_index,
        patch(
            "app.main.ensure_memory_vector_index",
            new=AsyncMock(return_value="created"),
        ) as ensure_memory_vector_index,
        patch(
            "app.main.ensure_content_vector_indexes",
            new=AsyncMock(return_value=CONTENT_INDEX_ACTIONS),
        ) as ensure_content_vector_indexes,
        patch(
            "app.main.ensure_research_vector_indexes",
            new=AsyncMock(return_value=RESEARCH_INDEX_ACTIONS),
        ) as ensure_research_vector_indexes,
    ):
        await start_embeddings(embeddings, database)

    # Every vector index is built from the one model that actually loaded.
    ensure_vector_indexes.assert_awaited_once_with(database, dimensions=384)
    ensure_context_vector_index.assert_awaited_once_with(database, dimensions=384)
    ensure_memory_vector_index.assert_awaited_once_with(database, dimensions=384)
    ensure_content_vector_indexes.assert_awaited_once_with(database, dimensions=384)
    ensure_research_vector_indexes.assert_awaited_once_with(database, dimensions=384)


@pytest.mark.asyncio
async def test_an_unavailable_search_index_is_logged_instead_of_stopping_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    embeddings = build_embedding_service()

    with (
        caplog.at_level("WARNING", logger="agent_context.startup"),
        patch(
            "app.main.ensure_vector_indexes",
            new=AsyncMock(side_effect=SearchUnavailableError("mongot is down")),
        ),
    ):
        await start_embeddings(embeddings, MagicMock())

    record = caplog.records[-1]
    assert record.message == "vector_index_unavailable"
    assert record.embedding_dimensions == 384
    assert record.embedding_error == "SearchUnavailableError"
