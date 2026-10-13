"""What a deployment has to provide for backups to actually happen.

A backup strategy is only real if it is scheduled, if the schedule points at
something that exists, and if what it writes is not published. These read the
committed files, so a unit or a script that drifts out of step with the
documentation fails here rather than on the night it is needed.
"""

import re
from pathlib import Path

import pytest
import yaml

UNITS = Path("deploy/systemd")
BACKUP_SERVICE = UNITS / "agent-context-backup.service"
BACKUP_TIMER = UNITS / "agent-context-backup.timer"
VERIFY_SERVICE = UNITS / "agent-context-backup-verify.service"
VERIFY_TIMER = UNITS / "agent-context-backup-verify.timer"
SCRIPTS = (Path("scripts/backup.sh"), Path("scripts/restore.sh"))


def read(path: Path) -> str:
    """Return one committed file's text."""
    return path.read_text(encoding="utf-8")


def compose() -> dict[str, object]:
    """Load the committed Compose model."""
    value = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def api_service() -> dict[str, object]:
    """Return the context-api service definition."""
    services = compose()["services"]
    assert isinstance(services, dict)
    definition = services["context-api"]
    assert isinstance(definition, dict)
    return definition


# --- scheduling ------------------------------------------------------------


@pytest.mark.parametrize("unit", [BACKUP_SERVICE, BACKUP_TIMER, VERIFY_SERVICE, VERIFY_TIMER])
def test_the_units_a_deployment_installs_are_committed(unit: Path) -> None:
    assert unit.is_file()
    assert "[Unit]" in read(unit)


def test_backups_are_scheduled_at_least_daily() -> None:
    # The retention policy keeps seven daily backups, which only means a week
    # of history if one is actually taken each day.
    timer = read(BACKUP_TIMER)

    assert "OnCalendar=daily" in timer
    assert "Unit=agent-context-backup.service" in timer


def test_a_backup_missed_while_the_host_was_off_is_still_taken() -> None:
    # Without this a machine that is not on at midnight quietly never backs up.
    assert "Persistent=true" in read(BACKUP_TIMER)
    assert "Persistent=true" in read(VERIFY_TIMER)


def test_the_restore_check_is_scheduled_separately_and_less_often() -> None:
    # It restores the archive into a throwaway database, so it costs time and
    # disk proportional to the data: weekly rather than nightly, but not never.
    verify = read(VERIFY_TIMER)

    assert "OnCalendar=Sun" in verify
    assert "Unit=agent-context-backup-verify.service" in verify
    assert "--restore" in read(VERIFY_SERVICE)


def test_each_timer_points_at_a_service_that_exists() -> None:
    for timer, service in ((BACKUP_TIMER, BACKUP_SERVICE), (VERIFY_TIMER, VERIFY_SERVICE)):
        named = re.search(r"^Unit=(.+)$", read(timer), flags=re.MULTILINE)
        assert named is not None
        assert (UNITS / named.group(1)).is_file()
        assert (UNITS / named.group(1)) == service


def test_a_scheduled_backup_runs_the_committed_script() -> None:
    started = re.search(r"^ExecStart=(\S+)", read(BACKUP_SERVICE), flags=re.MULTILINE)

    assert started is not None
    assert started.group(1).endswith("scripts/backup.sh")


@pytest.mark.parametrize("unit", [BACKUP_SERVICE, VERIFY_SERVICE])
def test_a_scheduled_backup_runs_unprivileged_and_bounded(unit: Path) -> None:
    text = read(unit)

    assert "NoNewPrivileges=true" in text
    assert re.search(r"^User=\S+", text, flags=re.MULTILINE)
    # A backup that hangs must not hold the timer forever.
    assert "TimeoutStartSec=" in text
    # It needs the checkout, Docker, and the backup directory. Nothing else.
    assert "ProtectSystem=strict" in text
    assert "ReadWritePaths=" in text


# --- the scripts a scheduler calls -----------------------------------------


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_scripts_are_executable_and_fail_loudly(script: Path) -> None:
    text = read(script)

    assert script.stat().st_mode & 0o111
    # Without `set -e` a failing dump would be followed by a cheerful message
    # and an exit code of zero, which is the worst possible backup.
    assert "set -Eeuo pipefail" in text


def test_a_scheduled_backup_uses_the_managed_verified_backup_command() -> None:
    script = read(SCRIPTS[0])

    assert '"${contextctl[@]}" backup' in script
    assert "SHA-256" in script
    assert "retention" in script


def test_restoring_is_never_one_command() -> None:
    # Restoring is step 2 of eleven. The steps around it are what make a
    # restore a recovery, so the script refuses to be mistaken for all of it.
    text = read(SCRIPTS[1])

    assert "RESTORE_CONFIRMED" in text
    assert "skills reindex" in text
    assert "docs/backup.md" in text


def test_restore_runs_in_a_one_off_container_while_the_api_is_stopped() -> None:
    text = read(SCRIPTS[1])

    assert "docker compose run --rm --no-deps -T" in text
    assert "docker compose exec" not in text.split("cat <<'NEXT'", 1)[0]


# --- where backups go ------------------------------------------------------


def test_the_api_can_write_backups_somewhere_that_is_not_a_named_volume() -> None:
    # A host path, so an operator can copy backups off the machine without
    # going through Docker. Getting them off the host is the point.
    definition = api_service()
    volumes = definition["volumes"]
    assert isinstance(volumes, list)

    assert any(str(volume).endswith(":/backups") for volume in volumes)
    assert any("BACKUP_HOST_DIRECTORY" in str(volume) for volume in volumes)


def test_the_retention_policy_is_environment_driven_with_the_documented_default() -> None:
    environment = api_service()["environment"]
    assert isinstance(environment, dict)

    assert environment["BACKUP_RETAIN_DAILY"] == "${BACKUP_RETAIN_DAILY:-7}"
    assert environment["BACKUP_RETAIN_WEEKLY"] == "${BACKUP_RETAIN_WEEKLY:-4}"
    assert environment["BACKUP_RETAIN_MONTHLY"] == "${BACKUP_RETAIN_MONTHLY:-3}"
    assert environment["BACKUP_DIRECTORY"] == "${BACKUP_DIRECTORY:-/backups}"


def test_backups_are_never_published() -> None:
    # One archive is every document the deployment holds. Only the API is
    # published, and the backup directory is not inside anything it serves.
    services = compose()["services"]
    assert isinstance(services, dict)
    published = [
        port
        for service in services.values()
        if isinstance(service, dict)
        for port in service.get("ports", [])
    ]

    assert published == ["127.0.0.1:${API_PORT:-8000}:8000"]
    assert "backups" not in str(api_service().get("build", ""))


def test_backups_are_not_committed() -> None:
    ignored = read(Path(".gitignore"))

    assert "backups/" in ignored
    assert "secrets/" in ignored


def test_the_example_environment_documents_every_backup_setting() -> None:
    example = read(Path(".env.example"))

    for name in (
        "BACKUP_HOST_DIRECTORY",
        "BACKUP_DIRECTORY",
        "BACKUP_RETAIN_DAILY",
        "BACKUP_RETAIN_WEEKLY",
        "BACKUP_RETAIN_MONTHLY",
    ):
        assert f"{name}=" in example


# --- what the documentation has to say -------------------------------------


def test_the_documentation_states_what_is_primary_and_what_is_derived() -> None:
    # The two facts a recovery depends on, and the two that are easiest to get
    # backwards: indexes are rebuilt, vectors are restored.
    document = read(Path("docs/backup.md")).lower()

    assert "search indexes are not a source of truth" in document
    assert "rebuild" in document and "reindex" in document
    assert "vectors are not an index" in document


def test_the_documentation_carries_the_whole_recovery_procedure() -> None:
    document = read(Path("docs/backup.md"))

    for step in (
        "Stop the affected services",
        "Restore MongoDB",
        "Verify the replica set",
        "Verify the collections",
        "Verify the indexes",
        "Start mongot",
        "Verify search",
        "Verify embeddings",
        "Start the API",
        "Start MCP",
        "Run smoke tests",
    ):
        assert step in document, f"the recovery procedure is missing: {step}"


def test_the_documentation_states_the_default_retention() -> None:
    document = read(Path("docs/backup.md"))

    assert "7 days" in document
    assert "4 ISO weeks" in document
    assert "3 months" in document
