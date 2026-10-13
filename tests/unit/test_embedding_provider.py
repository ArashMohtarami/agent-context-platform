"""Local embedding provider tests using deterministic stub models."""

import asyncio
import threading

import pytest
from embedding_doubles import (
    RecordingLoader,
    RenamedAccessorModel,
    StubEmbeddingModel,
    configuration,
    expected_values,
)

from app.embeddings.base import EmbeddingProvider
from app.embeddings.errors import (
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    EmbeddingInputError,
    EmbeddingUnavailableError,
)
from app.embeddings.local import (
    LocalEmbeddingProvider,
    load_sentence_transformer,
    resolve_device,
)
from app.embeddings.types import ResolvedEmbeddingDevice


def build_provider(
    model: StubEmbeddingModel | None = None,
    *,
    accelerators: tuple[ResolvedEmbeddingDevice, ...] = (),
    **overrides: object,
) -> tuple[LocalEmbeddingProvider, RecordingLoader]:
    """Build a provider backed by a recording loader and stub model."""
    loader = RecordingLoader(model)
    provider = LocalEmbeddingProvider(
        configuration(**overrides),
        loader=loader,
        accelerator_probe=lambda: accelerators,
    )
    return provider, loader


def test_local_provider_implements_the_embedding_provider_interface() -> None:
    provider, _ = build_provider()

    assert isinstance(provider, EmbeddingProvider)
    assert provider.configuration == configuration()


@pytest.mark.asyncio
async def test_the_model_is_loaded_once_and_reused_by_concurrent_callers() -> None:
    provider, loader = build_provider()

    results = await asyncio.gather(*(provider.embed_text(f"text {index}") for index in range(5)))

    assert len(loader.calls) == 1
    assert len({vector.model for vector in results}) == 1
    assert len(await provider.embed_documents(["another"])) == 1
    assert len(loader.calls) == 1


@pytest.mark.asyncio
async def test_dimensions_are_discovered_when_the_model_does_not_declare_them() -> None:
    model = StubEmbeddingModel(dimensions=4, declares_dimensions=False)
    provider, _ = build_provider(model)

    info = await provider.load()

    assert info.dimensions == 4
    assert model.batches == [["dimension probe"]]


@pytest.mark.asyncio
async def test_dimensions_are_read_from_a_renamed_backend_accessor() -> None:
    model = RenamedAccessorModel(dimensions=4)
    provider, _ = build_provider(model)

    info = await provider.load()

    assert info.dimensions == 4
    assert model.batches == []


@pytest.mark.asyncio
async def test_a_model_with_other_dimensions_is_refused() -> None:
    provider, _ = build_provider(StubEmbeddingModel(dimensions=8), dimensions=4)

    with pytest.raises(EmbeddingDimensionError, match="8 dimensions"):
        await provider.load()

    health = await provider.health()
    assert health.available is False
    assert health.detail == "EmbeddingDimensionError"
    assert health.dimensions is None


@pytest.mark.asyncio
async def test_a_vector_of_the_wrong_length_is_refused() -> None:
    provider, _ = build_provider(StubEmbeddingModel(dimensions=4, row_length=3))

    with pytest.raises(EmbeddingDimensionError):
        await provider.embed_text("payment idempotency")


@pytest.mark.asyncio
async def test_documents_are_embedded_in_configured_batches_in_order() -> None:
    model = StubEmbeddingModel()
    provider, _ = build_provider(model, batch_size=2)
    texts = [f"chunk {index}" for index in range(5)]

    vectors = await provider.embed_documents(texts)

    assert model.batches == [texts[0:2], texts[2:4], texts[4:5]]
    assert model.batch_sizes == [2, 2, 2]
    assert [vector.values for vector in vectors] == [expected_values(text, 4) for text in texts]


@pytest.mark.asyncio
async def test_vectors_carry_the_configured_model_metadata() -> None:
    provider, _ = build_provider()

    vector = await provider.embed_text("payment idempotency")

    assert vector.model == "stub-embedding-model"
    assert vector.dimensions == 4
    assert len(vector.values) == 4
    assert vector == await provider.embed_text("payment idempotency")


@pytest.mark.asyncio
async def test_the_normalization_setting_reaches_the_model() -> None:
    model = StubEmbeddingModel()
    provider, _ = build_provider(model, normalize=False)

    await provider.embed_text("payment idempotency")

    assert model.normalize_flags == [False]


@pytest.mark.asyncio
async def test_embedding_work_never_runs_on_the_event_loop_thread() -> None:
    model = StubEmbeddingModel()
    provider, _ = build_provider(model)

    await provider.embed_documents(["first", "second"])

    assert model.threads
    assert threading.get_ident() not in model.threads


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "texts",
    [
        [],
        ["  "],
        ["valid", ""],
        ["x" * 201],
        ["text"] * 9,
    ],
)
async def test_input_outside_the_configured_limits_is_rejected(texts: list[str]) -> None:
    provider, loader = build_provider()

    with pytest.raises(EmbeddingInputError):
        await provider.embed_documents(texts)

    assert loader.calls == []


@pytest.mark.asyncio
async def test_a_single_string_is_not_treated_as_a_batch_of_characters() -> None:
    provider, _ = build_provider()

    with pytest.raises(EmbeddingInputError):
        await provider.embed_documents("payment")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_transient_encoding_failures_are_retried() -> None:
    model = StubEmbeddingModel(failures=2)
    provider, _ = build_provider(model, max_attempts=3)

    vector = await provider.embed_text("payment idempotency")

    assert vector.values == expected_values("payment idempotency", 4)
    assert len(model.batches) == 3


@pytest.mark.asyncio
async def test_exhausted_retries_report_an_unavailable_model() -> None:
    model = StubEmbeddingModel(failures=5)
    provider, _ = build_provider(model, max_attempts=2)

    with pytest.raises(EmbeddingUnavailableError):
        await provider.embed_text("payment idempotency")

    assert len(model.batches) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [object(), "vector", [object()], [[object()]]])
async def test_unusable_backend_output_is_rejected(result: object) -> None:
    provider, _ = build_provider(StubEmbeddingModel(result=result), max_attempts=1)

    with pytest.raises(EmbeddingUnavailableError):
        await provider.embed_text("payment idempotency")


@pytest.mark.asyncio
async def test_health_reports_the_loaded_model_details() -> None:
    provider, _ = build_provider()

    health = await provider.health()

    assert health.available is True
    assert (health.model, health.dimensions, health.device) == ("stub-embedding-model", 4, "cpu")
    assert health.detail is None


@pytest.mark.asyncio
async def test_health_never_raises_when_the_model_cannot_load() -> None:
    provider = LocalEmbeddingProvider(
        configuration(),
        loader=RecordingLoader(error=OSError("missing weights")),
        accelerator_probe=lambda: (),
    )

    health = await provider.health()

    assert health.available is False
    assert health.detail == "EmbeddingUnavailableError"
    assert health.model == "stub-embedding-model"


@pytest.mark.asyncio
async def test_closing_unloads_the_model_and_allows_a_later_reload() -> None:
    provider, loader = build_provider()
    await provider.load()

    await provider.close()
    await provider.close()
    await provider.load()

    assert len(loader.calls) == 2


@pytest.mark.asyncio
async def test_an_absent_local_backend_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr("app.embeddings.local.import_module", missing)

    with pytest.raises(EmbeddingUnavailableError, match="backend is not installed"):
        load_sentence_transformer(configuration(), "cpu")


@pytest.mark.parametrize(
    ("requested", "accelerators", "expected"),
    [
        ("auto", (), "cpu"),
        ("auto", ("cuda",), "cuda"),
        ("auto", ("mps",), "mps"),
        ("auto", ("mps", "cuda"), "cuda"),
        ("cpu", ("cuda",), "cpu"),
        ("cuda", ("cuda",), "cuda"),
    ],
)
def test_device_resolution_prefers_cpu_unless_an_accelerator_exists(
    requested: str,
    accelerators: tuple[str, ...],
    expected: str,
) -> None:
    assert resolve_device(requested, lambda: accelerators) == expected  # type: ignore[arg-type]


def test_an_unavailable_requested_accelerator_fails_instead_of_falling_back() -> None:
    with pytest.raises(EmbeddingConfigurationError, match="EMBEDDING_DEVICE=cuda"):
        resolve_device("cuda", lambda: ())


def test_accelerator_probing_survives_a_missing_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.embeddings.local import available_accelerators

    def missing(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr("app.embeddings.local.import_module", missing)

    assert available_accelerators() == ()


@pytest.mark.asyncio
async def test_the_provider_runs_on_cpu_without_an_accelerator() -> None:
    provider, loader = build_provider(accelerators=())

    info = await provider.load()

    assert info.device == "cpu"
    assert loader.calls[0][1] == "cpu"
    assert info.max_sequence_length == 128
    assert info.normalized is True


@pytest.mark.asyncio
async def test_an_available_accelerator_is_used_when_requested_automatically() -> None:
    provider, loader = build_provider(accelerators=("cuda",), device="auto")

    info = await provider.load()

    assert info.device == "cuda"
    assert loader.calls[0][1] == "cuda"
