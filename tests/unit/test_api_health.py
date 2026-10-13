"""HTTP health response tests."""

import asyncio
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Response, status
from starlette.requests import Request

from app import __version__
from app.api.routes.health import live, ready
from app.db.health import MongoDBHealth
from app.db.text_indexes import TextIndexStatus, TextSearchHealth
from app.db.vector_indexes import VectorIndexStatus, VectorSearchHealth
from app.embeddings.types import EmbeddingHealth
from app.observability import instruments
from app.observability.instruments import REGISTRY

READY_MONGODB = MongoDBHealth(
    database_available=True,
    replica_set_ready=True,
    mongot_available=True,
)
READY_EMBEDDING = EmbeddingHealth(
    available=True,
    model="sentence-transformers/all-MiniLM-L6-v2",
    dimensions=384,
    device="cpu",
)
READY_VECTOR_SEARCH = VectorSearchHealth(
    available=True,
    index=VectorIndexStatus(
        name="skill_chunk_vector_index",
        exists=True,
        queryable=True,
        status="READY",
        dimensions=384,
        similarity="cosine",
    ),
    expected_dimensions=384,
)
READY_LEXICAL_SEARCH = TextSearchHealth(
    available=True,
    index=TextIndexStatus(
        name="skill_chunk_text_index",
        exists=True,
        queryable=True,
        status="READY",
        analyzer="lucene.standard",
    ),
)
READY_CONTEXT_VECTOR = VectorSearchHealth(
    available=True,
    index=VectorIndexStatus(
        name="project_context_vector_index",
        exists=True,
        queryable=True,
        status="READY",
        dimensions=384,
        similarity="cosine",
    ),
    expected_dimensions=384,
)
READY_CONTEXT_TEXT = TextSearchHealth(
    available=True,
    index=TextIndexStatus(
        name="project_context_text_index",
        exists=True,
        queryable=True,
        status="READY",
        analyzer="lucene.standard",
    ),
)
READY_MEMORY_VECTOR = VectorSearchHealth(
    available=True,
    index=VectorIndexStatus(
        name="memory_vector_index",
        exists=True,
        queryable=True,
        status="READY",
        dimensions=384,
        similarity="cosine",
    ),
    expected_dimensions=384,
)
READY_MEMORY_TEXT = TextSearchHealth(
    available=True,
    index=TextIndexStatus(
        name="memory_text_index",
        exists=True,
        queryable=True,
        status="READY",
        analyzer="lucene.standard",
    ),
)


def build_embeddings(health: EmbeddingHealth = READY_EMBEDDING) -> MagicMock:
    """Build an embedding service double reporting the given health."""
    embeddings = MagicMock()
    embeddings.health = AsyncMock(return_value=health)
    embeddings.dimensions = 384
    embeddings.model = health.model
    return embeddings


def build_request(embeddings: object, *, authenticated: bool = True) -> Request:
    """Build a request whose application state carries the given services."""
    application = FastAPI()
    application.state.mongodb = MagicMock()
    application.state.settings = MagicMock(
        mongodb_replica_set="rs0",
        audit_log_enabled=True,
        api_readiness_timeout_seconds=10.0,
    )
    application.state.embeddings = embeddings
    application.state.auth = MagicMock() if authenticated else None
    return Request({"type": "http", "app": application})


@contextmanager
def patched_health(
    mongodb: MongoDBHealth,
    vector_search: VectorSearchHealth = READY_VECTOR_SEARCH,
    lexical_search: TextSearchHealth = READY_LEXICAL_SEARCH,
    context_vector: VectorSearchHealth = READY_CONTEXT_VECTOR,
    context_text: TextSearchHealth = READY_CONTEXT_TEXT,
    memory_vector: VectorSearchHealth = READY_MEMORY_VECTOR,
    memory_text: TextSearchHealth = READY_MEMORY_TEXT,
    slow: float = 0.0,
    slow_probes: dict[str, float] | None = None,
) -> Iterator[None]:
    """Patch every dependency probe the readiness endpoint performs.

    `slow` makes every probe take that long, which is how a test tells whether
    they are asked together or one after another. `slow_probes` slows named
    ones only, which is how it tells whether one slow answer costs the others.
    """
    probes = {
        "check_mongodb_health": mongodb,
        "check_vector_search_health": vector_search,
        "check_text_search_health": lexical_search,
        "check_context_vector_health": context_vector,
        "check_context_text_health": context_text,
        "check_memory_vector_health": memory_vector,
        "check_memory_text_health": memory_text,
    }
    delays = slow_probes or {}
    with ExitStack() as stack:
        for name, result in probes.items():
            stack.enter_context(
                patch(f"app.api.routes.health.{name}", new=_probe(result, delays.get(name, slow)))
            )
        yield


def _probe(result: object, delay: float) -> AsyncMock:
    """Return a probe that answers with `result`, after `delay` seconds."""

    async def answer(*args: object, **kwargs: object) -> object:
        if delay:
            await asyncio.sleep(delay)
        return result

    return AsyncMock(side_effect=answer)


@pytest.mark.asyncio
async def test_liveness_does_not_depend_on_mongodb() -> None:
    assert (await live()).status == "alive"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("health", "expected_status"),
    [
        (READY_MONGODB, status.HTTP_200_OK),
        (
            MongoDBHealth(
                database_available=True,
                replica_set_ready=True,
                mongot_available=False,
            ),
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ),
    ],
)
async def test_readiness_reflects_all_mongodb_checks(
    health: MongoDBHealth, expected_status: int
) -> None:
    request = build_request(build_embeddings())
    response = Response()
    with patched_health(health):
        payload = await ready(request, response)

    assert response.status_code == expected_status
    assert payload.database is health.database_available
    assert payload.replica_set is health.replica_set_ready
    assert payload.mongot is health.mongot_available
    assert payload.mongodb is health.mongodb_ready


@pytest.mark.asyncio
async def test_readiness_reports_the_loaded_embedding_model() -> None:
    request = build_request(build_embeddings())
    response = Response()
    with patched_health(READY_MONGODB):
        payload = await ready(request, response)

    assert payload.status == "ready"
    assert response.status_code == status.HTTP_200_OK
    assert payload.embedding.enabled is True
    assert payload.embedding.available is True
    assert payload.embedding.model == "sentence-transformers/all-MiniLM-L6-v2"
    assert payload.embedding.dimensions == 384
    assert payload.embedding.device == "cpu"


@pytest.mark.asyncio
async def test_an_unavailable_embedding_model_fails_readiness() -> None:
    request = build_request(
        build_embeddings(
            EmbeddingHealth(
                available=False,
                model="sentence-transformers/all-MiniLM-L6-v2",
                detail="EmbeddingUnavailableError",
            )
        )
    )
    response = Response()
    with patched_health(READY_MONGODB):
        payload = await ready(request, response)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert payload.status == "not_ready"
    assert payload.mongodb is True
    assert payload.embedding.available is False
    assert payload.embedding.dimensions is None
    assert "EmbeddingUnavailableError" not in payload.model_dump_json()


@pytest.mark.asyncio
async def test_disabled_embeddings_do_not_block_readiness() -> None:
    request = build_request(None)
    response = Response()
    with patched_health(READY_MONGODB):
        payload = await ready(request, response)

    assert response.status_code == status.HTTP_200_OK
    assert payload.status == "ready"
    assert payload.embedding.enabled is False
    assert payload.embedding.available is False
    assert payload.embedding.model is None
    assert payload.vector_search.enabled is False
    assert payload.vector_search.ready is True
    assert payload.lexical_search.available is True
    assert payload.lexical_search.ready is True


@pytest.mark.asyncio
async def test_every_readiness_block_reports_whether_it_is_ready() -> None:
    request = build_request(build_embeddings())
    response = Response()
    with patched_health(READY_MONGODB):
        payload = await ready(request, response)

    serialized = payload.model_dump()

    # Each block computes its own readiness from rules only the platform knows
    # — an embedding model that may be disabled, a vector index that may be
    # built for other dimensions. Serializing the answer is what lets a client
    # read it instead of re-deriving it and drifting.
    for name in ("embedding", "vector_search", "lexical_search", "context_search", "memory_search"):
        assert serialized[name]["ready"] is True, name


@pytest.mark.asyncio
async def test_readiness_says_which_build_answered_and_for_how_long() -> None:
    # A readiness body that names the build turns "it started failing after the
    # deploy" from a guess into a reading.
    response = Response()
    with patched_health(READY_MONGODB):
        payload = await ready(build_request(build_embeddings()), response)

    assert payload.api.ready is True
    assert payload.api.version == __version__
    assert payload.api.uptime_seconds >= 0.0


@pytest.mark.asyncio
async def test_a_readiness_check_publishes_what_it_found_as_metrics() -> None:
    # So an alert can fire on a failing dependency without anything having to
    # parse the readiness body.
    REGISTRY.reset()
    failing = MongoDBHealth(
        database_available=True,
        replica_set_ready=True,
        mongot_available=False,
    )
    try:
        with patched_health(failing):
            await ready(build_request(build_embeddings()), Response())

        assert REGISTRY.value(instruments.DEPENDENCY_READY, dependency="mongodb") == 1.0
        assert REGISTRY.value(instruments.DEPENDENCY_READY, dependency="replica_set") == 1.0
        assert REGISTRY.value(instruments.DEPENDENCY_READY, dependency="mongot") == 0.0
        assert REGISTRY.value(instruments.DEPENDENCY_READY, dependency="platform") == 0.0
        assert REGISTRY.summary(instruments.READINESS_DURATION) is not None
    finally:
        REGISTRY.reset()


@pytest.mark.asyncio
async def test_every_dependency_the_phase_requires_is_reported() -> None:
    REGISTRY.reset()
    try:
        with patched_health(READY_MONGODB):
            await ready(build_request(build_embeddings()), Response())

        for dependency in ("api", "mongodb", "replica_set", "mongot", "embedding"):
            assert REGISTRY.value(instruments.DEPENDENCY_READY, dependency=dependency) == 1.0, (
                dependency
            )
    finally:
        REGISTRY.reset()


@pytest.mark.asyncio
async def test_readiness_says_whether_the_platform_requires_a_credential() -> None:
    response = Response()
    with patched_health(READY_MONGODB):
        secured = await ready(build_request(build_embeddings()), response)
        open_platform = await ready(
            build_request(build_embeddings(), authenticated=False),
            Response(),
        )

    # An open deployment is visible rather than assumed: this is how an
    # operator finds out that every endpoint answers without a key.
    assert secured.authentication.enabled is True
    assert open_platform.authentication.enabled is False
    assert secured.authentication.audit is True


@pytest.mark.asyncio
async def test_a_probe_that_runs_out_of_time_reports_not_ready() -> None:
    # Measured before this existed: with the search coordinator down, readiness
    # took exactly 180 seconds — six index probes in sequence, each waiting out
    # the database operation timeout. A health endpoint that hangs teaches
    # nothing to anything watching it.
    request = build_request(build_embeddings())
    request.app.state.settings.api_readiness_timeout_seconds = 0.05
    response = Response()

    with patched_health(READY_MONGODB, slow=5.0):
        payload = await ready(request, response)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert payload.status == "not_ready"
    # Not knowing is not the same as being fine.
    assert payload.mongot is False
    assert payload.vector_search.ready is False


@pytest.mark.asyncio
async def test_one_slow_probe_does_not_discard_what_the_others_answered() -> None:
    # The diagnosis is the point. With the search coordinator down, the
    # database and the replica set answer immediately, and reporting those as
    # unknown too would turn "mongot is down" into "something is wrong".
    request = build_request(build_embeddings())
    request.app.state.settings.api_readiness_timeout_seconds = 0.1

    with patched_health(READY_MONGODB, slow_probes={"check_vector_search_health": 5.0}):
        payload = await ready(request, Response())

    assert payload.status == "not_ready"
    assert payload.vector_search.ready is False
    # And everything that did answer is reported as it answered.
    assert payload.mongodb is True
    assert payload.replica_set is True
    assert payload.lexical_search.ready is True
    assert payload.memory_search.ready is True


@pytest.mark.asyncio
async def test_a_timed_out_readiness_check_still_says_which_build_answered() -> None:
    request = build_request(build_embeddings())
    request.app.state.settings.api_readiness_timeout_seconds = 0.05

    with patched_health(READY_MONGODB, slow=5.0):
        payload = await ready(request, Response())

    assert payload.api.version == __version__


@pytest.mark.asyncio
async def test_the_dependencies_are_asked_all_at_once() -> None:
    # None of these answers depends on another, and the slow case is the one
    # that matters: asked together they cost the slowest one rather than
    # the sum of all of them.
    started = asyncio.get_running_loop().time()
    request = build_request(build_embeddings())
    response = Response()

    with patched_health(READY_MONGODB, slow=0.05):
        payload = await ready(request, response)

    elapsed = asyncio.get_running_loop().time() - started
    assert payload.status == "ready"
    # Six probes of 50ms each: under 0.2s means concurrent, not sequential.
    assert elapsed < 0.2, elapsed
