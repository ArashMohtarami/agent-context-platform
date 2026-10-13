"""Vector storage fields shared by every embeddable document."""

from datetime import datetime
from math import isfinite
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.embeddings.types import MAX_EMBEDDING_DIMENSIONS, MAX_EMBEDDING_MODEL_NAME_LENGTH
from app.models.common import normalize_mongo_timestamp

EmbeddingModelName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_EMBEDDING_MODEL_NAME_LENGTH,
    ),
]


class EmbeddingFields(BaseModel):
    """A vector that always travels with the model metadata that produced it.

    Storing the model and its dimensions next to every vector is what allows a
    later embedding-model migration to find, re-embed, and separate vectors
    instead of silently mixing incompatible ones in one collection.
    """

    embedding: list[float] | None = Field(default=None, max_length=MAX_EMBEDDING_DIMENSIONS)
    embedding_model: EmbeddingModelName | None = None
    embedding_dimensions: int | None = Field(
        default=None,
        ge=1,
        le=MAX_EMBEDDING_DIMENSIONS,
    )
    embedding_generated_at: AwareDatetime | None = None

    @field_validator("embedding", mode="before")
    @classmethod
    def reject_non_numeric_values(cls, value: object) -> object:
        """Reject values BSON would not store as a double, including booleans."""
        if isinstance(value, list) and any(
            isinstance(item, bool) or not isinstance(item, int | float) for item in value
        ):
            raise ValueError("embedding values must be numbers")
        return value

    @field_validator("embedding")
    @classmethod
    def validate_vector_values(cls, value: list[float] | None) -> list[float] | None:
        """Reject vectors a similarity search could never rank."""
        if value is None:
            return None
        if not value:
            raise ValueError("embedding must not be empty")
        if any(not isfinite(item) for item in value):
            raise ValueError("embedding values must be finite numbers")
        return value

    @field_validator("embedding_dimensions")
    @classmethod
    def validate_dimensions(cls, value: int | None) -> int | None:
        """Reject booleans, which BSON otherwise treats as integer-like values."""
        if isinstance(value, bool):
            raise ValueError("embedding_dimensions must be an integer")
        return value

    @field_validator("embedding_generated_at")
    @classmethod
    def normalize_generated_at(cls, value: datetime | None) -> datetime | None:
        """Normalize the timestamp to MongoDB's UTC millisecond precision."""
        return None if value is None else normalize_mongo_timestamp(value)

    @property
    def has_embedding(self) -> bool:
        """Report whether this document currently carries a vector."""
        return self.embedding is not None

    @model_validator(mode="after")
    def validate_embedding_metadata(self) -> "EmbeddingFields":
        """Require complete, self-consistent embedding metadata on every document."""
        present = (
            self.embedding is not None,
            self.embedding_model is not None,
            self.embedding_dimensions is not None,
            self.embedding_generated_at is not None,
        )
        if any(present) and not all(present):
            raise ValueError(
                "embedding, embedding_model, embedding_dimensions, and "
                "embedding_generated_at must be stored together"
            )
        if self.embedding is not None and len(self.embedding) != self.embedding_dimensions:
            raise ValueError("embedding length must equal embedding_dimensions")
        return self
