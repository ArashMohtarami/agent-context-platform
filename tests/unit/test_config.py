"""Configuration validation tests."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import McpSettings, Settings
from app.retrieval.ranking import RankingWeights


def test_mongodb_configuration_reads_password_file(tmp_path: Path) -> None:
    password_file = tmp_path / "password"
    password_file.write_text("file-secret\n", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        mongodb_uri="mongodb://mongodb:27017",
        mongodb_database="context",
        mongodb_username="context_user",
        mongodb_password_file=password_file,
        log_level="debug",  # type: ignore[arg-type]
    )

    assert settings.mongodb_password_value() == "file-secret"
    assert settings.mongodb_replica_set == "rs0"
    assert settings.log_level == "DEBUG"


def test_mongodb_configuration_accepts_secret_environment_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MONGODB_PASSWORD_FILE", raising=False)
    monkeypatch.delenv("MONGODB_PASSWORD", raising=False)
    settings = Settings(
        _env_file=None,
        mongodb_uri="mongodb://mongodb:27017",
        mongodb_database="context",
        mongodb_username="context_user",
        mongodb_password=SecretStr("environment-secret"),
    )

    assert settings.mongodb_password_value() == "environment-secret"
    assert "environment-secret" not in repr(settings)


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({}, "configure exactly one"),
        (
            {
                "mongodb_password": SecretStr("secret"),
                "mongodb_password_file": Path("secret-file"),
            },
            "configure exactly one",
        ),
        (
            {"mongodb_uri": "mongodb://user:password@mongodb:27017"},
            "must not contain credentials",
        ),
        ({"mongodb_database": "invalid/name"}, "invalid character"),
        ({"log_level": "verbose"}, "LOG_LEVEL must be"),
    ],
)
def test_mongodb_configuration_rejects_insecure_or_invalid_values(
    overrides: dict[str, object],
    expected_message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MONGODB_PASSWORD_FILE", raising=False)
    monkeypatch.delenv("MONGODB_PASSWORD", raising=False)
    values: dict[str, object] = {
        "_env_file": None,
        "mongodb_uri": "mongodb://mongodb:27017",
        "mongodb_database": "context",
        "mongodb_username": "context_user",
    }
    values.update(overrides)

    with pytest.raises(ValidationError, match=expected_message):
        Settings(**values)  # type: ignore[arg-type]


def test_empty_password_file_is_rejected(tmp_path: Path) -> None:
    password_file = tmp_path / "password"
    password_file.touch()
    settings = Settings(
        _env_file=None,
        mongodb_uri="mongodb://mongodb:27017",
        mongodb_database="context",
        mongodb_username="context_user",
        mongodb_password_file=password_file,
    )

    with pytest.raises(ValueError, match="must not be empty"):
        settings.mongodb_password_value()


def test_skill_import_configuration_has_bounded_section_aware_defaults(tmp_path: Path) -> None:
    password_file = tmp_path / "password"
    password_file.write_text("test-secret", encoding="utf-8")

    settings = Settings(
        _env_file=None,
        mongodb_uri="mongodb://mongodb:27017",
        mongodb_database="context",
        mongodb_username="context_user",
        mongodb_password_file=password_file,
    )

    assert settings.skills_directory == Path("skills")
    assert settings.skill_max_file_bytes == 1_000_000
    assert (
        settings.skill_chunk_min_tokens,
        settings.skill_chunk_target_tokens,
        settings.skill_chunk_max_tokens,
        settings.skill_chunk_overlap_tokens,
    ) == (500, 650, 800, 75)


@pytest.mark.parametrize(
    "chunk_values",
    [
        {"skill_chunk_min_tokens": 700, "skill_chunk_target_tokens": 600},
        {"skill_chunk_target_tokens": 900, "skill_chunk_max_tokens": 800},
        {"skill_chunk_min_tokens": 100, "skill_chunk_overlap_tokens": 100},
    ],
)
def test_skill_chunk_configuration_rejects_incoherent_sizes(
    chunk_values: dict[str, int],
    tmp_path: Path,
) -> None:
    password_file = tmp_path / "password"
    password_file.write_text("test-secret", encoding="utf-8")
    values: dict[str, object] = {
        "_env_file": None,
        "mongodb_uri": "mongodb://mongodb:27017",
        "mongodb_database": "context",
        "mongodb_username": "context_user",
        "mongodb_password_file": password_file,
        **chunk_values,
    }

    with pytest.raises(ValidationError):
        Settings(**values)  # type: ignore[arg-type]


def build_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Build valid settings with optional embedding overrides."""
    password_file = tmp_path / "password"
    password_file.write_text("test-secret", encoding="utf-8")
    values: dict[str, object] = {
        "_env_file": None,
        "mongodb_uri": "mongodb://mongodb:27017",
        "mongodb_database": "context",
        "mongodb_username": "context_user",
        "mongodb_password_file": password_file,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_mongodb_pool_rejects_unmaintainable_minimum(tmp_path: Path) -> None:
    assert build_settings(tmp_path).mongodb_min_pool_size == 0
    assert build_settings(tmp_path, mongodb_min_pool_size=20).mongodb_min_pool_size == 20
    with pytest.raises(ValidationError, match="MONGODB_MIN_POOL_SIZE must not exceed"):
        build_settings(tmp_path, mongodb_min_pool_size=20, mongodb_max_pool_size=10)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"app_env": "production"},
            "MCP_REMOTE_ALLOW_LOOPBACK_HTTP must be false in production",
        ),
        (
            {"api_proxy_headers": True},
            "API_FORWARDED_ALLOW_IPS is required",
        ),
        (
            {"api_proxy_headers": True, "api_forwarded_allow_ips": "*"},
            "must name explicit proxy IP ranges",
        ),
        (
            {"api_forwarded_allow_ips": "10.0.0.0/24"},
            "requires API_PROXY_HEADERS=true",
        ),
    ],
)
def test_remote_mcp_transport_and_proxy_settings_fail_closed(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        build_settings(tmp_path, **overrides)


def test_production_remote_mcp_accepts_only_explicit_trusted_proxy_configuration(
    tmp_path: Path,
) -> None:
    settings = build_settings(
        tmp_path,
        app_env="production",
        mcp_remote_allow_loopback_http=False,
        api_proxy_headers=True,
        api_forwarded_allow_ips="10.42.0.0/24,2001:db8::1",
    )

    assert settings.api_proxy_headers is True
    assert settings.api_forwarded_allow_ips == "10.42.0.0/24,2001:db8::1"


def test_embedding_configuration_defaults_to_a_local_cpu_capable_model(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    assert settings.embedding_enabled is True
    assert settings.embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
    assert settings.embedding_dimensions == 384
    assert settings.embedding_device == "auto"
    assert settings.embedding_batch_size == 32
    assert settings.embedding_max_batch_texts == 256
    assert settings.embedding_max_text_characters == 20_000
    assert settings.embedding_normalize is True
    assert settings.embedding_max_attempts == 3
    assert settings.embedding_retry_backoff_seconds == 0.5
    assert settings.embedding_model_cache_directory is None
    assert settings.embedding_allow_downloads is True


def test_embedding_configuration_is_environment_driven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL", "/models/offline-model")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "768")
    monkeypatch.setenv("EMBEDDING_DEVICE", "cpu")
    monkeypatch.setenv("EMBEDDING_ENABLED", "false")
    monkeypatch.setenv("EMBEDDING_ALLOW_DOWNLOADS", "false")

    settings = build_settings(tmp_path)

    assert settings.embedding_model == "/models/offline-model"
    assert settings.embedding_dimensions == 768
    assert settings.embedding_device == "cpu"
    assert settings.embedding_enabled is False
    assert settings.embedding_allow_downloads is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"embedding_model": ""},
        {"embedding_model": " padded "},
        {"embedding_model": "model name"},
        {"embedding_model": "m" * 257},
        {"embedding_dimensions": 0},
        {"embedding_dimensions": 4_097},
        {"embedding_device": "tpu"},
        {"embedding_batch_size": 0},
        {"embedding_batch_size": 513},
        {"embedding_max_batch_texts": 0},
        {"embedding_max_text_characters": 0},
        {"embedding_max_attempts": 0},
        {"embedding_max_attempts": 11},
        {"embedding_retry_backoff_seconds": -0.5},
        {"embedding_retry_backoff_seconds": 30.5},
        {"embedding_batch_size": 64, "embedding_max_batch_texts": 32},
    ],
)
def test_invalid_embedding_configuration_is_rejected(
    overrides: dict[str, object],
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError):
        build_settings(tmp_path, **overrides)


def test_vector_search_configuration_has_bounded_defaults(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    assert settings.vector_search_candidate_multiplier == 10
    assert settings.vector_search_max_candidates == 1_000


@pytest.mark.parametrize(
    "overrides",
    [
        {"vector_search_candidate_multiplier": 0},
        {"vector_search_candidate_multiplier": 101},
        {"vector_search_max_candidates": 0},
        {"vector_search_max_candidates": 5_001},
    ],
)
def test_invalid_vector_search_configuration_is_rejected(
    overrides: dict[str, object],
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError):
        build_settings(tmp_path, **overrides)


def test_hybrid_retrieval_configuration_matches_the_documented_formula(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)

    assert settings.ranking_weights() == RankingWeights(
        vector=0.45,
        lexical=0.25,
        importance=0.15,
        recency=0.10,
        source_priority=0.05,
    )
    assert settings.retrieval_recency_half_life_days == 90.0
    assert settings.retrieval_duplicate_overlap == 0.85
    assert settings.retrieval_token_budget == 4_000
    assert settings.retrieval_max_token_budget == 16_000
    assert settings.research_retrieval_token_budget == 8_000
    assert settings.research_retrieval_max_token_budget == 16_000


def test_storing_search_queries_is_off_until_a_deployment_asks_for_it(tmp_path: Path) -> None:
    default = build_settings(tmp_path)
    opted_in = build_settings(tmp_path, retrieval_log_enabled=True)

    # The structured log always reports a search; storing the query text with
    # it is a retention decision, so it is never the default.
    assert default.retrieval_log_enabled is False
    assert opted_in.retrieval_log_enabled is True


def test_ranking_weights_are_configurable(tmp_path: Path) -> None:
    settings = build_settings(
        tmp_path,
        retrieval_weight_vector=0.6,
        retrieval_weight_lexical=0.4,
        retrieval_weight_importance=0.0,
        retrieval_weight_recency=0.0,
        retrieval_weight_source_priority=0.0,
    )

    assert settings.ranking_weights().vector == 0.6
    assert settings.ranking_weights().normalized().vector == pytest.approx(0.6)


@pytest.mark.parametrize(
    "overrides",
    [
        {"retrieval_weight_vector": -0.1},
        {"retrieval_weight_lexical": 1.1},
        {
            "retrieval_weight_vector": 0.0,
            "retrieval_weight_lexical": 0.0,
            "retrieval_weight_importance": 0.0,
            "retrieval_weight_recency": 0.0,
            "retrieval_weight_source_priority": 0.0,
        },
        {"retrieval_recency_half_life_days": 0.0},
        {"retrieval_duplicate_overlap": 0.0},
        {"retrieval_duplicate_overlap": 1.5},
        {"retrieval_token_budget": 99},
        {"retrieval_token_budget": 16_001},
        {"retrieval_max_token_budget": 99},
        {"research_retrieval_token_budget": 99},
        {"research_retrieval_token_budget": 16_001},
        {"research_retrieval_max_token_budget": 99},
        {"research_retrieval_token_budget": 8_000, "research_retrieval_max_token_budget": 4_000},
        {"retrieval_token_budget": 8_000, "retrieval_max_token_budget": 4_000},
    ],
)
def test_invalid_hybrid_retrieval_configuration_is_rejected(
    overrides: dict[str, object],
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError):
        build_settings(tmp_path, **overrides)


def test_the_mcp_settings_load_without_any_database_configuration() -> None:
    settings = McpSettings(_env_file=None)

    # The MCP process must be able to start in a container that was handed no
    # MongoDB credentials at all, rather than unused ones it is trusted not to
    # use.
    assert settings.mcp_api_base_url == "http://127.0.0.1:8000"
    assert "mongodb_uri" not in McpSettings.model_fields
    assert settings.mcp_api_token_value() is None


def test_the_full_settings_still_carry_every_mcp_value(tmp_path: Path) -> None:
    settings = build_settings(
        tmp_path,
        mcp_scopes="search:read",
        mcp_profile_ids="personal-brand",
    )

    # One configuration surface: the API process reads MCP values from the same
    # place a deployment sets them.
    assert isinstance(settings, McpSettings)
    assert settings.mcp_authorization().scopes == ("search:read",)
    assert settings.mcp_authorization().profiles == ("personal-brand",)


def test_metrics_are_published_unless_a_deployment_says_otherwise(tmp_path: Path) -> None:
    # A deployment nobody can measure is one whose first sign of trouble is a
    # person noticing, so the default is on and switching it off is a choice.
    assert build_settings(tmp_path).metrics_enabled is True
    assert build_settings(tmp_path, metrics_enabled=False).metrics_enabled is False


def test_the_mcp_process_is_not_configured_with_metrics(tmp_path: Path) -> None:
    # It serves stdio and opens no port, so there is nothing for the setting to
    # govern there: its numbers reach an operator as log events instead.
    assert "metrics_enabled" not in McpSettings.model_fields
