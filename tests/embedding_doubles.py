"""Deterministic embedding test doubles shared by unit and integration tests."""

import threading
from collections.abc import Sequence
from dataclasses import replace
from hashlib import sha256
from typing import Any

from app.embeddings.config import EmbeddingConfiguration
from app.embeddings.types import ResolvedEmbeddingDevice

BASE_CONFIGURATION = EmbeddingConfiguration(
    model="stub-embedding-model",
    dimensions=4,
    device="cpu",
    batch_size=2,
    max_batch_texts=8,
    max_text_characters=200,
    retry_backoff_seconds=0.0,
)


def configuration(**overrides: Any) -> EmbeddingConfiguration:
    """Return the shared stub configuration with selected overrides."""
    return replace(BASE_CONFIGURATION, **overrides)


def expected_values(text: str, dimensions: int) -> tuple[float, ...]:
    """Return the vector `StubEmbeddingModel` produces for one text."""
    digest = sha256(text.encode("utf-8")).digest()
    return tuple(digest[index % len(digest)] / 255 for index in range(dimensions))


class StubEmbeddingModel:
    """A deterministic stand-in for a loaded sentence-transformer model."""

    def __init__(
        self,
        dimensions: int = 4,
        *,
        declares_dimensions: bool = True,
        failures: int = 0,
        row_length: int | None = None,
        result: object | None = None,
    ) -> None:
        self.dimensions = dimensions
        self.max_seq_length = 128
        self.batches: list[list[str]] = []
        self.batch_sizes: list[int] = []
        self.normalize_flags: list[bool] = []
        self.threads: set[int] = set()
        self._declares_dimensions = declares_dimensions
        self._failures = failures
        self._row_length = dimensions if row_length is None else row_length
        self._result = result

    def get_sentence_embedding_dimension(self) -> int | None:
        """Report the declared dimensions unless the model hides them."""
        return self.dimensions if self._declares_dimensions else None

    def encode(
        self,
        inputs: Sequence[str],
        /,
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> object:
        """Record the call and return deterministic per-text vectors."""
        _ = convert_to_numpy, show_progress_bar
        self.batches.append(list(inputs))
        self.batch_sizes.append(batch_size)
        self.normalize_flags.append(normalize_embeddings)
        self.threads.add(threading.get_ident())
        if self._failures > 0:
            self._failures -= 1
            raise RuntimeError("transient backend failure")
        if self._result is not None:
            return self._result
        return [list(expected_values(text, self._row_length)) for text in inputs]


class RenamedAccessorModel(StubEmbeddingModel):
    """A backend that only exposes the newer dimension accessor name."""

    def __init__(self, dimensions: int = 4) -> None:
        super().__init__(dimensions, declares_dimensions=False)

    def get_embedding_dimension(self) -> int:
        """Report dimensions under the renamed accessor."""
        return self.dimensions


class RecordingLoader:
    """Record how often and with which device a model is loaded."""

    def __init__(self, model: StubEmbeddingModel | None = None, error: Exception | None = None):
        self.model = StubEmbeddingModel() if model is None else model
        self.error = error
        self.calls: list[tuple[EmbeddingConfiguration, ResolvedEmbeddingDevice]] = []

    def __call__(
        self,
        configuration: EmbeddingConfiguration,
        device: ResolvedEmbeddingDevice,
    ) -> StubEmbeddingModel:
        """Return the stub model, or fail the way a missing model would."""
        self.calls.append((configuration, device))
        if self.error is not None:
            raise self.error
        return self.model
