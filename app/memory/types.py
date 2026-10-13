"""Typed values passed through the memory pipelines."""

from dataclasses import dataclass

from bson import ObjectId

from app.models.memory import MemoryDocument
from app.skills.types import RetrievalMethod


def memory_key(
    scope: str,
    project_id: str | None,
    client_id: str | None,
    document_id: ObjectId,
) -> str:
    """Return the stable retrieval identity of one memory.

    The scope is part of the key so a global memory and a project memory can
    never collide while being merged or deduplicated.
    """
    shared = f"memory:{scope}:{project_id or 'global'}"
    return f"{shared}:{document_id}" if client_id is None else f"{shared}:{client_id}:{document_id}"


@dataclass(frozen=True, slots=True)
class MemoryMatch:
    """One memory returned by a search with its relevance score."""

    memory: MemoryDocument
    score: float

    @property
    def key(self) -> str:
        """Return the stable identity used to merge and deduplicate matches."""
        return memory_key(
            self.memory.scope,
            self.memory.project_id,
            self.memory.client_id,
            self.memory.id,
        )


@dataclass(frozen=True, slots=True)
class MemorySearchResult:
    """One ranked memory with the searches that found it."""

    memory: MemoryDocument
    score: float
    methods: tuple[RetrievalMethod, ...]


@dataclass(frozen=True, slots=True)
class MemorySupersession:
    """The result of replacing one memory with another."""

    created: MemoryDocument
    superseded: MemoryDocument
