"""The embedding provider abstraction every application service depends on."""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.embeddings.config import EmbeddingConfiguration
from app.embeddings.types import EmbeddingHealth, EmbeddingModelInfo, EmbeddingVector


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Produce vectors without exposing the embedding backend to callers.

    Implementations must be safe to call from an async request handler, so any
    CPU-bound work belongs in a worker thread rather than the event loop.
    """

    @property
    def configuration(self) -> EmbeddingConfiguration:
        """Return the immutable configuration this provider was built from."""
        ...

    async def load(self) -> EmbeddingModelInfo:
        """Load the model at most once and return its discovered metadata."""
        ...

    async def embed_text(self, text: str) -> EmbeddingVector:
        """Embed one text and return the vector with its model metadata."""
        ...

    async def embed_documents(self, texts: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        """Embed many texts in configured batches, preserving input order."""
        ...

    async def health(self) -> EmbeddingHealth:
        """Report model availability without raising or leaking internals."""
        ...

    async def close(self) -> None:
        """Release the loaded model so shutdown can free its memory."""
        ...
