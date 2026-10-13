"""Persisted embedding metadata invariants for vector-bearing documents."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.db.schema import COLLECTION_VALIDATORS
from app.models.skill_chunk import SkillChunkDocument

GENERATED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def build_chunk(**overrides: Any) -> SkillChunkDocument:
    """Build a chunk document with optional embedding overrides."""
    values: dict[str, Any] = {
        "skill_id": "python",
        "skill_version": "1.0.0",
        "chunk_id": "chunk-0000",
        "content": "Typed Python",
        "section": "Introduction",
        "chunk_index": 0,
        "tokens": 2,
    }
    values.update(overrides)
    return SkillChunkDocument(**values)


def test_a_chunk_without_a_vector_stays_valid() -> None:
    chunk = build_chunk()

    assert chunk.has_embedding is False
    assert chunk.embedding_model is None
    assert chunk.embedding_dimensions is None


def test_a_vector_is_stored_with_its_model_metadata() -> None:
    chunk = build_chunk(
        embedding=[0.1, 0.2, 0.3],
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
        embedding_dimensions=3,
        embedding_generated_at=GENERATED_AT,
    )

    document = chunk.model_dump(by_alias=True)

    assert chunk.has_embedding is True
    assert document["embedding"] == [0.1, 0.2, 0.3]
    assert document["embedding_model"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert document["embedding_dimensions"] == 3
    assert document["embedding_generated_at"] == GENERATED_AT


@pytest.mark.parametrize(
    "overrides",
    [
        {"embedding": [0.1, 0.2]},
        {"embedding_model": "model-only"},
        {"embedding_dimensions": 3},
        {"embedding_generated_at": GENERATED_AT},
        {
            "embedding": [0.1, 0.2],
            "embedding_model": "model",
            "embedding_dimensions": 2,
        },
    ],
)
def test_incomplete_embedding_metadata_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="stored together"):
        build_chunk(**overrides)


def test_a_vector_that_contradicts_its_dimensions_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must equal embedding_dimensions"):
        build_chunk(
            embedding=[0.1, 0.2, 0.3],
            embedding_model="model",
            embedding_dimensions=4,
            embedding_generated_at=GENERATED_AT,
        )


@pytest.mark.parametrize(
    "values",
    [[float("nan")], [float("inf")], [True], ["0.1"], [None]],
)
def test_values_that_are_not_finite_numbers_are_rejected(values: list[Any]) -> None:
    with pytest.raises(ValidationError):
        build_chunk(
            embedding=values,
            embedding_model="model",
            embedding_dimensions=len(values),
            embedding_generated_at=GENERATED_AT,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"embedding_dimensions": 0},
        {"embedding_dimensions": 4_097},
        {"embedding_model": "m" * 257},
        {"embedding": []},
    ],
)
def test_metadata_outside_the_supported_bounds_is_rejected(overrides: dict[str, Any]) -> None:
    values: dict[str, Any] = {
        "embedding": [0.1],
        "embedding_model": "model",
        "embedding_dimensions": 1,
        "embedding_generated_at": GENERATED_AT,
    }
    values.update(overrides)

    with pytest.raises(ValidationError):
        build_chunk(**values)


def test_the_generated_timestamp_uses_mongodb_millisecond_precision() -> None:
    chunk = build_chunk(
        embedding=[0.1],
        embedding_model="model",
        embedding_dimensions=1,
        embedding_generated_at=datetime(2026, 1, 1, 12, 0, 0, 123_456, tzinfo=UTC),
    )

    assert chunk.embedding_generated_at == datetime(2026, 1, 1, 12, 0, 0, 123_000, tzinfo=UTC)


def test_only_retrievable_collections_accept_vectors() -> None:
    # V2 indexes current ideas, briefs, and content items. V3 extends this
    # to searchable research artifacts; immutable versions and registry-only
    # collections remain vector-free.
    embeddable = {
        "skill_chunks",
        "project_context",
        "memories",
        "content_ideas",
        "content_briefs",
        "content_items",
        "research_chunks",
        "research_notes",
        "research_claims",
        "research_briefs",
        "knowledge_candidates",
    }
    for name, validator in COLLECTION_VALIDATORS.items():
        wrapper = validator["$and"][0] if name == "memories" else validator
        schema = wrapper["$jsonSchema"]
        assert isinstance(schema, dict)
        has_embedding = "embedding" in schema["properties"]
        assert has_embedding is (name in embeddable), name


def test_the_project_context_validator_requires_complete_vector_metadata() -> None:
    schema = COLLECTION_VALIDATORS["project_context"]["$jsonSchema"]
    assert isinstance(schema, dict)

    assert set(schema["required"]) >= {
        "embedding",
        "embedding_model",
        "embedding_dimensions",
        "embedding_generated_at",
    }
    assert schema["properties"]["embedding"]["items"] == {"bsonType": "double"}


def test_the_memory_validator_requires_complete_vector_metadata() -> None:
    schema = COLLECTION_VALIDATORS["memories"]["$and"][0]["$jsonSchema"]
    assert isinstance(schema, dict)

    assert set(schema["required"]) >= {
        "embedding",
        "embedding_model",
        "embedding_dimensions",
        "embedding_generated_at",
    }
    assert schema["properties"]["embedding"]["items"] == {"bsonType": "double"}
    # The supersession link is stored as a document reference, never a string.
    assert schema["properties"]["superseded_by"]["bsonType"] == ["objectId", "null"]


def test_the_skill_chunk_validator_requires_complete_vector_metadata() -> None:
    schema = COLLECTION_VALIDATORS["skill_chunks"]["$jsonSchema"]
    assert isinstance(schema, dict)

    assert set(schema["required"]) >= {
        "embedding",
        "embedding_model",
        "embedding_dimensions",
        "embedding_generated_at",
    }
    assert schema["properties"]["embedding"]["items"] == {"bsonType": "double"}
    assert schema["properties"]["embedding"]["maxItems"] == 4_096
    assert schema["properties"]["embedding"]["bsonType"] == ["array", "null"]
