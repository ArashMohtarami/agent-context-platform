"""Typed values passed through the project and context pipelines."""

from dataclasses import dataclass

from bson import ObjectId

from app.models.project_context import ProjectContextDocument
from app.skills.types import RetrievalMethod


def context_key(project_id: str, document_id: ObjectId) -> str:
    """Return the stable retrieval identity of one context document.

    The project is part of the key so two projects' entries can never collide
    while being merged or deduplicated.
    """
    return f"project_context:{project_id}:{document_id}"


@dataclass(frozen=True, slots=True)
class ProjectContextMatch:
    """One context document returned by a search with its relevance score."""

    context: ProjectContextDocument
    score: float

    @property
    def key(self) -> str:
        """Return the stable identity used to merge and deduplicate matches."""
        return context_key(self.context.project_id, self.context.id)


@dataclass(frozen=True, slots=True)
class ContextSearchResult:
    """One ranked context document with the searches that found it."""

    context: ProjectContextDocument
    score: float
    methods: tuple[RetrievalMethod, ...]
