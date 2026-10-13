"""Typed values shared by embedding providers, services, and health checks."""

from dataclasses import dataclass
from math import isfinite
from typing import Final, Literal

from app.embeddings.errors import EmbeddingDimensionError

MAX_EMBEDDING_DIMENSIONS: Final = 4_096
MAX_EMBEDDING_MODEL_NAME_LENGTH: Final = 256
MAX_EMBEDDING_BATCH_SIZE: Final = 512
MAX_EMBEDDING_BATCH_TEXTS: Final = 2_048
MAX_EMBEDDING_TEXT_CHARACTERS: Final = 200_000
MAX_EMBEDDING_ATTEMPTS: Final = 10
MAX_EMBEDDING_RETRY_BACKOFF_SECONDS: Final = 30.0

EmbeddingDevice = Literal["auto", "cpu", "cuda", "mps"]
ResolvedEmbeddingDevice = Literal["cpu", "cuda", "mps"]
SUPPORTED_EMBEDDING_DEVICES: Final[frozenset[str]] = frozenset({"auto", "cpu", "cuda", "mps"})
ACCELERATOR_PREFERENCE: Final[tuple[ResolvedEmbeddingDevice, ...]] = ("cuda", "mps")


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    """One vector that always carries the model metadata needed to compare it."""

    model: str
    dimensions: int
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        """Reject vectors that could be stored next to incompatible vectors."""
        if not self.model or len(self.model) > MAX_EMBEDDING_MODEL_NAME_LENGTH:
            raise EmbeddingDimensionError("an embedding must name its embedding model")
        if not 1 <= self.dimensions <= MAX_EMBEDDING_DIMENSIONS:
            raise EmbeddingDimensionError(
                f"embedding dimensions must be between 1 and {MAX_EMBEDDING_DIMENSIONS}"
            )
        if not isinstance(self.values, tuple):
            raise EmbeddingDimensionError("embedding values must be an immutable tuple")
        if len(self.values) != self.dimensions:
            raise EmbeddingDimensionError(
                "embedding length must equal the declared embedding dimensions"
            )
        if not all(isfinite(value) for value in self.values):
            raise EmbeddingDimensionError("embedding values must be finite numbers")


@dataclass(frozen=True, slots=True)
class EmbeddingModelInfo:
    """Metadata discovered from the loaded local model."""

    model: str
    dimensions: int
    device: ResolvedEmbeddingDevice
    normalized: bool
    max_sequence_length: int | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingHealth:
    """Credential-free embedding readiness details safe to return to clients."""

    available: bool
    model: str
    dimensions: int | None = None
    device: ResolvedEmbeddingDevice | None = None
    detail: str | None = None
