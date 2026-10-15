"""What the platform does when something it depends on is not working.

Every dependency here fails differently, and the useful question is never
"does it break" but "what does it do instead". This file states the answers in
one place, because they are the part of the design an operator has to be able
to predict during an incident, and because each of them is decided in a
different module and would otherwise only be visible by reading all of them.

The contract in one sentence: the platform degrades to what still works, says
so through readiness, and answers a caller with the status that says whose
problem it is.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pymongo.errors import ExecutionTimeout, PyMongoError, ServerSelectionTimeoutError

from app.api.middleware import SecurityHeadersMiddleware, unavailable_dependency, unhandled_error
from app.core.config import Settings
from app.db.client import MongoDBClient, MongoDBNotReadyError
from app.db.errors import SearchUnavailableError
from app.db.health import MongoDBHealth
from app.embeddings.errors import EmbeddingUnavailableError
from app.skills.errors import SkillParseError


def build_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Return valid settings with the database configuration filled in."""
    password_file = tmp_path / "mongodb-password"
    password_file.write_text("test-only-password", encoding="utf-8")
    return Settings(
        _env_file=None,
        mongodb_uri="mongodb://mongodb:27017",
        mongodb_database="agent_context_test",
        mongodb_username="application_test_user",
        mongodb_password_file=password_file,
        **overrides,  # type: ignore[arg-type]
    )


# --- MongoDB unavailable ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_database_that_fails_its_checks_stops_startup(tmp_path: Path) -> None:
    # Rather than starting and serving errors: a process that came up without a
    # database would be reported healthy by anything watching the port.
    failing = MongoDBHealth(
        database_available=False,
        replica_set_ready=False,
        mongot_available=False,
    )
    client = MagicMock()
    client.close = AsyncMock()
    with patch("app.db.client.check_mongodb_health", new=AsyncMock(return_value=failing)):
        with pytest.raises(MongoDBNotReadyError):
            await MongoDBClient(build_settings(tmp_path), lambda _: client).connect()

    # And the connection it opened to find out is released.
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_database_outage_during_a_request_is_answered_as_an_outage() -> None:
    # 503, not 500. The difference is what a client retries on, what a proxy
    # takes a backend out of rotation for, and what an operator reads as
    # "MongoDB is down" rather than "there is a bug".
    application = FastAPI()
    application.add_exception_handler(Exception, unhandled_error)
    application.add_exception_handler(PyMongoError, unavailable_dependency)
    application.add_middleware(SecurityHeadersMiddleware)

    @application.get("/reads")
    async def reads() -> dict[str, bool]:
        raise ServerSelectionTimeoutError("mongodb://mongodb:27017: connection refused")

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/reads")

    assert response.status_code == 503
    assert response.json() == {"detail": "the platform is temporarily unavailable"}


@pytest.mark.asyncio
async def test_an_outage_answer_names_no_host_and_no_connection_string() -> None:
    application = FastAPI()
    application.add_exception_handler(Exception, unhandled_error)
    application.add_exception_handler(PyMongoError, unavailable_dependency)

    @application.get("/reads")
    async def reads() -> dict[str, bool]:
        raise ServerSelectionTimeoutError("mongodb://user:pass@db-01.internal:27017 unreachable")

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/reads")).text

    assert "mongodb://" not in body
    assert "db-01.internal" not in body
    assert "pass" not in body


@pytest.mark.asyncio
async def test_an_outage_answer_still_carries_the_security_headers() -> None:
    application = FastAPI()
    application.add_exception_handler(Exception, unhandled_error)
    application.add_exception_handler(PyMongoError, unavailable_dependency)
    application.add_middleware(SecurityHeadersMiddleware)

    @application.get("/reads")
    async def reads() -> dict[str, bool]:
        raise PyMongoError("anything")

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/reads")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


def test_every_database_operation_has_an_upper_bound(tmp_path: Path) -> None:
    # Measured before this existed: a search issued while the search
    # coordinator was down took forty seconds, because connecting had already
    # succeeded and nothing bounded what came after.
    settings = build_settings(tmp_path)

    assert settings.mongodb_timeout_ms == 15_000
    assert settings.mongodb_connect_timeout_ms < settings.mongodb_timeout_ms
    assert settings.mongodb_server_selection_timeout_ms < settings.mongodb_timeout_ms


@pytest.mark.asyncio
async def test_an_operation_that_exceeds_its_timeout_is_an_outage_not_a_defect() -> None:
    # The timeout's own error is a PyMongoError, so it lands on the same
    # handler and is answered 503 like any other database failure.
    application = FastAPI()
    application.add_exception_handler(Exception, unhandled_error)
    application.add_exception_handler(PyMongoError, unavailable_dependency)

    @application.get("/slow")
    async def slow() -> dict[str, bool]:
        raise ExecutionTimeout("operation exceeded time limit")

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/slow")).status_code == 503


# --- mongot unavailable -----------------------------------------------------


def test_a_failing_coordinator_fails_readiness_without_stopping_the_process() -> None:
    # Lexical and vector retrieval stop working; everything that does not need
    # an index keeps working, and readiness is what says which is which.
    degraded = MongoDBHealth(
        database_available=True,
        replica_set_ready=True,
        mongot_available=False,
    )

    assert degraded.mongodb_ready is True
    assert degraded.ready is False


@pytest.mark.asyncio
async def test_a_search_that_cannot_be_served_is_reported_as_unavailable() -> None:
    from app.services.retrieval_service import RetrievalService
    from tests.unit.test_hybrid_search_service import FakeChunkRepository, FakeSkillRepository
    from tests.unit.test_memory_retrieval import FakeMemoryFactory, FakeMemorySearchRepository

    broken = FakeMemorySearchRepository(
        None, error=SearchUnavailableError("index is not queryable")
    )
    service = RetrievalService(
        FakeSkillRepository([]),
        FakeChunkRepository(),
        memories=FakeMemoryFactory({None: broken}),
    )

    with pytest.raises(SearchUnavailableError):
        await service.memory_search("anything")


# --- the embedding model unavailable ----------------------------------------


def test_without_a_model_the_platform_still_retrieves_lexically() -> None:
    # The degradation that matters most: a model that will not load takes
    # semantic search down and leaves the rest of retrieval answering.
    from app.services.retrieval_service import RetrievalService
    from tests.unit.test_hybrid_search_service import FakeChunkRepository, FakeSkillRepository

    without = RetrievalService(FakeSkillRepository([]), FakeChunkRepository(), None)

    assert without.methods == ("lexical",)


@pytest.mark.asyncio
async def test_vector_search_without_a_model_is_refused_rather_than_faked() -> None:
    from app.services.retrieval_service import RetrievalService
    from tests.unit.test_hybrid_search_service import FakeChunkRepository, FakeSkillRepository

    without = RetrievalService(FakeSkillRepository([]), FakeChunkRepository(), None)

    with pytest.raises(EmbeddingUnavailableError):
        await without.vector_search("anything")


def test_the_ranking_redistributes_a_weight_it_cannot_measure() -> None:
    # Rather than scoring every result against a signal that does not exist,
    # which would score them all identically and call it a ranking.
    from app.retrieval.ranking import DEFAULT_WEIGHTS

    lexical_only = DEFAULT_WEIGHTS.for_methods(("lexical",))

    assert lexical_only.vector == 0.0
    assert lexical_only.lexical > DEFAULT_WEIGHTS.lexical


@pytest.mark.asyncio
async def test_a_model_that_will_not_load_does_not_stop_startup() -> None:
    from app.main import start_embeddings

    embeddings = MagicMock()
    embeddings.model = "sentence-transformers/all-MiniLM-L6-v2"
    embeddings.start = AsyncMock(side_effect=EmbeddingUnavailableError("no model"))

    # Returns rather than raising: readiness reports it, and lexical retrieval
    # keeps answering in the meantime.
    await start_embeddings(embeddings, MagicMock())

    embeddings.start.assert_awaited_once()


# --- malformed and duplicate input ------------------------------------------


def test_a_malformed_skill_stops_the_import_before_anything_is_written(
    tmp_path: Path,
) -> None:
    # Every file is parsed and chunked before any of them is persisted, so a
    # directory with one bad file leaves the knowledge base untouched rather
    # than half-imported.
    from tests.unit.test_skill_importer import make_importer, markdown

    root = tmp_path / "skills"
    root.mkdir()
    (root / "good.md").write_text(markdown("good"), encoding="utf-8")
    (root / "bad.md").write_text("no frontmatter here", encoding="utf-8")
    service = MagicMock()
    service.import_skill = AsyncMock()

    with pytest.raises(SkillParseError):
        make_importer(service).prepare_directory(root)

    service.import_skill.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_repeated_import_of_unchanged_content_changes_nothing(
    tmp_path: Path,
) -> None:
    # Importing the same directory twice is the normal case — a deploy runs it
    # every time — so it has to be a no-op rather than a new version each time.
    from app.skills.types import SkillImportOutcome
    from tests.unit.test_skill_importer import make_importer, markdown

    root = tmp_path / "skills"
    root.mkdir()
    (root / "one.md").write_text(markdown("one"), encoding="utf-8")
    service = MagicMock()
    service.import_skill = AsyncMock(
        return_value=SkillImportOutcome("unchanged", "one", "1.0.0", 0)
    )

    report = await make_importer(service).import_directory(root)

    assert report.discovered == 1
    assert report.unchanged == 1
    assert report.new == 0
    assert report.updated == 0
    assert report.chunks_created == 0


def test_corrupted_stored_data_is_refused_rather_than_returned() -> None:
    # A document that does not validate is a document the platform will not
    # serve: returning it would put whatever is in the database into an answer.
    from app.models.memory import MemoryDocument

    with pytest.raises(ValueError):
        MemoryDocument.model_validate({"type": "convention", "content": "x", "importance": 47.0})


def test_a_stored_document_from_another_project_cannot_be_validated_into_one() -> None:
    from app.models.project_context import ProjectContextDocument

    with pytest.raises(ValueError):
        ProjectContextDocument.model_validate(
            {
                "project_id": "",
                "type": "business_rule",
                "title": "t",
                "content": "c",
                "source": "s",
            }
        )


# --- restart recovery -------------------------------------------------------


def test_nothing_the_platform_needs_lives_only_in_memory() -> None:
    # What a restart has to survive is the whole question, and the answer is
    # that the durable state is MongoDB's volume: the search indexes are
    # rebuilt from it and the model cache is re-downloadable.
    import yaml

    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    volumes = compose["volumes"]

    assert volumes["mongodb_data"]["name"] == "agent_context_mongodb_data"
    assert volumes["mongot_data"]["name"] == "agent_context_mongot_data"
    assert volumes["embedding_models"]["name"] == "agent_context_embedding_models"


def test_startup_reconciles_indexes_rather_than_assuming_them() -> None:
    # Which is what makes a restart onto a fresh search coordinator recover on
    # its own instead of needing an operator.
    from app.main import create_app

    source = Path("app/main.py").read_text(encoding="utf-8")

    assert "ensure_text_indexes" in source
    assert "ensure_vector_indexes" in source
    assert "initialize_database" in source
    assert create_app is not None


@pytest.mark.asyncio
async def test_a_restart_releases_the_model_and_the_database(tmp_path: Path) -> None:
    from app.main import create_app

    mongodb = MagicMock()
    mongodb.connect = AsyncMock()
    mongodb.close = AsyncMock()
    mongodb.database = MagicMock()
    embeddings = MagicMock()
    embeddings.close = AsyncMock()
    embeddings.start = AsyncMock(side_effect=EmbeddingUnavailableError("no model"))
    embeddings.model = "m"

    with (
        patch("app.main.MongoDBClient", return_value=mongodb),
        patch("app.main.initialize_database", new=AsyncMock()),
        patch("app.main.start_lexical_search", new=AsyncMock()),
        patch("app.main.build_embedding_service", return_value=embeddings),
        patch("app.main.start_authentication", return_value=None),
    ):
        application = create_app(build_settings(tmp_path))
        async with application.router.lifespan_context(application):
            pass

    embeddings.close.assert_awaited_once()
    mongodb.close.assert_awaited_once()


# --- network failures -------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unreachable_platform_is_reported_to_an_agent_as_such() -> None:
    import httpx

    from app.mcp.errors import UnavailableError
    from tests.mcp_doubles import RecordedApi, api_client

    client = api_client(RecordedApi(error=httpx.ConnectError("connection refused")))
    try:
        with pytest.raises(UnavailableError) as failure:
            await client.request("GET", "/api/v1/projects")
    finally:
        await client.close()

    # And the transport's own message, which names a host and a port, is not
    # what the agent is told.
    assert "connection refused" not in str(failure.value)


def test_the_agent_facing_client_bounds_every_request(tmp_path: Path) -> None:
    from app.core.config import McpSettings

    settings = McpSettings(_env_file=None)

    assert 0.0 < settings.mcp_request_timeout_seconds <= 300.0


def test_the_agent_facing_client_ignores_an_inherited_proxy() -> None:
    # A shell-level proxy would route private retrieved knowledge off the host.
    source = Path("app/mcp/client.py").read_text(encoding="utf-8")

    assert "trust_env=False" in source


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing domain behavior uses an explicit test organization."""
    yield
