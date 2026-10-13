"""Embedding service tests for active-model enforcement and composition."""

from collections.abc import Sequence
from dataclasses import replace

import pytest
from embedding_doubles import configuration

from app.core.config import Settings
from app.embeddings.base import EmbeddingProvider
from app.embeddings.config import EmbeddingConfiguration
from app.embeddings.errors import EmbeddingCompatibilityError, EmbeddingInputError
from app.embeddings.types import EmbeddingHealth, EmbeddingModelInfo, EmbeddingVector
from app.services.embedding_service import EmbeddingService, build_embedding_service


class FakeProvider:
    """A provider whose vectors and failures are controlled by the test."""

    def __init__(
        self,
        *,
        vectors: tuple[EmbeddingVector, ...] | None = None,
        model: str = "stub-embedding-model",
        dimensions: int = 4,
    ) -> None:
        self._configuration = configuration(model=model, dimensions=dimensions)
        self._vectors = vectors
        self.calls: list[list[str]] = []
        self.loaded = 0
        self.closed = 0

    @property
    def configuration(self) -> EmbeddingConfiguration:
        return self._configuration

    async def load(self) -> EmbeddingModelInfo:
        self.loaded += 1
        return EmbeddingModelInfo(
            model=self._configuration.model,
            dimensions=self._configuration.dimensions,
            device="cpu",
            normalized=True,
        )

    def _vector(self, text: str) -> EmbeddingVector:
        return EmbeddingVector(
            model=self._configuration.model,
            dimensions=self._configuration.dimensions,
            values=tuple(float(len(text)) for _ in range(self._configuration.dimensions)),
        )

    async def embed_text(self, text: str) -> EmbeddingVector:
        return (await self.embed_documents([text]))[0]

    async def embed_documents(self, texts: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        self.calls.append(list(texts))
        if self._vectors is not None:
            return self._vectors
        return tuple(self._vector(text) for text in texts)

    async def health(self) -> EmbeddingHealth:
        return EmbeddingHealth(
            available=True,
            model=self._configuration.model,
            dimensions=self._configuration.dimensions,
            device="cpu",
        )

    async def close(self) -> None:
        self.closed += 1


def test_the_fake_provider_matches_the_provider_interface() -> None:
    assert isinstance(FakeProvider(), EmbeddingProvider)


@pytest.mark.asyncio
async def test_chunk_embedding_preserves_order_and_active_model_metadata() -> None:
    service = EmbeddingService(FakeProvider())

    vectors = await service.embed_chunks(["one", "three", "seven!!"])

    assert [vector.values[0] for vector in vectors] == [3.0, 5.0, 7.0]
    assert {vector.model for vector in vectors} == {"stub-embedding-model"}
    assert service.model == "stub-embedding-model"
    assert service.dimensions == 4


@pytest.mark.asyncio
async def test_a_query_is_embedded_with_the_active_model() -> None:
    service = EmbeddingService(FakeProvider())

    vector = await service.embed_query("payment idempotency")

    assert vector.dimensions == 4
    assert vector.model == "stub-embedding-model"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "foreign",
    [
        EmbeddingVector(model="other-model", dimensions=4, values=(0.1, 0.2, 0.3, 0.4)),
        EmbeddingVector(model="stub-embedding-model", dimensions=2, values=(0.1, 0.2)),
    ],
)
async def test_vectors_from_another_model_or_dimension_are_refused(
    foreign: EmbeddingVector,
) -> None:
    service = EmbeddingService(FakeProvider(vectors=(foreign,)))

    with pytest.raises(EmbeddingCompatibilityError):
        await service.embed_chunks(["one"])
    with pytest.raises(EmbeddingCompatibilityError):
        service.verify(foreign)


@pytest.mark.asyncio
async def test_a_provider_returning_the_wrong_number_of_vectors_is_refused() -> None:
    single = EmbeddingVector(
        model="stub-embedding-model",
        dimensions=4,
        values=(0.1, 0.2, 0.3, 0.4),
    )
    service = EmbeddingService(FakeProvider(vectors=(single,)))

    with pytest.raises(EmbeddingCompatibilityError, match="different number of vectors"):
        await service.embed_chunks(["one", "two"])


@pytest.mark.asyncio
async def test_start_and_close_reach_the_provider() -> None:
    provider = FakeProvider()
    service = EmbeddingService(provider)

    info = await service.start()
    health = await service.health()
    await service.close()

    assert (info.model, info.dimensions, info.device) == ("stub-embedding-model", 4, "cpu")
    assert health.available is True
    assert (provider.loaded, provider.closed) == (1, 1)


def test_no_service_is_built_when_embeddings_are_disabled(settings: Settings) -> None:
    assert build_embedding_service(settings.model_copy(update={"embedding_enabled": False})) is None


def test_the_configured_local_provider_is_built_from_settings(settings: Settings) -> None:
    configured = settings.model_copy(
        update={"embedding_model": "local/test-model", "embedding_dimensions": 16}
    )

    service = build_embedding_service(configured)

    assert service is not None
    assert (service.model, service.dimensions) == ("local/test-model", 16)


def test_the_service_only_accepts_the_configured_dimensions() -> None:
    provider = FakeProvider()
    service = EmbeddingService(provider)
    vector = EmbeddingVector(model="stub-embedding-model", dimensions=4, values=(1.0,) * 4)

    assert service.verify(vector) is vector

    provider._configuration = replace(provider.configuration, dimensions=8)
    with pytest.raises(EmbeddingCompatibilityError, match="dimensions"):
        service.verify(vector)


@pytest.mark.asyncio
async def test_more_chunks_than_the_per_call_limit_are_embedded_in_windows() -> None:
    provider = FakeProvider()
    service = EmbeddingService(provider)
    contents = [f"{'x' * (index + 1)}" for index in range(20)]

    vectors = await service.embed_chunks(contents)

    assert provider.configuration.max_batch_texts == 8
    assert [len(call) for call in provider.calls] == [8, 8, 4]
    assert [vector.values[0] for vector in vectors] == [float(len(text)) for text in contents]


@pytest.mark.asyncio
async def test_embedding_no_chunks_is_rejected() -> None:
    service = EmbeddingService(FakeProvider())

    with pytest.raises(EmbeddingInputError):
        await service.embed_chunks([])
