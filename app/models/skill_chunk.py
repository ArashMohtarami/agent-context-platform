"""Skill chunk persistence model with optional model-tagged embeddings."""

from pydantic import Field, model_validator

from app.models.common import (
    ContentText,
    Identifier,
    Metadata,
    MongoDocumentModel,
    ShortText,
    deterministic_object_id,
    populate_deterministic_id,
)
from app.models.embedding import EmbeddingFields
from app.models.skill import SemanticVersion


class SkillChunkDocument(EmbeddingFields, MongoDocumentModel):
    """A deterministic section-aware chunk of one skill version."""

    skill_id: Identifier
    skill_version: SemanticVersion
    chunk_id: Identifier
    content: ContentText
    section: ShortText | None = None
    chunk_index: int = Field(ge=0)
    tokens: int = Field(ge=0, le=100_000)
    metadata: Metadata = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, value: object) -> object:
        """Derive `_id` from the complete chunk natural key."""
        return populate_deterministic_id(
            value,
            namespace="skill_chunk",
            fields=("skill_id", "skill_version", "chunk_id"),
        )

    @model_validator(mode="after")
    def validate_id(self) -> "SkillChunkDocument":
        """Prevent chunk identity drift."""
        expected = deterministic_object_id(
            "skill_chunk",
            self.skill_id,
            self.skill_version,
            self.chunk_id,
        )
        if self.id != expected:
            raise ValueError("_id does not match the chunk natural key")
        return self
