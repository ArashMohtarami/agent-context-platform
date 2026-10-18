"""What the images this repository builds are allowed to be.

Read from the Dockerfile rather than from a built image, so the check runs
everywhere the suite does. What a build actually produces was confirmed by
running one: `id` reports uid 999, `CapEff` is zero, and `/app` refuses a write.
"""

import re
from pathlib import Path

import pytest

DOCKERFILE = Path("Dockerfile").read_text(encoding="utf-8")
# Every stage a container is ever started from. `runtime` is a base that is
# never run directly, and `dependency-builder` only installs wheels.
RUNNABLE = ("test", "production", "mcp")
RUNTIME_USER = "app"


def stage(name: str) -> str:
    """Return the instructions belonging to one build stage."""
    sections = re.split(r"^FROM .*? AS (\S+)\s*$", DOCKERFILE, flags=re.MULTILINE)
    named = dict(zip(sections[1::2], sections[2::2], strict=True))
    return named[name]


@pytest.mark.parametrize("name", RUNNABLE)
def test_nothing_that_runs_runs_as_root(name: str) -> None:
    # Declared in the image rather than at `docker run`, so it holds however
    # the container is started — including by a client spawning the MCP server.
    assert f"USER {RUNTIME_USER}" in stage(name)


def test_the_runtime_user_owns_nothing_it_does_not_need() -> None:
    # A system account with no password and no shell login, whose home is the
    # application directory it can read but, under a read-only root, not write.
    assert "groupadd --system app" in DOCKERFILE
    assert "useradd --system --gid app --home-dir /app app" in DOCKERFILE


def test_the_model_cache_is_owned_by_the_runtime_user() -> None:
    # The one directory the application writes to. It is a volume in Compose,
    # and the image's ownership is what a fresh volume inherits.
    assert "chown app:app /var/cache/embeddings" in DOCKERFILE


def test_the_runtime_contains_the_mongodb_backup_tools() -> None:
    # The CLI and Database Tools must coexist; MongoDB remains private.
    stage = next(line for line in DOCKERFILE.splitlines() if line.endswith(" AS mongodb-tools"))
    assert stage.startswith("FROM mongo:8.3.9-noble@sha256:")
    assert "--from=mongodb-tools /usr/bin/mongodump /usr/bin/mongorestore" in DOCKERFILE
    assert "COPY --from=operator-tools /operator-runtime/ /" in DOCKERFILE
    assert "libgssapi_krb5.so.2" in Path("docker/runtime/collect.py").read_text()


def test_the_test_image_contains_backup_deployment_contracts() -> None:
    """Containerized tests can inspect the files Phase 16 promises to ship."""
    assert "COPY deploy/systemd ./deploy/systemd" in DOCKERFILE
    assert "COPY docs/backup.md ./docs/backup.md" in DOCKERFILE
    assert "scripts/backup.sh scripts/restore.sh" in DOCKERFILE
    assert "COPY .env.example .gitignore ./" in DOCKERFILE


def test_no_stage_switches_back_to_root_after_dropping() -> None:
    for name in RUNNABLE:
        instructions = stage(name)
        after = instructions.split(f"USER {RUNTIME_USER}", 1)[1]
        assert "USER root" not in after
        # Nothing installs, copies, or chowns after privileges are dropped —
        # those are the instructions that would need them back.
        assert not re.search(r"^\s*(RUN|COPY|ADD)\b", after, flags=re.MULTILINE)


def test_no_credential_is_baked_into_an_image() -> None:
    # Passwords reach a container through a mounted secret file, never through
    # a build argument or an environment default, because both survive in the
    # image's layers for anyone who pulls it.
    assert "ARG" not in DOCKERFILE
    for forbidden in ("PASSWORD", "SECRET", "TOKEN", "API_KEY"):
        assert forbidden not in DOCKERFILE


def test_bytecode_is_never_written_into_the_image() -> None:
    # Also what makes a read-only root filesystem possible: an interpreter that
    # wrote .pyc files beside the source would fail on the first import.
    assert "PYTHONDONTWRITEBYTECODE=1" in DOCKERFILE
