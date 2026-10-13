"""The production posture, asserted rather than trusted to review.

Phase 18 reviewed image pinning, resource limits, restart policies, health
checks, timeouts, and shutdown. A review is a moment; these are what keep its
conclusions true after it, so a change that quietly removes a limit or unpins
an image fails here rather than in production.
"""

import re
from pathlib import Path

import pytest
import yaml

COMPOSE = Path("docker-compose.yml")
DOCKERFILE = Path("Dockerfile")
# Everything that is started and kept running. `mongodb-init` and the two
# profile services are excluded: they run once and exit, or are spawned.
LONG_RUNNING = ("mongodb", "mongot", "context-api")
# Every service that runs anything at all, including the one-shot bootstrap.
ALL_SERVICES = ("mongodb", "mongodb-init", "mongot", "context-api", "mcp", "test")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def compose() -> dict[str, dict[str, object]]:
    """Load the committed Compose model."""
    value = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    services = value["services"]
    assert isinstance(services, dict)
    return services


def dockerfile() -> str:
    """Return the committed Dockerfile."""
    return DOCKERFILE.read_text(encoding="utf-8")


# --- image and dependency pinning -------------------------------------------


def test_every_external_image_is_pinned_by_digest() -> None:
    # A tag moves. Pinning the digest is what makes a rebuild produce the image
    # that was reviewed rather than whatever the tag points at that day.
    images = [service["image"] for service in compose().values() if "image" in service]

    assert images, "no external images found"
    for image in images:
        assert DIGEST.search(str(image)), image


def test_every_dockerfile_base_image_is_pinned_by_digest() -> None:
    bases = [
        line.split()[1]
        for line in dockerfile().splitlines()
        if line.startswith("FROM ") and " AS " in line
    ]
    external = [base for base in bases if "/" in base or ":" in base]

    assert external, "no external base images found"
    for base in external:
        assert DIGEST.search(base), base


def test_the_dependency_lock_pins_every_version() -> None:
    for lock in (Path("requirements.lock"), Path("requirements-dev.lock")):
        requirements = [
            line.strip()
            for line in lock.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith(("#", "-"))
        ]
        assert requirements, lock
        for requirement in requirements:
            assert "==" in requirement, f"{lock}: {requirement}"


# --- resource limits --------------------------------------------------------


@pytest.mark.parametrize("name", ("mongodb", "mongodb-init", "mongot", "context-api", "mcp"))
def test_every_running_service_has_a_memory_ceiling(name: str) -> None:
    # An unbounded container takes the host down with it. The ceilings are set
    # above measured idle usage and are overridable, because a limit that kills
    # a working process is worse than no limit at all.
    assert "mem_limit" in compose()[name], name


@pytest.mark.parametrize("name", ("mongodb", "mongot", "context-api", "mcp"))
def test_every_running_service_bounds_its_cpu_and_its_processes(name: str) -> None:
    service = compose()[name]

    assert "cpus" in service, name
    # A process ceiling is the cheap defence against a fork bomb in anything
    # the container runs, including the database's own tooling.
    assert "pids_limit" in service, name


def test_the_search_coordinator_is_given_the_most_memory_to_start_with() -> None:
    # It holds the search and vector indexes in memory, which is the one cost
    # in this deployment that grows with the data rather than with the traffic.
    services = compose()
    assert "MONGOT_MEM_LIMIT" in str(services["mongot"]["mem_limit"])
    assert "mem_reservation" in services["mongot"]


# --- restart and shutdown ---------------------------------------------------


@pytest.mark.parametrize("name", LONG_RUNNING)
def test_a_long_running_service_is_restarted_unless_it_was_stopped(name: str) -> None:
    assert compose()[name]["restart"] == "unless-stopped", name


def test_the_one_shot_bootstrap_is_never_restarted() -> None:
    # It is idempotent but it is also finished: restarting it on exit would
    # loop forever and would keep re-running credential management.
    assert compose()["mongodb-init"]["restart"] == "no"


def test_a_spawned_service_declares_no_restart_policy() -> None:
    # An MCP server belongs to the client that spawned it. Restarting one whose
    # client has gone would leave a process reading a stdin nobody writes to.
    assert "restart" not in compose()["mcp"]


@pytest.mark.parametrize("name", LONG_RUNNING)
def test_a_long_running_service_is_given_time_to_stop(name: str) -> None:
    assert "stop_grace_period" in compose()[name], name


def test_the_api_is_allowed_to_finish_a_request_before_it_is_killed() -> None:
    from app.core.config import Settings

    grace = compose()["context-api"]["stop_grace_period"]
    # The container's grace period must exceed the server's own graceful
    # shutdown timeout, or the orchestrator's kill is what ends the process and
    # the timeout never gets to do its job.
    assert str(grace) == "30s"
    assert Settings.model_fields["api_shutdown_timeout_seconds"].default < 30


# --- health checks ----------------------------------------------------------


@pytest.mark.parametrize("name", LONG_RUNNING)
def test_every_long_running_service_reports_its_own_health(name: str) -> None:
    assert "healthcheck" in compose()[name], name


def test_the_image_carries_a_health_check_of_its_own() -> None:
    # So the image is healthy or not wherever it runs, not only under the
    # Compose file that happens to be in this repository.
    text = dockerfile()

    assert "HEALTHCHECK" in text
    assert "/health/ready" in text


def test_health_is_readiness_rather_than_liveness() -> None:
    # An API that answers but cannot reach MongoDB is not healthy, and calling
    # it healthy is how a rollout proceeds into an outage.
    assert "/health/ready" in str(compose()["context-api"]["healthcheck"])
    assert "/health/live" not in str(compose()["context-api"]["healthcheck"])


# --- exposure ---------------------------------------------------------------


def test_only_the_api_publishes_a_port_and_only_to_loopback() -> None:
    services = compose()
    published = {name: s["ports"] for name, s in services.items() if s.get("ports")}

    assert set(published) == {"context-api"}
    for mapping in published["context-api"]:
        assert str(mapping).startswith("127.0.0.1:"), mapping


def test_the_database_and_the_coordinator_are_reachable_only_inside() -> None:
    services = compose()
    for name in ("mongodb", "mongot"):
        assert "ports" not in services[name], name
        assert services[name]["networks"] == ["agent_context"], name


def test_the_private_network_is_internal() -> None:
    value = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert value["networks"]["agent_context"]["internal"] is True


# --- no secrets in images ---------------------------------------------------


def instructions() -> list[str]:
    """Return the Dockerfile's instructions, without its prose.

    Comments explain; only instructions build, so only instructions can put
    something into an image.
    """
    return [
        line.strip()
        for line in dockerfile().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_the_image_build_takes_no_secret_and_bakes_none_in() -> None:
    built = instructions()

    # A build argument survives in the image's layers, so the build takes none
    # and there is nothing for a secret to arrive through.
    assert not any(line.startswith("ARG ") for line in built)
    for line in built:
        upper = line.upper()
        for forbidden in ("PASSWORD", "SECRET", "TOKEN", "KEYFILE", "CREDENTIAL"):
            assert forbidden not in upper, line


def test_no_secret_file_is_copied_into_any_image() -> None:
    copied = [line for line in dockerfile().splitlines() if line.startswith("COPY ")]

    for line in copied:
        assert "secrets" not in line, line
        assert ".env\b" not in line, line
        # `.env.example` carries no values, only names and comments.
        assert ".env " not in line, line


def test_readiness_answers_before_the_health_check_stops_waiting() -> None:
    # A readiness answer that arrives after the thing asking for it has given
    # up is not an answer: the container would be marked unhealthy on a
    # timeout, telling an operator nothing about which dependency was at fault.
    from app.core.config import Settings
    from app.db.health import MONGOT_PROBE_TIMEOUT_SECONDS

    health_check = compose()["context-api"]["healthcheck"]
    assert isinstance(health_check, dict)
    waits = float(str(health_check["timeout"]).rstrip("s"))

    assert Settings.model_fields["api_readiness_timeout_seconds"].default < waits
    # And the coordinator probe, which runs inside one of those probes, has to
    # fit inside the budget it is bounded by.
    assert (
        MONGOT_PROBE_TIMEOUT_SECONDS
        < Settings.model_fields["api_readiness_timeout_seconds"].default
    )
