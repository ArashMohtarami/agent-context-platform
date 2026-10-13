"""Docker Compose MongoDB topology tests."""

from pathlib import Path

import pytest
import yaml

from app.db.health import MONGOT_PROBE_COLLECTION

# Every service, and how much hardening its image can take. The ones built from
# this repository run unprivileged from their first instruction; the official
# MongoDB images start as root to chown their data directory and drop
# privileges with gosu, which needs capabilities that cannot be taken away
# without stopping them before they open the database.
OURS = ("context-api", "mcp", "test")
THEIRS = ("mongodb", "mongodb-init", "mongot")
# Read-only is every service of ours but the test runner, which writes a
# pytest cache into its working directory.
READ_ONLY = ("context-api", "mcp")
# The one writable path a read-only service gets. A path inside a container,
# not one on this host.
TMPFS = "/tmp"  # noqa: S108


def load_compose() -> dict[str, object]:
    """Load the committed Compose model without interpolating local secrets."""
    value = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def service(name: str) -> dict[str, object]:
    """Return one service definition."""
    services = load_compose()["services"]
    assert isinstance(services, dict)
    definition = services[name]
    assert isinstance(definition, dict)
    return definition


def test_mongodb_is_persistent_authenticated_and_private() -> None:
    compose = load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    mongodb = services["mongodb"]
    assert isinstance(mongodb, dict)

    assert str(mongodb["image"]).startswith("mongo:8.3.9-noble@sha256:")
    assert "ports" not in mongodb
    assert "mongodb_data:/data/db" in mongodb["volumes"]
    assert "--auth" in mongodb["command"]
    assert "--replSet" in mongodb["command"]
    assert mongodb["networks"] == ["agent_context"]

    networks = compose["networks"]
    assert isinstance(networks, dict)
    assert networks["agent_context"]["internal"] is True


def test_bootstrap_and_application_use_separate_credentials() -> None:
    compose = load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    init_environment = services["mongodb-init"]["environment"]
    app_environment = services["context-api"]["environment"]

    assert "MONGODB_ROOT_USERNAME" in init_environment
    assert app_environment["MONGODB_USERNAME"].startswith("${MONGODB_APP_USERNAME")
    assert app_environment["MONGODB_PASSWORD_FILE"] == "/run/secrets/mongodb-app-password"
    assert "MONGODB_ROOT_USERNAME" not in app_environment


def test_mongot_is_pinned_persistent_private_and_health_checked() -> None:
    compose = load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    mongot = services["mongot"]
    assert isinstance(mongot, dict)

    assert mongot["image"] == (
        "mongodb/mongodb-community-search:1.70.4"
        "@sha256:63ebc805c6b55d027e345b5224d8713009f21911aedafb50b6c28c1cd9583e4f"
    )
    assert "ports" not in mongot
    assert mongot["networks"] == ["agent_context"]
    assert "mongot_data:/data/mongot" in mongot["volumes"]
    assert mongot["healthcheck"]["test"] == [
        "CMD",
        "/usr/local/bin/mongot-healthcheck.sh",
    ]
    assert mongot["depends_on"]["mongodb-init"]["condition"] == ("service_completed_successfully")

    volumes = compose["volumes"]
    assert isinstance(volumes, dict)
    assert volumes["mongot_data"]["name"] == "agent_context_mongot_data"


def test_mongod_routes_search_to_mongot_but_application_has_no_direct_endpoint() -> None:
    compose = load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    mongodb = services["mongodb"]
    mongot = services["mongot"]
    application = services["context-api"]

    assert "searchIndexManagementHostAndPort=mongot.agent_context:27028" in mongodb["command"]
    assert "mongotHost=mongot.agent_context:27028" in mongodb["command"]
    assert "searchTLSMode=disabled" in mongodb["command"]
    assert "useGrpcForSearch=true" in mongodb["command"]
    assert "skipAuthenticationToSearchIndexManagementServer=false" in mongodb["command"]
    assert application["depends_on"]["mongot"]["condition"] == "service_healthy"
    assert application["environment"]["MONGODB_URI"].startswith("${MONGODB_URI:-mongodb://mongodb")
    assert all("MONGOT" not in name for name in application["environment"])
    assert mongot["environment"].keys() == {"MONGOT_SYNC_USERNAME", "MONGOT_LOG_LEVEL"}


def test_mongot_verification_materializes_its_coordinator_probe() -> None:
    script = Path("scripts/verify-mongot.sh").read_text(encoding="utf-8")
    test_image = Path("Dockerfile").read_text(encoding="utf-8").split("FROM runtime AS test")[1]

    assert f'getCollection("{MONGOT_PROBE_COLLECTION}")' in script
    assert "replaceOne" in script
    assert "$listSearchIndexes" in script
    assert "probe.drop()" in script
    assert "scripts/verify-mongot.sh" in test_image.split("FROM runtime AS production")[0]


def test_mongot_sync_user_is_distinct_and_uses_a_secret_file() -> None:
    compose = load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    init_environment = services["mongodb-init"]["environment"]
    mongot_environment = services["mongot"]["environment"]

    assert init_environment["MONGOT_SYNC_USERNAME"].startswith("${MONGOT_SYNC_USERNAME")
    assert init_environment["MONGOT_SYNC_PASSWORD_FILE"] == ("/run/secrets/mongot-sync-password")
    assert mongot_environment["MONGOT_SYNC_USERNAME"].startswith("${MONGOT_SYNC_USERNAME")
    assert "MONGOT_SYNC_PASSWORD" not in mongot_environment
    assert services["mongot"]["secrets"] == [
        {"source": "mongot_sync_password", "target": "mongot-sync-password"}
    ]


def test_embedding_models_are_cached_on_a_named_persistent_volume() -> None:
    compose = load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    application = services["context-api"]
    environment = application["environment"]

    assert "embedding_models:/var/cache/embeddings" in application["volumes"]
    assert environment["EMBEDDING_MODEL"].startswith("${EMBEDDING_MODEL:-sentence-transformers/")
    assert environment["EMBEDDING_DIMENSIONS"] == "${EMBEDDING_DIMENSIONS:-384}"
    assert environment["EMBEDDING_DEVICE"] == "${EMBEDDING_DEVICE:-cpu}"

    volumes = compose["volumes"]
    assert isinstance(volumes, dict)
    assert volumes["embedding_models"]["name"] == "agent_context_embedding_models"


def test_startup_allows_time_for_the_first_model_load() -> None:
    compose = load_compose()
    services = compose["services"]
    assert isinstance(services, dict)

    assert services["context-api"]["healthcheck"]["start_period"] == "180s"


def test_the_embedding_service_is_never_published_to_the_host() -> None:
    compose = load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    published = [
        port
        for service in services.values()
        if isinstance(service, dict)
        for port in service.get("ports", [])
    ]

    assert published == ["127.0.0.1:${API_PORT:-8000}:8000"]


def test_vector_search_tuning_is_environment_driven() -> None:
    compose = load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    environment = services["context-api"]["environment"]

    assert environment["VECTOR_SEARCH_CANDIDATE_MULTIPLIER"] == (
        "${VECTOR_SEARCH_CANDIDATE_MULTIPLIER:-10}"
    )
    assert environment["VECTOR_SEARCH_MAX_CANDIDATES"] == "${VECTOR_SEARCH_MAX_CANDIDATES:-1000}"
    assert not any(name.startswith("VECTOR_SEARCH_DIMENSION") for name in environment)


def test_hybrid_ranking_tuning_is_environment_driven() -> None:
    compose = load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    environment = services["context-api"]["environment"]

    assert environment["RETRIEVAL_WEIGHT_VECTOR"] == "${RETRIEVAL_WEIGHT_VECTOR:-0.45}"
    assert environment["RETRIEVAL_WEIGHT_LEXICAL"] == "${RETRIEVAL_WEIGHT_LEXICAL:-0.25}"
    assert environment["RETRIEVAL_TOKEN_BUDGET"] == "${RETRIEVAL_TOKEN_BUDGET:-4000}"
    assert environment["RETRIEVAL_MAX_TOKEN_BUDGET"] == "${RETRIEVAL_MAX_TOKEN_BUDGET:-16000}"
    assert environment["RESEARCH_RETRIEVAL_TOKEN_BUDGET"] == (
        "${RESEARCH_RETRIEVAL_TOKEN_BUDGET:-8000}"
    )
    assert environment["RESEARCH_RETRIEVAL_MAX_TOKEN_BUDGET"] == (
        "${RESEARCH_RETRIEVAL_MAX_TOKEN_BUDGET:-16000}"
    )
    assert environment["RETRIEVAL_LOG_ENABLED"] == "${RETRIEVAL_LOG_ENABLED:-false}"


def test_metrics_are_published_by_default_and_only_on_the_api_port() -> None:
    compose = load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    environment = services["context-api"]["environment"]

    assert environment["METRICS_ENABLED"] == "${METRICS_ENABLED:-true}"
    # No exporter, no collector, and no second port: a self-hosted scraper
    # reads the API's own port or nothing collects at all.
    assert services["context-api"]["ports"] == ["127.0.0.1:${API_PORT:-8000}:8000"]
    assert not any(name.startswith("OTEL_") for name in environment)


def test_the_mcp_server_is_spawned_rather_than_kept_running() -> None:
    compose = load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    mcp = services["mcp"]

    # An MCP server reads one conversation from its own stdin, so it is started
    # by the client that will talk to it, not by `docker compose up`.
    assert mcp["profiles"] == ["mcp"]
    assert mcp["stdin_open"] is True
    assert mcp["build"]["target"] == "mcp"
    # It speaks over stdio, so it publishes nothing and binds nothing.
    assert "ports" not in mcp
    assert "expose" not in mcp
    assert mcp["networks"] == ["agent_context"]


def test_the_mcp_server_holds_no_database_credentials() -> None:
    compose = load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    mcp = services["mcp"]
    environment = mcp["environment"]

    # The component an agent talks to directly should be able to do the least:
    # it reaches the platform through the API and nowhere else.
    assert not any(name.startswith("MONGODB_") for name in environment)
    assert "secrets" not in mcp
    assert environment["MCP_API_BASE_URL"] == "${MCP_API_BASE_URL:-http://context-api:8000}"


@pytest.mark.parametrize("name", OURS + THEIRS)
def test_no_service_may_gain_privileges_it_was_not_started_with(name: str) -> None:
    # Costs nothing and holds for every image here, ours and theirs.
    assert service(name)["security_opt"] == ["no-new-privileges:true"]


@pytest.mark.parametrize("name", OURS)
def test_our_own_images_run_without_any_capability(name: str) -> None:
    # They are unprivileged from their first instruction and bind a port above
    # 1024, so there is nothing left for a capability to be needed for.
    assert service(name)["cap_drop"] == ["ALL"]


@pytest.mark.parametrize("name", THEIRS)
def test_the_official_images_keep_the_capabilities_their_entrypoints_need(name: str) -> None:
    # Their entrypoints chown the data directory as root and then drop
    # privileges with gosu. Taking CHOWN, SETUID and SETGID away stops mongod
    # before it opens the database, so this records why they are not dropped
    # rather than leaving it looking like an omission.
    assert "cap_drop" not in service(name)


@pytest.mark.parametrize("name", READ_ONLY)
def test_the_running_services_cannot_write_to_their_own_image(name: str) -> None:
    definition = service(name)

    assert definition["read_only"] is True
    # One writable path, in memory, discarded with the container.
    assert definition["tmpfs"] == [TMPFS]


def test_the_model_cache_stays_writable_under_a_read_only_root() -> None:
    # A read-only root filesystem does not cover a volume, which is how the
    # model is downloaded once and kept across restarts.
    definition = service("context-api")

    assert definition["read_only"] is True
    assert "embedding_models:/var/cache/embeddings" in definition["volumes"]


def test_only_the_test_runner_keeps_a_writable_filesystem() -> None:
    # pytest writes its cache into the working directory. Nothing that serves
    # traffic needs to write anywhere, and none of them may.
    assert "read_only" not in service("test")
    for name in READ_ONLY:
        assert service(name)["read_only"] is True
