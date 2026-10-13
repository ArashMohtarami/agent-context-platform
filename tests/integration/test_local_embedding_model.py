"""Opt-in checks that load the real configured local embedding model."""

import os
from math import sqrt
from pathlib import Path

import pytest

from app.embeddings.config import EmbeddingConfiguration
from app.embeddings.local import LocalEmbeddingProvider
from app.embeddings.types import EmbeddingVector

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_EMBEDDING_MODEL_TESTS") != "1",
        reason="set RUN_EMBEDDING_MODEL_TESTS=1 to load the configured local model",
    ),
]

RELATED_QUERY = "payment idempotency"
RELATED_QUESTION = "How do we prevent duplicate payment requests?"
UNRELATED_TEXT = "The office coffee machine is descaled every second Friday."


def build_configuration() -> EmbeddingConfiguration:
    """Read the same environment the application uses, pinned to CPU."""
    cache = os.getenv("EMBEDDING_MODEL_CACHE_DIRECTORY")
    return EmbeddingConfiguration(
        model=os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
        dimensions=int(os.getenv("EMBEDDING_DIMENSIONS", "384")),
        device="cpu",
        batch_size=2,
        cache_directory=None if cache is None else Path(cache),
    )


def cosine_similarity(left: EmbeddingVector, right: EmbeddingVector) -> float:
    """Return the cosine similarity MongoDB Vector Search will later use."""
    dot = sum(a * b for a, b in zip(left.values, right.values, strict=True))
    left_norm = sqrt(sum(value * value for value in left.values))
    right_norm = sqrt(sum(value * value for value in right.values))
    return dot / (left_norm * right_norm)


@pytest.mark.asyncio
async def test_the_configured_local_model_embeds_on_cpu_with_stable_dimensions() -> None:
    configuration = build_configuration()
    provider = LocalEmbeddingProvider(configuration)
    try:
        info = await provider.load()
        health = await provider.health()
        vectors = await provider.embed_documents([RELATED_QUERY, RELATED_QUESTION, UNRELATED_TEXT])
        repeated = await provider.embed_text(RELATED_QUERY)
    finally:
        await provider.close()

    assert info.device == "cpu"
    assert info.dimensions == configuration.dimensions
    assert (health.available, health.device) == (True, "cpu")
    assert len(vectors) == 3
    assert all(vector.dimensions == configuration.dimensions for vector in vectors)
    assert all(len(vector.values) == configuration.dimensions for vector in vectors)
    assert all(vector.model == configuration.model for vector in vectors)
    # Transformer inference is batch-sensitive in its last bits, so the same
    # text embedded alone must match rather than be bit-identical.
    assert repeated.values == pytest.approx(vectors[0].values, abs=1e-5)


@pytest.mark.asyncio
async def test_related_technical_text_is_closer_than_unrelated_text() -> None:
    configuration = build_configuration()
    provider = LocalEmbeddingProvider(configuration)
    try:
        query, question, unrelated = await provider.embed_documents(
            [RELATED_QUERY, RELATED_QUESTION, UNRELATED_TEXT]
        )
    finally:
        await provider.close()

    related_similarity = cosine_similarity(query, question)
    unrelated_similarity = cosine_similarity(query, unrelated)

    assert related_similarity > unrelated_similarity
    assert related_similarity > 0.25
    assert unrelated_similarity < 0.2


@pytest.mark.asyncio
async def test_normalized_vectors_are_unit_length() -> None:
    configuration = build_configuration()
    provider = LocalEmbeddingProvider(configuration)
    try:
        vector = await provider.embed_text(RELATED_QUERY)
    finally:
        await provider.close()

    magnitude = sqrt(sum(value * value for value in vector.values))

    assert configuration.normalize is True
    assert magnitude == pytest.approx(1.0, abs=1e-5)
