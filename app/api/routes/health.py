"""Liveness and dependency-backed readiness endpoints."""

import asyncio
import logging
from collections.abc import Awaitable
from time import perf_counter
from typing import Final, Literal, cast

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, computed_field

from app import __version__
from app.db.client import MongoDBClient
from app.db.context_indexes import check_context_text_health, check_context_vector_health
from app.db.health import MongoDBHealth, check_mongodb_health
from app.db.memory_indexes import check_memory_text_health, check_memory_vector_health
from app.db.text_indexes import TextSearchHealth, check_text_search_health
from app.db.vector_indexes import VectorSearchHealth, check_vector_search_health
from app.observability.instruments import (
    record_readiness,
    record_readiness_duration,
    uptime_seconds,
)
from app.services.embedding_service import EmbeddingService

router = APIRouter(prefix="/health", tags=["health"])

logger = logging.getLogger("agent_context.health")


class LivenessResponse(BaseModel):
    """Process liveness response."""

    status: Literal["alive"] = "alive"


class ApiReadiness(BaseModel):
    """The serving process itself.

    Trivially ready — it answered — but worth reporting anyway: a readiness
    body that says which build produced it and how long that build has been up
    turns "it started failing after the deploy" from a guess into a reading.
    """

    ready: Literal[True] = True
    version: str
    uptime_seconds: float


class EmbeddingReadiness(BaseModel):
    """Credential-free local embedding model status."""

    enabled: bool
    available: bool
    model: str | None = None
    dimensions: int | None = None
    device: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ready(self) -> bool:
        """Report readiness, which disabled embeddings never block."""
        return self.available or not self.enabled


class VectorSearchReadiness(BaseModel):
    """Credential-free MongoDB Vector Search index status."""

    enabled: bool
    available: bool
    index: str | None = None
    queryable: bool = False
    index_status: str | None = None
    dimensions: int | None = None
    dimensions_match: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ready(self) -> bool:
        """Report readiness, which disabled embeddings never block."""
        if not self.enabled:
            return True
        return self.available and self.queryable and self.dimensions_match


class LexicalSearchReadiness(BaseModel):
    """Credential-free MongoDB Search index status."""

    available: bool
    index: str | None = None
    queryable: bool = False
    index_status: str | None = None
    analyzer: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ready(self) -> bool:
        """Report whether lexical search can serve a query."""
        return self.available and self.queryable


class CollectionSearchReadiness(BaseModel):
    """Credential-free status of one collection's pair of search indexes."""

    enabled: bool
    vector_available: bool = False
    vector_index: str | None = None
    vector_queryable: bool = False
    vector_dimensions_match: bool = False
    text_available: bool = False
    text_index: str | None = None
    text_queryable: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ready(self) -> bool:
        """Report whether this collection can be retrieved both ways.

        With no embedding model the vector half is not required, exactly as it
        is not required for skills.
        """
        if not self.text_available or not self.text_queryable:
            return False
        if not self.enabled:
            return True
        return self.vector_available and self.vector_queryable and self.vector_dimensions_match


class AuthenticationReadiness(BaseModel):
    """Whether this deployment requires a credential.

    Tenant domain startup requires authentication. This diagnostic reports
    configuration; it never establishes organization authority.
    """

    enabled: bool
    audit: bool


class ReadinessResponse(BaseModel):
    """Dependency readiness response without internal error details."""

    status: Literal["ready", "not_ready"]
    api: ApiReadiness
    authentication: AuthenticationReadiness
    mongodb: bool
    database: bool
    replica_set: bool
    mongot: bool
    embedding: EmbeddingReadiness
    vector_search: VectorSearchReadiness
    lexical_search: LexicalSearchReadiness
    context_search: CollectionSearchReadiness
    memory_search: CollectionSearchReadiness


@router.get("/live", response_model=LivenessResponse)
async def live() -> LivenessResponse:
    """Report HTTP-process liveness without checking dependencies."""
    return LivenessResponse()


async def embedding_readiness(embeddings: EmbeddingService | None) -> EmbeddingReadiness:
    """Describe the configured embedding model for readiness responses."""
    if embeddings is None:
        return EmbeddingReadiness(enabled=False, available=False)
    health = await embeddings.health()
    return EmbeddingReadiness(
        enabled=True,
        available=health.available,
        model=health.model,
        dimensions=health.dimensions,
        device=health.device,
    )


async def vector_search_readiness(
    mongodb: MongoDBClient,
    embeddings: EmbeddingService | None,
) -> VectorSearchReadiness:
    """Describe the skill chunk vector index for readiness responses."""
    if embeddings is None:
        return VectorSearchReadiness(enabled=False, available=False)
    health = await check_vector_search_health(mongodb.database, embeddings.dimensions)
    index = health.index
    return VectorSearchReadiness(
        enabled=True,
        available=health.available,
        index=index.name if index is not None else None,
        queryable=index.queryable if index is not None else False,
        index_status=index.status if index is not None else None,
        dimensions=index.dimensions if index is not None else None,
        dimensions_match=health.dimensions_match,
    )


async def lexical_search_readiness(mongodb: MongoDBClient) -> LexicalSearchReadiness:
    """Describe the skill chunk Search index for readiness responses."""
    health = await check_text_search_health(mongodb.database)
    index = health.index
    return LexicalSearchReadiness(
        available=health.available,
        index=index.name if index is not None else None,
        queryable=index.queryable if index is not None else False,
        index_status=index.status if index is not None else None,
        analyzer=index.analyzer if index is not None else None,
    )


async def collection_search_readiness(
    text: TextSearchHealth,
    vector: VectorSearchHealth | None,
) -> CollectionSearchReadiness:
    """Describe one collection's index pair for readiness responses."""
    text_index = text.index
    if vector is None:
        return CollectionSearchReadiness(
            enabled=False,
            text_available=text.available,
            text_index=text_index.name if text_index is not None else None,
            text_queryable=text_index.queryable if text_index is not None else False,
        )
    vector_index = vector.index
    return CollectionSearchReadiness(
        enabled=True,
        vector_available=vector.available,
        vector_index=vector_index.name if vector_index is not None else None,
        vector_queryable=vector_index.queryable if vector_index is not None else False,
        vector_dimensions_match=vector.dimensions_match,
        text_available=text.available,
        text_index=text_index.name if text_index is not None else None,
        text_queryable=text_index.queryable if text_index is not None else False,
    )


async def context_search_readiness(
    mongodb: MongoDBClient,
    embeddings: EmbeddingService | None,
) -> CollectionSearchReadiness:
    """Describe both project-context search indexes for readiness responses."""
    return await collection_search_readiness(
        await check_context_text_health(mongodb.database),
        None
        if embeddings is None
        else await check_context_vector_health(mongodb.database, embeddings.dimensions),
    )


async def memory_search_readiness(
    mongodb: MongoDBClient,
    embeddings: EmbeddingService | None,
) -> CollectionSearchReadiness:
    """Describe both memory search indexes for readiness responses."""
    return await collection_search_readiness(
        await check_memory_text_health(mongodb.database),
        None
        if embeddings is None
        else await check_memory_vector_health(mongodb.database, embeddings.dimensions),
    )


Probes = tuple[
    MongoDBHealth,
    EmbeddingReadiness,
    VectorSearchReadiness,
    LexicalSearchReadiness,
    CollectionSearchReadiness,
    CollectionSearchReadiness,
]
# What one block reports when it could not answer inside its budget. Not ready,
# because not knowing is not the same as being fine.
UNKNOWN: Final[Probes] = (
    MongoDBHealth(database_available=False, replica_set_ready=False, mongot_available=False),
    EmbeddingReadiness(enabled=True, available=False),
    VectorSearchReadiness(enabled=True, available=False),
    LexicalSearchReadiness(available=False),
    CollectionSearchReadiness(enabled=True),
    CollectionSearchReadiness(enabled=True),
)


async def bounded[T](probing: Awaitable[T], budget: float, unknown: T, name: str) -> T:
    """Give one probe a deadline of its own, and an answer if it misses it.

    Per probe rather than around all of them together. A single deadline for
    the whole check throws away the answers that did arrive: with the search
    coordinator down the index probes time out while the database and the
    replica set answer immediately, and reporting those as unknown too turns a
    precise diagnosis into "something is wrong". Bounding each one keeps every
    answer that was available and marks only the ones that were not.
    """
    try:
        return await asyncio.wait_for(probing, timeout=budget)
    # A probe exists to produce a verdict, so anything it raises is one.
    except (TimeoutError, Exception) as error:
        logger.warning(
            "readiness_probe_failed",
            extra={
                "operation": "ready",
                "component": name,
                "outcome": "timeout" if isinstance(error, TimeoutError) else "failed",
                "error_type": type(error).__name__,
            },
        )
        return unknown


async def probe(
    mongodb: MongoDBClient,
    embeddings: EmbeddingService | None,
    replica_set: str,
    budget: float,
) -> Probes:
    """Ask every dependency at once, each with its own deadline.

    Concurrently rather than one after another, because none of these answers
    depends on another and the slow case is what matters: with the search
    coordinator down, six probes in sequence each waited out the database
    operation timeout and readiness took exactly three minutes. Asked together,
    each bounded, the whole check costs one budget.
    """
    asked = await asyncio.gather(
        bounded(
            check_mongodb_health(mongodb.client, mongodb.database, replica_set),
            budget,
            UNKNOWN[0],
            "mongodb",
        ),
        bounded(embedding_readiness(embeddings), budget, UNKNOWN[1], "embedding"),
        bounded(vector_search_readiness(mongodb, embeddings), budget, UNKNOWN[2], "vector_search"),
        bounded(lexical_search_readiness(mongodb), budget, UNKNOWN[3], "lexical_search"),
        bounded(
            context_search_readiness(mongodb, embeddings), budget, UNKNOWN[4], "context_search"
        ),
        bounded(memory_search_readiness(mongodb, embeddings), budget, UNKNOWN[5], "memory_search"),
    )
    return cast(Probes, tuple(asked))


@router.get("/ready", response_model=ReadinessResponse)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    """Report readiness when MongoDB, mongot, embeddings, and vectors are usable."""
    started = perf_counter()
    mongodb: MongoDBClient = request.app.state.mongodb
    embeddings: EmbeddingService | None = request.app.state.embeddings
    settings = request.app.state.settings
    health, embedding, vector_search, lexical_search, context_search, memory_search = await probe(
        mongodb,
        embeddings,
        settings.mongodb_replica_set,
        settings.api_readiness_timeout_seconds,
    )
    authentication = AuthenticationReadiness(
        enabled=request.app.state.auth is not None,
        audit=settings.audit_log_enabled,
    )
    is_ready = (
        health.ready
        and embedding.ready
        and vector_search.ready
        and lexical_search.ready
        and context_search.ready
        and memory_search.ready
    )
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    _publish(
        health=health,
        embedding=embedding,
        vector_search=vector_search,
        lexical_search=lexical_search,
        context_search=context_search,
        memory_search=memory_search,
        is_ready=is_ready,
        seconds=perf_counter() - started,
    )
    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        api=ApiReadiness(version=__version__, uptime_seconds=round(uptime_seconds(), 3)),
        authentication=authentication,
        mongodb=health.mongodb_ready,
        database=health.database_available,
        replica_set=health.replica_set_ready,
        mongot=health.mongot_available,
        embedding=embedding,
        vector_search=vector_search,
        lexical_search=lexical_search,
        context_search=context_search,
        memory_search=memory_search,
    )


def _publish(
    *,
    health: MongoDBHealth,
    embedding: EmbeddingReadiness,
    vector_search: VectorSearchReadiness,
    lexical_search: LexicalSearchReadiness,
    context_search: CollectionSearchReadiness,
    memory_search: CollectionSearchReadiness,
    is_ready: bool,
    seconds: float,
) -> None:
    """Publish this check's verdict as gauges a scraper can alert on.

    Written here rather than read at `/metrics`, because a scrape must stay
    cheap: probing every dependency on each scrape would put the monitoring on
    the critical path of the thing it monitors. The container health check
    already calls this endpoint on a schedule, so the gauges stay as fresh as
    readiness itself is.
    """
    record_readiness(dependency="api", ready=True)
    record_readiness(dependency="mongodb", ready=health.database_available)
    record_readiness(dependency="replica_set", ready=health.replica_set_ready)
    record_readiness(dependency="mongot", ready=health.mongot_available)
    record_readiness(dependency="embedding", ready=embedding.ready)
    record_readiness(dependency="vector_search", ready=vector_search.ready)
    record_readiness(dependency="lexical_search", ready=lexical_search.ready)
    record_readiness(dependency="context_search", ready=context_search.ready)
    record_readiness(dependency="memory_search", ready=memory_search.ready)
    record_readiness(dependency="platform", ready=is_ready)
    record_readiness_duration(seconds)
