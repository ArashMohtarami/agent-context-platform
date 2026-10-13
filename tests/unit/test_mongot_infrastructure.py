"""Static checks for the self-managed mongot runtime configuration."""

from pathlib import Path

import yaml


def rendered_mongot_config() -> dict[str, object]:
    """Render non-secret test values into the committed runtime template."""
    template = Path("docker/mongot/mongot.yml.template").read_text(encoding="utf-8")
    rendered = template.replace("__MONGOT_SYNC_USERNAME__", "test_sync_user").replace(
        "__MONGOT_LOG_LEVEL__", "INFO"
    )
    value = yaml.safe_load(rendered)
    assert isinstance(value, dict)
    return value


def test_mongot_uses_replica_set_scram_and_password_file() -> None:
    config = rendered_mongot_config()
    sync_source = config["syncSource"]
    assert isinstance(sync_source, dict)
    replica_set = sync_source["replicaSet"]
    assert replica_set == {
        "hostAndPort": "mongodb:27017",
        "scramAuth": {
            "username": "test_sync_user",
            # The container entrypoint creates this owner-only ephemeral file.
            "passwordFile": "/tmp/mongot-sync-password",  # noqa: S108
            "authSource": "admin",
            "tls": {"enabled": False},
        },
    }
    assert sync_source["replicationReader"] == {"readPreference": "primaryPreferred"}


def test_mongot_persists_data_and_serves_private_container_endpoints() -> None:
    config = rendered_mongot_config()

    assert config["storage"] == {"dataPath": "/data/mongot"}
    assert config["server"] == {"grpc": {"address": "0.0.0.0:27028", "tls": {"mode": "disabled"}}}
    assert config["healthCheck"] == {"address": "0.0.0.0:8080"}
    assert config["metrics"] == {"enabled": True, "address": "0.0.0.0:9946"}


def test_phase_2_configuration_defines_no_search_or_vector_indexes() -> None:
    repository_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            Path("docker-compose.yml"),
            Path("docker/mongot/mongot.yml.template"),
            Path("scripts/create-mongot-user.js"),
        ]
    )

    assert "createSearchIndex" not in repository_text
    assert "$vectorSearch" not in repository_text
