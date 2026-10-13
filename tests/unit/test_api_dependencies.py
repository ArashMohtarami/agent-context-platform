"""What the routers are actually handed, over a recording database double.

A service assembled with one source missing answers every test that injects it
directly and still returns nothing in production, so the wiring is asserted
here rather than assumed.
"""

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request

from app.api.dependencies import (
    get_retrieval_logger,
    get_retrieval_service,
    get_search_service,
)
from app.core.config import Settings
from app.db.client_types import MongoDocument
from app.embeddings.types import EmbeddingVector
from app.retrieval.types import RETRIEVABLE_SOURCES
from app.search.logging import CompositeRetrievalLogger, StructuredRetrievalLogger
from app.security.principal import unauthenticated_principal

PROJECT = "sampleco-payment"
DIMENSIONS = 4


# One active skill version, so the chunk search has a scope to run against.
SKILL_ROWS: tuple[MongoDocument, ...] = ({"skill_id": "fastapi-production", "version": "1.0.0"},)


class FakeCursor:
    """A cursor over a fixed result set."""

    def __init__(self, rows: Sequence[MongoDocument] = ()) -> None:
        self.rows = list(rows)

    def sort(self, *args: object, **kwargs: object) -> "FakeCursor":
        return self

    def limit(self, *args: object, **kwargs: object) -> "FakeCursor":
        return self

    async def to_list(self, length: int | None = None) -> list[MongoDocument]:
        return self.rows


class FakeCollection:
    """Record every query one collection is asked to run."""

    def __init__(
        self,
        name: str,
        calls: list[tuple[str, str]],
        rows: Sequence[MongoDocument] = (),
    ) -> None:
        self.name = name
        self.calls = calls
        self.rows = rows

    def find(self, *args: object, **kwargs: object) -> FakeCursor:
        self.calls.append((self.name, "find"))
        return FakeCursor(self.rows)

    async def aggregate(self, pipeline: Sequence[MongoDocument]) -> FakeCursor:
        stage = next(iter(pipeline[0])) if pipeline else "$empty"
        self.calls.append((self.name, stage))
        return FakeCursor()

    async def insert_one(self, document: MongoDocument) -> object:
        self.calls.append((self.name, "insert_one"))
        return SimpleNamespace(inserted_id=document.get("_id"))


class FakeDatabase:
    """Hand out recording collections by name."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __getitem__(self, name: str) -> FakeCollection:
        return FakeCollection(name, self.calls, SKILL_ROWS if name == "skills" else ())


class FakeEmbeddings:
    """Embed a query without loading a model."""

    model = "stub-embedding-model"
    dimensions = DIMENSIONS

    async def embed_query(self, text: str) -> EmbeddingVector:
        return EmbeddingVector(
            model=self.model,
            dimensions=DIMENSIONS,
            values=(0.1,) * DIMENSIONS,
        )


def build_request(settings: Settings, database: FakeDatabase) -> Request:
    """Build the minimal request state every dependency reads."""
    state = SimpleNamespace(
        settings=settings,
        mongodb=SimpleNamespace(database=database),
        embeddings=FakeEmbeddings(),
    )
    return cast(Request, SimpleNamespace(app=SimpleNamespace(state=state)))


def logging_settings(tmp_path: Path, *, enabled: bool) -> Settings:
    """Build settings that differ only in whether searches are stored."""
    password_file = tmp_path / "mongodb-password"
    password_file.write_text("test-only-password", encoding="utf-8")
    return Settings(
        _env_file=None,
        mongodb_uri="mongodb://mongodb:27017",
        mongodb_database="agent_context_test",
        mongodb_username="application_test_user",
        mongodb_password_file=password_file,
        retrieval_log_enabled=enabled,
    )


@pytest.mark.asyncio
async def test_the_unified_search_reaches_every_retrievable_source(settings: Settings) -> None:
    database = FakeDatabase()
    service = await get_search_service(
        build_request(settings, database), unauthenticated_principal()
    )

    result = await service.search_knowledge("payment idempotency", project_id=PROJECT)

    # A source missing from the wiring would be reported as skipped here, which
    # is the failure that unit tests injecting a service cannot see.
    assert result.trace.searched_sources == RETRIEVABLE_SOURCES
    queried = {name for name, _ in database.calls}
    assert queried == {"skills", "skill_chunks", "project_context", "memories"}
    assert ("project_context", "$vectorSearch") in database.calls
    assert ("memories", "$search") in database.calls


@pytest.mark.asyncio
async def test_the_hybrid_endpoint_is_built_from_the_same_sources(settings: Settings) -> None:
    database = FakeDatabase()
    service = get_retrieval_service(build_request(settings, database))

    await service.hybrid_search("payment idempotency", project_id=PROJECT)

    queried = {name for name, _ in database.calls}
    assert queried == {"skills", "skill_chunks", "project_context", "memories"}


@pytest.mark.asyncio
async def test_searches_are_reported_without_being_stored_by_default(tmp_path: Path) -> None:
    request = build_request(logging_settings(tmp_path, enabled=False), FakeDatabase())

    logger = await get_retrieval_logger(request, unauthenticated_principal())

    assert isinstance(logger, StructuredRetrievalLogger)


@pytest.mark.asyncio
async def test_a_deployment_can_opt_into_storing_what_was_searched(tmp_path: Path) -> None:
    request = build_request(logging_settings(tmp_path, enabled=True), FakeDatabase())

    logger = await get_retrieval_logger(request, unauthenticated_principal())

    assert isinstance(logger, CompositeRetrievalLogger)


@pytest.mark.asyncio
async def test_an_opted_in_deployment_stores_one_record_per_search(tmp_path: Path) -> None:
    database = FakeDatabase()
    request = build_request(logging_settings(tmp_path, enabled=True), database)
    service = await get_search_service(request, unauthenticated_principal())

    await service.search_knowledge("payment idempotency", project_id=PROJECT)

    assert any(name == "retrieval_logs" for name, _ in database.calls)


@pytest.mark.asyncio
async def test_nothing_is_stored_when_a_deployment_did_not_ask(settings: Settings) -> None:
    database = FakeDatabase()
    service = await get_search_service(
        build_request(settings, database), unauthenticated_principal()
    )

    await service.search_knowledge("payment idempotency", project_id=PROJECT)

    assert all(name != "retrieval_logs" for name, _ in database.calls)
