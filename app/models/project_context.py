"""Project-context persistence model with model-tagged embeddings."""

from typing import Literal

from pydantic import Field, model_validator

from app.models.actor import ActorProvenance, system_actor
from app.models.common import (
    ContentText,
    Identifier,
    ShortText,
    TaggedDocument,
    deterministic_object_id,
    populate_deterministic_id,
)
from app.models.embedding import EmbeddingFields
from app.models.promotion import PromotionProvenance
from app.models.skill import LifecycleStatus

ContextType = Literal[
    "architecture",
    "architecture_decision",
    "business_rule",
    "api",
    "database",
    "infrastructure",
    "deployment",
    "security",
    "integration",
    "convention",
    "troubleshooting",
    "known_issue",
    "implementation",
    "constraint",
]


class ProjectContextDocument(EmbeddingFields, TaggedDocument):
    """Durable technical knowledge bound to exactly one project.

    The vector travels with the model that produced it, exactly as it does for
    skill chunks, so one embedding-model migration can find and re-embed every
    retrievable document without mixing incompatible vectors.
    """

    project_id: Identifier
    type: ContextType
    title: ShortText
    content: ContentText
    importance: float = Field(ge=0.0, le=1.0)
    source: Identifier
    status: LifecycleStatus = "active"
    external_id: Identifier | None = None
    promotion_provenance: PromotionProvenance | None = None
    actor: ActorProvenance = Field(default_factory=system_actor)

    @model_validator(mode="before")
    @classmethod
    def assign_external_id(cls, value: object) -> object:
        """Use a deterministic `_id` when an idempotency key is supplied."""
        return populate_deterministic_id(
            value,
            namespace="project_context",
            fields=("project_id", "external_id"),
        )

    @model_validator(mode="after")
    def validate_external_id(self) -> "ProjectContextDocument":
        """Keep an external idempotency key aligned with `_id`."""
        if self.external_id is not None:
            expected = deterministic_object_id("project_context", self.project_id, self.external_id)
            if self.id != expected:
                raise ValueError("_id does not match project_id and external_id")
        return self
