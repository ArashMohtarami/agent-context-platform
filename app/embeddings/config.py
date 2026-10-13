"""Deterministic embedding configuration derived from application settings."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.embeddings.errors import EmbeddingConfigurationError
from app.embeddings.types import (
    MAX_EMBEDDING_ATTEMPTS,
    MAX_EMBEDDING_BATCH_SIZE,
    MAX_EMBEDDING_BATCH_TEXTS,
    MAX_EMBEDDING_DIMENSIONS,
    MAX_EMBEDDING_MODEL_NAME_LENGTH,
    MAX_EMBEDDING_RETRY_BACKOFF_SECONDS,
    MAX_EMBEDDING_TEXT_CHARACTERS,
    SUPPORTED_EMBEDDING_DEVICES,
    EmbeddingDevice,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for typing only
    from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class EmbeddingConfiguration:
    """The immutable contract a local embedding provider is built from.

    Two providers built from the same environment always describe their vectors
    identically, which is what keeps stored model metadata deterministic.
    """

    model: str
    dimensions: int
    device: EmbeddingDevice = "auto"
    batch_size: int = 32
    max_batch_texts: int = 256
    max_text_characters: int = 20_000
    normalize: bool = True
    max_attempts: int = 3
    retry_backoff_seconds: float = 0.5
    cache_directory: Path | None = None
    allow_downloads: bool = True

    def __post_init__(self) -> None:
        """Reject configuration that cannot produce comparable vectors."""
        _validate_model(self.model)
        _require_range("EMBEDDING_DIMENSIONS", self.dimensions, 1, MAX_EMBEDDING_DIMENSIONS)
        if self.device not in SUPPORTED_EMBEDDING_DEVICES:
            raise EmbeddingConfigurationError(
                "EMBEDDING_DEVICE must be auto, cpu, cuda, or mps",
            )
        _require_range("EMBEDDING_BATCH_SIZE", self.batch_size, 1, MAX_EMBEDDING_BATCH_SIZE)
        _require_range(
            "EMBEDDING_MAX_BATCH_TEXTS",
            self.max_batch_texts,
            1,
            MAX_EMBEDDING_BATCH_TEXTS,
        )
        _require_range(
            "EMBEDDING_MAX_TEXT_CHARACTERS",
            self.max_text_characters,
            1,
            MAX_EMBEDDING_TEXT_CHARACTERS,
        )
        _require_range("EMBEDDING_MAX_ATTEMPTS", self.max_attempts, 1, MAX_EMBEDDING_ATTEMPTS)
        if not isinstance(self.normalize, bool) or not isinstance(self.allow_downloads, bool):
            raise EmbeddingConfigurationError("embedding switches must be boolean values")
        if not 0.0 <= self.retry_backoff_seconds <= MAX_EMBEDDING_RETRY_BACKOFF_SECONDS:
            raise EmbeddingConfigurationError(
                "EMBEDDING_RETRY_BACKOFF_SECONDS must be between 0 and "
                f"{MAX_EMBEDDING_RETRY_BACKOFF_SECONDS}"
            )
        if self.batch_size > self.max_batch_texts:
            raise EmbeddingConfigurationError(
                "EMBEDDING_BATCH_SIZE must not exceed EMBEDDING_MAX_BATCH_TEXTS"
            )

    @classmethod
    def from_settings(cls, settings: "Settings") -> "EmbeddingConfiguration":
        """Build the provider contract from environment-driven settings."""
        return cls(
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            device=settings.embedding_device,
            batch_size=settings.embedding_batch_size,
            max_batch_texts=settings.embedding_max_batch_texts,
            max_text_characters=settings.embedding_max_text_characters,
            normalize=settings.embedding_normalize,
            max_attempts=settings.embedding_max_attempts,
            retry_backoff_seconds=settings.embedding_retry_backoff_seconds,
            cache_directory=settings.embedding_model_cache_directory,
            allow_downloads=settings.embedding_allow_downloads,
        )


def _validate_model(model: str) -> None:
    """Require a usable local path or model repository identifier."""
    if not model or model != model.strip():
        raise EmbeddingConfigurationError("EMBEDDING_MODEL must not be empty or padded")
    if len(model) > MAX_EMBEDDING_MODEL_NAME_LENGTH:
        raise EmbeddingConfigurationError(
            f"EMBEDDING_MODEL must be at most {MAX_EMBEDDING_MODEL_NAME_LENGTH} characters"
        )
    if any(character.isspace() or ord(character) < 32 for character in model):
        raise EmbeddingConfigurationError("EMBEDDING_MODEL must not contain control characters")


def _require_range(name: str, value: int, minimum: int, maximum: int) -> None:
    """Reject non-integer and out-of-range numeric configuration."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise EmbeddingConfigurationError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise EmbeddingConfigurationError(f"{name} must be between {minimum} and {maximum}")
