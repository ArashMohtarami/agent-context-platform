"""Embedding generation for application services and indexing jobs."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from time import perf_counter
from typing import Final

from app.core.config import Settings
from app.embeddings.base import EmbeddingProvider
from app.embeddings.config import EmbeddingConfiguration
from app.embeddings.errors import EmbeddingCompatibilityError, EmbeddingInputError
from app.embeddings.local import LocalEmbeddingProvider
from app.embeddings.types import EmbeddingHealth, EmbeddingModelInfo, EmbeddingVector
from app.observability.instruments import record_embedding

# The two things this service is asked for, measured apart. A query is one
# short text on the path of a live search; documents are many longer texts on
# an import. One number covering both would describe neither.
QUERY: Final = "query"
DOCUMENTS: Final = "documents"


@contextmanager
def _measured(operation: str) -> Iterator[None]:
    """Record how long one embedding operation took, and whether it failed.

    A failure is timed too. An embedding that fails slowly is a different
    problem from one that fails immediately, and the duration is what tells
    a loaded model apart from a missing one.
    """
    started = perf_counter()
    failed = True
    try:
        yield
        failed = False
    finally:
        record_embedding(operation=operation, seconds=perf_counter() - started, failed=failed)


class EmbeddingService:
    """Bind every stored vector to exactly one active model and dimension.

    The service owns the policy; the provider owns the model. Callers never see
    the backend, so a future provider can be swapped in without touching them.
    """

    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider

    @property
    def model(self) -> str:
        """Return the active embedding model identifier."""
        return self._provider.configuration.model

    @property
    def dimensions(self) -> int:
        """Return the active embedding dimensions."""
        return self._provider.configuration.dimensions

    async def start(self) -> EmbeddingModelInfo:
        """Load the model once so startup fails loudly instead of on first use."""
        return await self._provider.load()

    async def embed_query(self, text: str) -> EmbeddingVector:
        """Embed one search query with the active model."""
        with _measured(QUERY):
            return self.verify(await self._provider.embed_text(text))

    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        """Embed many chunk texts in order.

        Indexing a large document can exceed the provider's per-call text limit,
        so the work is windowed to that limit rather than rejected.
        """
        if not contents:
            raise EmbeddingInputError("at least one text is required")
        window = self._provider.configuration.max_batch_texts
        vectors: list[EmbeddingVector] = []
        # Measured around the whole call rather than per window, because that
        # is what a caller waited for.
        with _measured(DOCUMENTS):
            for start in range(0, len(contents), window):
                group = contents[start : start + window]
                produced = await self._provider.embed_documents(group)
                if len(produced) != len(group):
                    raise EmbeddingCompatibilityError(
                        "the embedding provider returned a different number of vectors"
                    )
                vectors.extend(self.verify(vector) for vector in produced)
        return tuple(vectors)

    def verify(self, vector: EmbeddingVector) -> EmbeddingVector:
        """Reject a vector produced by a different model or dimension.

        This is the single guard that keeps incompatible vectors from being
        written next to each other during an embedding-model migration.
        """
        configuration = self._provider.configuration
        if vector.model != configuration.model:
            raise EmbeddingCompatibilityError(
                "the vector was produced by a different embedding model"
            )
        if vector.dimensions != configuration.dimensions:
            raise EmbeddingCompatibilityError(
                "the vector does not match the configured embedding dimensions"
            )
        return vector

    async def health(self) -> EmbeddingHealth:
        """Report embedding availability for readiness checks."""
        return await self._provider.health()

    async def close(self) -> None:
        """Release the embedding model during shutdown."""
        await self._provider.close()


def build_embedding_service(settings: Settings) -> EmbeddingService | None:
    """Build the configured local provider, or nothing when embeddings are disabled."""
    if not settings.embedding_enabled:
        return None
    return EmbeddingService(LocalEmbeddingProvider(EmbeddingConfiguration.from_settings(settings)))
