"""Embedding configuration tests covering determinism and invalid values."""

from pathlib import Path

import pytest
from embedding_doubles import configuration

from app.core.config import Settings
from app.embeddings.config import EmbeddingConfiguration
from app.embeddings.errors import EmbeddingConfigurationError


def test_configuration_is_deterministic_for_one_environment(settings: Settings) -> None:
    first = EmbeddingConfiguration.from_settings(settings)
    second = EmbeddingConfiguration.from_settings(settings)

    assert first == second
    assert hash(first) == hash(second)
    assert (first.model, first.dimensions) == (
        settings.embedding_model,
        settings.embedding_dimensions,
    )


def test_configuration_maps_every_environment_driven_value(tmp_path: Path) -> None:
    password_file = tmp_path / "password"
    password_file.write_text("test-secret", encoding="utf-8")
    cache = tmp_path / "models"
    settings = Settings(
        _env_file=None,
        mongodb_uri="mongodb://mongodb:27017",
        mongodb_database="context",
        mongodb_username="context_user",
        mongodb_password_file=password_file,
        embedding_model="/models/local-model",
        embedding_dimensions=768,
        embedding_device="cpu",
        embedding_batch_size=8,
        embedding_max_batch_texts=64,
        embedding_max_text_characters=1_000,
        embedding_normalize=False,
        embedding_max_attempts=2,
        embedding_retry_backoff_seconds=1.5,
        embedding_model_cache_directory=cache,
        embedding_allow_downloads=False,
    )

    built = EmbeddingConfiguration.from_settings(settings)

    assert built == EmbeddingConfiguration(
        model="/models/local-model",
        dimensions=768,
        device="cpu",
        batch_size=8,
        max_batch_texts=64,
        max_text_characters=1_000,
        normalize=False,
        max_attempts=2,
        retry_backoff_seconds=1.5,
        cache_directory=cache,
        allow_downloads=False,
    )


def test_defaults_run_on_cpu_capable_hardware(settings: Settings) -> None:
    built = EmbeddingConfiguration.from_settings(settings)

    assert built.device == "auto"
    assert built.normalize is True
    assert built.batch_size <= built.max_batch_texts


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"model": ""}, "EMBEDDING_MODEL"),
        ({"model": " padded-model "}, "EMBEDDING_MODEL"),
        ({"model": "model\nname"}, "EMBEDDING_MODEL"),
        ({"model": "m" * 257}, "EMBEDDING_MODEL"),
        ({"dimensions": 0}, "EMBEDDING_DIMENSIONS"),
        ({"dimensions": 4_097}, "EMBEDDING_DIMENSIONS"),
        ({"dimensions": True}, "EMBEDDING_DIMENSIONS"),
        ({"device": "tpu"}, "EMBEDDING_DEVICE"),
        ({"batch_size": 0}, "EMBEDDING_BATCH_SIZE"),
        ({"batch_size": 9}, "EMBEDDING_BATCH_SIZE"),
        ({"max_batch_texts": 0}, "EMBEDDING_MAX_BATCH_TEXTS"),
        ({"max_text_characters": 0}, "EMBEDDING_MAX_TEXT_CHARACTERS"),
        ({"max_attempts": 0}, "EMBEDDING_MAX_ATTEMPTS"),
        ({"max_attempts": 11}, "EMBEDDING_MAX_ATTEMPTS"),
        ({"retry_backoff_seconds": -1.0}, "EMBEDDING_RETRY_BACKOFF_SECONDS"),
        ({"retry_backoff_seconds": 31.0}, "EMBEDDING_RETRY_BACKOFF_SECONDS"),
        ({"normalize": "yes"}, "boolean"),
        ({"allow_downloads": 1}, "boolean"),
    ],
)
def test_invalid_configuration_is_rejected(
    overrides: dict[str, object],
    expected_message: str,
) -> None:
    with pytest.raises(EmbeddingConfigurationError, match=expected_message):
        configuration(**overrides)
