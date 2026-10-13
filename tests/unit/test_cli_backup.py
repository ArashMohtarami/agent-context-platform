"""Backup and restore: what reaches the tools, and what never does.

These run a real subprocess — a stand-in `mongodump` that records how it was
invoked — because the property worth proving is about the process table, and a
mocked call could not show it.
"""

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from app.cli import contextctl
from app.cli.errors import EXIT_FAILED, EXIT_OK, EXIT_UNAVAILABLE, EXIT_USAGE

PASSWORD = "correct-horse-battery-staple"
DATABASE = "agent_context_test"

# A stand-in for the MongoDB tools that records its own argv, its environment,
# and what the configuration file it was pointed at contained.
RECORDER = """#!/usr/bin/env python3
import json, os, sys

record = {"argv": sys.argv[1:], "config": None, "environment": dict(os.environ)}
for argument in sys.argv[1:]:
    if argument.startswith("--config="):
        path = argument.split("=", 1)[1]
        record["config"] = open(path).read()
        record["config_mode"] = oct(os.stat(path).st_mode & 0o777)
        record["config_path"] = path
open(os.environ["RECORD_TO"], "w").write(json.dumps(record))
for argument in sys.argv[1:]:
    if argument.startswith("--out=") or argument.startswith("--archive="):
        target = argument.split("=", 1)[1]
        if not argument.startswith("--out="):
            open(target, "w").write("archive")
sys.exit(int(os.environ.get("EXIT_WITH", "0")))
"""
FAILING = """#!/usr/bin/env python3
import sys
print("progress 50%", file=sys.stderr)
print("Failed: authentication failed", file=sys.stderr)
sys.exit(1)
"""


@pytest.fixture
def platform_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Configure the database settings the backup commands read."""
    password_file = tmp_path / "mongodb-password"
    password_file.write_text(PASSWORD, encoding="utf-8")
    monkeypatch.setenv("MONGODB_URI", "mongodb://mongodb:27017")
    monkeypatch.setenv("MONGODB_DATABASE", DATABASE)
    monkeypatch.setenv("MONGODB_USERNAME", "application_user")
    monkeypatch.setenv("MONGODB_PASSWORD_FILE", str(password_file))
    monkeypatch.delenv("MONGODB_PASSWORD", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(contextctl, "get_settings", get_settings)
    return tmp_path


def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str = RECORDER) -> Path:
    """Put stand-in MongoDB tools on PATH and return where they record to."""
    binaries = tmp_path / "bin"
    binaries.mkdir(exist_ok=True)
    for tool in ("mongodump", "mongorestore"):
        executable = binaries / tool
        executable.write_text(body, encoding="utf-8")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{binaries}{os.pathsep}{os.environ['PATH']}")
    record = tmp_path / "record.json"
    monkeypatch.setenv("RECORD_TO", str(record))
    return record


def recorded(record: Path) -> dict[str, Any]:
    """Read what the stand-in tool was invoked with."""
    loaded: dict[str, Any] = json.loads(record.read_text(encoding="utf-8"))
    return loaded


def test_the_password_never_reaches_the_process_table(
    platform_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = install(platform_environment, monkeypatch)
    output = platform_environment / "context.archive.gz"

    code = contextctl.main(["backup", "--output", str(output)])

    assert code == EXIT_OK
    invocation = recorded(record)
    # argv is world-readable through /proc on a shared host.
    assert PASSWORD not in " ".join(invocation["argv"])
    # And it is not smuggled through the environment either, which `ps e` and
    # /proc/<pid>/environ would show just as readily.
    assert PASSWORD not in json.dumps(invocation["environment"])
    # It arrives the one way MongoDB's tools support that keeps it private.
    assert invocation["config"] == f'password: "{PASSWORD}"\n'
    assert invocation["config_mode"] == "0o600"
    _ = capsys.readouterr()


def test_the_configuration_file_does_not_outlive_the_command(
    platform_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = install(platform_environment, monkeypatch)

    contextctl.main(["backup", "--output", str(platform_environment / "context.archive.gz")])
    _ = capsys.readouterr()

    assert not Path(recorded(record)["config_path"]).exists()


def test_the_dump_names_the_database_and_the_application_user(
    platform_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = install(platform_environment, monkeypatch)

    contextctl.main(["backup", "--output", str(platform_environment / "context.archive.gz")])
    _ = capsys.readouterr()

    argv = recorded(record)["argv"]
    assert f"--db={DATABASE}" in argv
    assert "--username=application_user" in argv
    assert f"--authenticationDatabase={DATABASE}" in argv
    assert "--gzip" in argv


def test_a_directory_dump_is_asked_for_explicitly(
    platform_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = install(platform_environment, monkeypatch)
    output = platform_environment / "dump"

    contextctl.main(["backup", "--output", str(output), "--directory", "--no-gzip"])
    _ = capsys.readouterr()

    argv = recorded(record)["argv"]
    assert f"--out={output}" in argv
    assert not any(item.startswith("--archive=") for item in argv)
    assert "--gzip" not in argv


def test_a_backup_refuses_to_overwrite_an_archive_that_exists(
    platform_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install(platform_environment, monkeypatch)
    output = platform_environment / "context.archive.gz"
    output.write_text("an earlier backup", encoding="utf-8")

    code = contextctl.main(["backup", "--output", str(output)])

    assert code == EXIT_USAGE
    assert "already exists" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == "an earlier backup"


def test_a_backup_refuses_to_merge_into_an_existing_dump_directory(
    platform_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install(platform_environment, monkeypatch)
    output = platform_environment / "dump"
    output.mkdir()

    code = contextctl.main(["backup", "--output", str(output), "--directory"])

    assert code == EXIT_USAGE
    assert "already exists" in capsys.readouterr().err


def test_an_unmanaged_archive_is_protected(
    platform_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install(platform_environment, monkeypatch)
    output = platform_environment / "context.archive.gz"

    code = contextctl.main(["backup", "--output", str(output)])
    _ = capsys.readouterr()

    assert code == EXIT_OK
    assert output.stat().st_mode & 0o777 == 0o600


def test_a_restore_without_confirmation_does_nothing(
    platform_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = install(platform_environment, monkeypatch)
    source = platform_environment / "context.archive.gz"
    source.write_text("archive", encoding="utf-8")

    code = contextctl.main(["restore", "--input", str(source)])

    assert code == EXIT_USAGE
    assert "--yes" in capsys.readouterr().err
    # Refused before the tool ran, not after.
    assert not record.exists()


def test_a_restore_from_an_absent_backup_is_refused(
    platform_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install(platform_environment, monkeypatch)

    code = contextctl.main(["restore", "--input", str(platform_environment / "absent.gz"), "--yes"])

    assert code == EXIT_USAGE
    assert "does not exist" in capsys.readouterr().err


def test_a_confirmed_restore_runs_and_can_drop_first(
    platform_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = install(platform_environment, monkeypatch)
    source = platform_environment / "context.archive.gz"
    source.write_text("archive", encoding="utf-8")

    code = contextctl.main(["restore", "--input", str(source), "--drop", "--yes"])
    out = capsys.readouterr().out

    assert code == EXIT_OK
    argv = recorded(record)["argv"]
    assert f"--archive={source}" in argv
    assert "--drop" in argv
    # The one thing a restore does not bring back is said plainly.
    assert "reindex" in out


def test_a_restore_tells_the_operator_what_to_do_next_as_json(
    platform_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install(platform_environment, monkeypatch)
    source = platform_environment / "context.archive.gz"
    source.write_text("archive", encoding="utf-8")

    code = contextctl.main(["--json", "restore", "--input", str(source), "--yes"])

    assert code == EXIT_OK
    reported = json.loads(capsys.readouterr().out)
    assert reported["database"] == DATABASE
    assert reported["dropped_first"] is False


def test_a_failing_tool_is_reported_with_its_own_last_words(
    platform_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install(platform_environment, monkeypatch, FAILING)

    code = contextctl.main(["backup", "--output", str(platform_environment / "context.archive.gz")])

    err = capsys.readouterr().err
    assert code == EXIT_FAILED
    assert "authentication failed" in err
    # Its progress noise is not worth showing.
    assert "50%" not in err


def test_missing_database_tools_are_reported_with_somewhere_to_get_them(
    platform_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", str(platform_environment / "empty"))

    code = contextctl.main(["backup", "--output", str(platform_environment / "context.archive.gz")])

    err = capsys.readouterr().err
    assert code == EXIT_UNAVAILABLE
    assert "mongodump was not found" in err
    assert "rebuild the context-api image" in err
