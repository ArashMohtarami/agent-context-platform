"""Intentional-memory persistence model with model-tagged embeddings."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.models.actor import ActorProvenance, system_actor
from app.models.common import (
    ContentText,
    Identifier,
    MongoObjectId,
    ShortText,
    TaggedDocument,
    deterministic_object_id,
)
from app.models.embedding import EmbeddingFields
from app.models.promotion import PromotionProvenance
from app.models.skill import LifecycleStatus

MemoryScope = Literal["global", "project", "agent", "project_agent"]


class MemoryDocument(EmbeddingFields, TaggedDocument):
    """An intentionally stored shared or client-operational memory.

    Memory is never overwritten when it turns out to be wrong: a replacement
    records what it `supersedes`, and the replaced memory records what
    superseded it, so a decision's history survives the correction.
    """

    scope: MemoryScope
    project_id: Identifier | None = None
    client_id: Identifier | None = None
    type: Identifier
    content: ContentText
    summary: ShortText | None = None
    importance: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source: Identifier
    status: LifecycleStatus = "active"
    external_id: Identifier | None = None
    last_used_at: AwareDatetime | None = None
    supersedes: MongoObjectId | None = None
    superseded_by: MongoObjectId | None = None
    promotion_provenance: PromotionProvenance | None = None
    actor: ActorProvenance = Field(default_factory=system_actor)

    @model_validator(mode="before")
    @classmethod
    def assign_external_id(cls, value: object) -> object:
        """Use a scope-aware deterministic `_id` for idempotent writes."""
        if not isinstance(value, Mapping) or "_id" in value or "id" in value:
            return value
        scope = value.get("scope")
        project_id = value.get("project_id")
        external_id = value.get("external_id")
        if not isinstance(scope, str) or not isinstance(external_id, str):
            return value
        if project_id is not None and not isinstance(project_id, str):
            return value
        populated = dict(value)
        client_id = value.get("client_id")
        if client_id is not None and not isinstance(client_id, str):
            return value
        # Keep the V1 global/project id shape unchanged. Agent scopes add the
        # authenticated client identity so two clients may safely reuse an
        # idempotency key without sharing operational memory.
        parts = ["memory", scope, project_id or "global"]
        if scope in {"agent", "project_agent"}:
            if client_id is None:
                return value
            parts.append(client_id)
        parts.append(external_id)
        populated["_id"] = deterministic_object_id(*parts)
        return populated

    @field_validator("last_used_at")
    @classmethod
    def normalize_last_used_at(cls, value: datetime | None) -> datetime | None:
        """Normalize an optional use timestamp to MongoDB's UTC precision."""
        if value is None:
            return None
        normalized = value.astimezone(UTC)
        return normalized.replace(microsecond=(normalized.microsecond // 1_000) * 1_000)

    @model_validator(mode="after")
    def validate_scope(self) -> "MemoryDocument":
        """Require exactly the owner fields appropriate for each scope."""
        expected_owner = {
            "global": (False, False),
            "project": (True, False),
            "agent": (False, True),
            "project_agent": (True, True),
        }[self.scope]
        requires_project, requires_client = expected_owner
        if (self.project_id is not None) != requires_project:
            raise ValueError(f"{self.scope} memory has an invalid project_id")
        if (self.client_id is not None) != requires_client:
            raise ValueError(f"{self.scope} memory has an invalid client_id")
        if self.last_used_at is not None and self.last_used_at < self.created_at:
            raise ValueError("last_used_at must be on or after created_at")
        if self.external_id is not None:
            parts = ["memory", self.scope, self.project_id or "global"]
            if self.scope in {"agent", "project_agent"}:
                if self.client_id is None:  # pragma: no cover - checked above
                    raise ValueError("agent memory requires client_id")
                parts.append(self.client_id)
            parts.append(self.external_id)
            expected = deterministic_object_id(*parts)
            if self.id != expected:
                raise ValueError("_id does not match memory scope and external_id")
        return self

    @model_validator(mode="after")
    def validate_supersession(self) -> "MemoryDocument":
        """Keep a supersession chain meaningful in both directions."""
        if self.supersedes == self.id or self.superseded_by == self.id:
            raise ValueError("a memory cannot supersede itself")
        if self.supersedes is not None and self.supersedes == self.superseded_by:
            raise ValueError("a memory cannot both supersede and be superseded by one memory")
        if self.superseded_by is not None and self.status != "superseded":
            raise ValueError("a memory that was replaced must have superseded status")
        return self
