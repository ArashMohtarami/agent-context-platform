"""Live MongoDB checks for model-tagged skill chunk vectors."""

import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from bson import ObjectId
from pymongo.errors import WriteError

from app.core.config import get_settings
from app.db.client import MongoDBClient
from app.db.initializer import initialize_database
from app.embeddings.types import EmbeddingVector
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.services.skill_service import SkillService
from app.skills.chunker import MarkdownSkillChunker
from app.skills.importer import SkillImporter
from app.skills.parser import SkillParser

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
]

DIMENSIONS = 8
MODEL = "phase5-stub-model"


class CountingEmbedder:
    """Return one deterministic vector per chunk without loading a model."""

    def __init__(self) -> None:
        self.embedded = 0

    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        self.embedded += len(contents)
        return tuple(
            EmbeddingVector(
                model=MODEL,
                dimensions=DIMENSIONS,
                values=tuple(float(len(content) % 7) / 7 for _ in range(DIMENSIONS)),
            )
            for content in contents
        )


def skill_markdown(skill_id: str) -> str:
    """Build a canonical skill used only by this integration run."""
    return f"""---
id: {skill_id}
name: Phase 5 Embedding Skill
version: 1.0.0
description: Embedded skill chunk integration coverage.
category: phase5-integration
trust_level: generated
tags:
  - phase5
---

# Phase 5 Embedding Skill

## Rules

Payment requests use idempotency keys.

## Verification

Vectors are stored with their embedding model metadata.
"""


@asynccontextmanager
async def skill_database(skill_id: str) -> AsyncIterator[MongoDBClient]:
    """Connect and remove only records owned by this test."""
    mongodb = MongoDBClient(get_settings())
    await mongodb.connect()
    try:
        await initialize_database(mongodb.database)
        yield mongodb
    finally:
        await mongodb.database["skill_chunks"].delete_many({"skill_id": skill_id})
        await mongodb.database["skills"].delete_many({"skill_id": skill_id})
        await mongodb.close()


@pytest.mark.asyncio
async def test_imported_chunks_store_vectors_with_model_metadata(tmp_path: Path) -> None:
    skill_id = f"phase5-skill-{ObjectId()}"
    root = tmp_path / "skills"
    root.mkdir()
    (root / "embedded.md").write_text(skill_markdown(skill_id), encoding="utf-8")
    embedder = CountingEmbedder()

    async with skill_database(skill_id) as mongodb:
        chunks = MongoSkillChunkRepository(mongodb.database)
        importer = SkillImporter(
            SkillParser(max_file_bytes=100_000),
            MarkdownSkillChunker(min_tokens=20, target_tokens=30, max_tokens=45, overlap_tokens=5),
            SkillService(MongoSkillRepository(mongodb.database), chunks, embedder),
        )

        report = await importer.import_directory(root)
        stored = await chunks.list_for_version(skill_id, "1.0.0", limit=50)

        assert report.chunks_created == report.chunks_embedded == embedder.embedded
        assert stored.items
        for chunk in stored.items:
            assert chunk.embedding is not None
            assert len(chunk.embedding) == DIMENSIONS
            assert chunk.embedding_model == MODEL
            assert chunk.embedding_dimensions == DIMENSIONS
            assert chunk.embedding_generated_at is not None


@pytest.mark.asyncio
async def test_mongodb_rejects_vectors_without_complete_model_metadata() -> None:
    skill_id = f"phase5-invalid-{ObjectId()}"

    async with skill_database(skill_id) as mongodb:
        collection = mongodb.database["skill_chunks"]
        document = {
            "_id": ObjectId(),
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "revision": 1,
            "skill_id": skill_id,
            "skill_version": "1.0.0",
            "chunk_id": "chunk-0000",
            "content": "Payment requests use idempotency keys.",
            "section": "Rules",
            "chunk_index": 0,
            "tokens": 6,
            "metadata": {},
            "embedding": [0.1] * DIMENSIONS,
            "embedding_dimensions": DIMENSIONS,
            "embedding_generated_at": datetime.now(UTC),
        }

        with pytest.raises(WriteError):
            await collection.insert_one(document)

        document["embedding_model"] = "x" * 257
        with pytest.raises(WriteError):
            await collection.insert_one(document)

        document["embedding_model"] = MODEL
        document["embedding"] = ["not-a-number"] * DIMENSIONS
        with pytest.raises(WriteError):
            await collection.insert_one(document)

        assert await collection.count_documents({"skill_id": skill_id}) == 0
