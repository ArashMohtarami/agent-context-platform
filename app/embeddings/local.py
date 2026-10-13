"""Local sentence-transformer-compatible embedding provider."""

import asyncio
import logging
from collections.abc import Callable, Iterable, Sequence
from importlib import import_module
from time import perf_counter
from typing import Protocol, cast

from app.embeddings.config import EmbeddingConfiguration
from app.embeddings.errors import (
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingInputError,
    EmbeddingUnavailableError,
)
from app.embeddings.types import (
    ACCELERATOR_PREFERENCE,
    EmbeddingDevice,
    EmbeddingHealth,
    EmbeddingModelInfo,
    EmbeddingVector,
    ResolvedEmbeddingDevice,
)

logger = logging.getLogger("agent_context.embeddings")

_DIMENSION_PROBE_TEXT = "dimension probe"
_DIMENSION_ACCESSORS = ("get_embedding_dimension", "get_sentence_embedding_dimension")


class SentenceTransformerModel(Protocol):
    """The subset of the sentence-transformer API this provider depends on.

    Dimensions are read through the optional accessors named in
    `_DIMENSION_ACCESSORS` because backends have renamed them, and are probed
    from a real vector when no accessor is available.
    """

    def encode(
        self,
        inputs: list[str],
        /,
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> object:
        """Return one vector for each input text."""
        ...


class ModelLoader(Protocol):
    """Load a model for one configuration and already-resolved device."""

    def __call__(
        self,
        configuration: EmbeddingConfiguration,
        device: ResolvedEmbeddingDevice,
    ) -> SentenceTransformerModel:
        """Return a loaded local model."""
        ...


AcceleratorProbe = Callable[[], tuple[ResolvedEmbeddingDevice, ...]]


def available_accelerators() -> tuple[ResolvedEmbeddingDevice, ...]:
    """Report optional accelerators without requiring an accelerator backend."""
    available: list[ResolvedEmbeddingDevice] = []
    try:
        torch = import_module("torch")
        if bool(torch.cuda.is_available()):
            available.append("cuda")
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and bool(mps.is_available()):
            available.append("mps")
    except Exception:
        logger.debug("embedding_accelerator_probe_failed", exc_info=True)
        return ()
    return tuple(available)


def resolve_device(
    requested: EmbeddingDevice,
    probe: AcceleratorProbe = available_accelerators,
) -> ResolvedEmbeddingDevice:
    """Select CPU by default and only use an accelerator that really exists.

    `auto` falls back to CPU so the platform stays usable without a GPU. An
    explicitly requested accelerator fails loudly instead of silently running
    somewhere the operator did not ask for.
    """
    if requested == "cpu":
        return "cpu"
    available = probe()
    if requested == "auto":
        return next(
            (candidate for candidate in ACCELERATOR_PREFERENCE if candidate in available),
            "cpu",
        )
    if requested not in available:
        raise EmbeddingConfigurationError(
            f"EMBEDDING_DEVICE={requested} is not available on this host"
        )
    return requested


def load_sentence_transformer(
    configuration: EmbeddingConfiguration,
    device: ResolvedEmbeddingDevice,
) -> SentenceTransformerModel:
    """Load the configured model with the optional local sentence-transformer backend."""
    try:
        backend = import_module("sentence_transformers")
    except ImportError as error:
        raise EmbeddingUnavailableError("the local embedding backend is not installed") from error
    cache_directory = configuration.cache_directory
    try:
        model = backend.SentenceTransformer(
            configuration.model,
            device=device,
            cache_folder=None if cache_directory is None else str(cache_directory),
            local_files_only=not configuration.allow_downloads,
            trust_remote_code=False,
        )
    except Exception as error:
        raise EmbeddingUnavailableError(
            "the configured local embedding model could not be loaded"
        ) from error
    return cast(SentenceTransformerModel, model)


class LocalEmbeddingProvider:
    """Load one local model once and reuse it for every embedding request.

    Model loading and encoding are CPU-bound, so both run in worker threads and
    never block the event loop. Encoding is serialized because the underlying
    model is a single shared, non-thread-safe resource.
    """

    def __init__(
        self,
        configuration: EmbeddingConfiguration,
        *,
        loader: ModelLoader | None = None,
        accelerator_probe: AcceleratorProbe | None = None,
    ) -> None:
        self._configuration = configuration
        self._loader: ModelLoader = load_sentence_transformer if loader is None else loader
        self._probe: AcceleratorProbe = (
            available_accelerators if accelerator_probe is None else accelerator_probe
        )
        self._model: SentenceTransformerModel | None = None
        self._info: EmbeddingModelInfo | None = None
        self._load_lock = asyncio.Lock()
        self._encode_lock = asyncio.Lock()

    @property
    def configuration(self) -> EmbeddingConfiguration:
        """Return the immutable configuration this provider was built from."""
        return self._configuration

    async def load(self) -> EmbeddingModelInfo:
        """Load the model at most once, even under concurrent first requests."""
        cached = self._info
        if cached is not None:
            return cached
        async with self._load_lock:
            cached = self._info
            if cached is not None:
                return cached
            info = await self._load_model()
            self._info = info
            return info

    async def embed_text(self, text: str) -> EmbeddingVector:
        """Embed one text and return the vector with its model metadata."""
        return (await self.embed_documents([text]))[0]

    async def embed_documents(self, texts: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        """Embed many texts in configured batches, preserving input order."""
        normalized = self._normalized_texts(texts)
        info = await self.load()
        batch_size = self._configuration.batch_size
        vectors: list[EmbeddingVector] = []
        async with self._encode_lock:
            model = self._model
            if model is None:  # pragma: no cover - load() always assigns the model
                raise EmbeddingUnavailableError("the local embedding model is not loaded")
            for start in range(0, len(normalized), batch_size):
                batch = normalized[start : start + batch_size]
                rows = await self._encode(model, batch)
                if len(rows) != len(batch):
                    raise EmbeddingUnavailableError(
                        "the local embedding model returned an unexpected vector count"
                    )
                vectors.extend(
                    EmbeddingVector(
                        model=info.model,
                        dimensions=info.dimensions,
                        values=row,
                    )
                    for row in rows
                )
        return tuple(vectors)

    async def health(self) -> EmbeddingHealth:
        """Report model availability without raising or leaking internals."""
        model = self._configuration.model
        try:
            info = await self.load()
        except EmbeddingError as error:
            return EmbeddingHealth(available=False, model=model, detail=type(error).__name__)
        return EmbeddingHealth(
            available=True,
            model=info.model,
            dimensions=info.dimensions,
            device=info.device,
        )

    async def close(self) -> None:
        """Release the loaded model so shutdown can free its memory."""
        async with self._load_lock:
            if self._model is None and self._info is None:
                return
            self._model = None
            self._info = None
        logger.info(
            "embedding_model_unloaded",
            extra={"embedding_model": self._configuration.model},
        )

    async def _load_model(self) -> EmbeddingModelInfo:
        configuration = self._configuration
        device = resolve_device(configuration.device, self._probe)
        started_at = perf_counter()
        try:
            model = await asyncio.to_thread(self._loader, configuration, device)
            dimensions = await asyncio.to_thread(_discover_dimensions, model, configuration)
        except EmbeddingError:
            raise
        except Exception as error:
            raise EmbeddingUnavailableError(
                "the local embedding model could not be loaded"
            ) from error
        if dimensions != configuration.dimensions:
            raise EmbeddingDimensionError(
                f"the configured model produces {dimensions} dimensions but "
                f"EMBEDDING_DIMENSIONS is {configuration.dimensions}"
            )
        info = EmbeddingModelInfo(
            model=configuration.model,
            dimensions=dimensions,
            device=device,
            normalized=configuration.normalize,
            max_sequence_length=_max_sequence_length(model),
        )
        self._model = model
        logger.info(
            "embedding_model_loaded",
            extra={
                "embedding_model": info.model,
                "embedding_dimensions": info.dimensions,
                "embedding_device": info.device,
                "duration_ms": round((perf_counter() - started_at) * 1_000, 3),
            },
        )
        return info

    async def _encode(
        self,
        model: SentenceTransformerModel,
        batch: list[str],
    ) -> tuple[tuple[float, ...], ...]:
        configuration = self._configuration
        last_error: Exception | None = None
        for attempt in range(1, configuration.max_attempts + 1):
            try:
                raw = await asyncio.to_thread(
                    model.encode,
                    batch,
                    batch_size=configuration.batch_size,
                    normalize_embeddings=configuration.normalize,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
            except Exception as error:
                last_error = error
            else:
                return _coerce_matrix(raw)
            if attempt < configuration.max_attempts:
                logger.warning(
                    "embedding_encode_retry",
                    extra={
                        "embedding_model": configuration.model,
                        "embedding_error": type(last_error).__name__,
                    },
                )
                await asyncio.sleep(configuration.retry_backoff_seconds * 2 ** (attempt - 1))
        logger.error(
            "embedding_encode_failed",
            extra={
                "embedding_model": configuration.model,
                "embedding_error": type(last_error).__name__,
            },
        )
        raise EmbeddingUnavailableError(
            "the local embedding model failed to encode a batch"
        ) from last_error

    def _normalized_texts(self, texts: Sequence[str]) -> list[str]:
        configuration = self._configuration
        if isinstance(texts, str):
            raise EmbeddingInputError("embedding input must be a sequence of texts")
        items = list(texts)
        if not items:
            raise EmbeddingInputError("at least one text is required")
        if len(items) > configuration.max_batch_texts:
            raise EmbeddingInputError(
                f"at most {configuration.max_batch_texts} texts can be embedded per call"
            )
        normalized: list[str] = []
        for item in items:
            if not isinstance(item, str):
                raise EmbeddingInputError("embedding input must be text")
            text = item.strip()
            if not text:
                raise EmbeddingInputError("embedding input must not be empty")
            if len(text) > configuration.max_text_characters:
                raise EmbeddingInputError(
                    "embedding input exceeds the configured maximum text length"
                )
            normalized.append(text)
        return normalized


def _declared_dimensions(model: SentenceTransformerModel) -> int | None:
    """Read declared dimensions from whichever accessor the backend exposes."""
    for name in _DIMENSION_ACCESSORS:
        accessor = getattr(model, name, None)
        if not callable(accessor):
            continue
        value = accessor()
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _discover_dimensions(
    model: SentenceTransformerModel,
    configuration: EmbeddingConfiguration,
) -> int:
    """Read the model's real output dimensions, probing it when undeclared."""
    declared = _declared_dimensions(model)
    if declared is not None:
        return declared
    rows = _coerce_matrix(
        model.encode(
            [_DIMENSION_PROBE_TEXT],
            batch_size=1,
            normalize_embeddings=configuration.normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
    )
    if len(rows) != 1 or not rows[0]:
        raise EmbeddingUnavailableError("the local embedding model returned no probe vector")
    return len(rows[0])


def _max_sequence_length(model: SentenceTransformerModel) -> int | None:
    """Report the model's input limit when the backend exposes one."""
    value = getattr(model, "max_seq_length", None)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _coerce_matrix(raw: object) -> tuple[tuple[float, ...], ...]:
    """Convert a backend-specific result into plain finite float rows."""
    if isinstance(raw, str) or not isinstance(raw, Iterable):
        raise EmbeddingUnavailableError("the local embedding model returned an unsupported value")
    rows: list[tuple[float, ...]] = []
    try:
        for row in raw:
            if isinstance(row, str) or not isinstance(row, Iterable):
                raise EmbeddingUnavailableError(
                    "the local embedding model returned an unsupported vector"
                )
            rows.append(tuple(float(value) for value in row))
    except (TypeError, ValueError) as error:
        raise EmbeddingUnavailableError(
            "the local embedding model returned non-numeric values"
        ) from error
    return tuple(rows)
