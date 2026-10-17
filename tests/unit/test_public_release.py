"""Release artifacts exclude private state and fail without revealing credentials."""

import os
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

import pytest

from app.security.keys import mint
from scripts import prepare_open_source_release as release


@pytest.fixture
def public_root(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    for name in ("README.md", "LICENSE", "pyproject.toml"):
        (root / name).write_text(f"Public {name}\n", encoding="utf-8")
    return root


def put(root: Path, relative: str, contents: bytes = b"public\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    return path


def git(root: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - arguments are fixed test commands in a temporary checkout.
        ["git", "-C", str(root), *arguments],  # noqa: S607 - Git is the tool under test.
        check=True,
        capture_output=True,
    )


def forbid_reads(monkeypatch: pytest.MonkeyPatch, paths: set[Path]) -> None:
    read_bytes = Path.read_bytes

    def guarded(path: Path) -> bytes:
        assert path not in paths, f"private file was read: {path.name}"
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)


@pytest.mark.parametrize(
    "relative",
    [
        "secrets/service-token",
        "backups/database.archive.gz",
        ".private/design.md",
        ".codex/config.toml",
        ".agents/session.md",
        ".aws/credentials",
        ".graphify/venv/local-state.json",
        "graphify-out/graph.json",
        "docs/graphify-out/GRAPH_REPORT.md",
        ".env",
        ".env.production",
        "docs/.env.local",
        "docs/secrets/token.txt",
        "docs/backups/database.md",
        "app/__pycache__/module.pyc",
        "agent-context-platform-plan.md",
        "docs/agent-context-platform-v5-prompts.md",
        "my-doc.md",
        "docs/mvp-user-guide.md",
        "docs/open-source-release.md",
        "docs/repository-content-launch.md",
        "docs/acceptance-report.md",
        "docs/v4-readiness-report.md",
        "docs/v5-gemini-security-review.md",
        "unrelated-personal-project/README.md",
    ],
)
def test_private_files_are_never_read_or_selected(
    public_root: Path, relative: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = put(public_root, relative, mint().token.encode())
    forbid_reads(monkeypatch, {private})

    files = release.audited_files(public_root)

    assert PurePosixPath(relative) not in files
    assert set(files) == {
        PurePosixPath(name) for name in ("README.md", "LICENSE", "pyproject.toml")
    }


def test_untracked_public_files_and_tracked_private_files_have_distinct_policies(
    public_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(public_root, "init", "--quiet")
    private = put(public_root, "docs/.private/implementation.md", mint().token.encode())
    git(public_root, "add", "README.md", "LICENSE", "pyproject.toml", "docs/.private")
    put(public_root, ".gitignore", b"docs/ignored-local.md\nsecrets/\n")
    ignored = put(public_root, "docs/ignored-local.md", mint().token.encode())
    secret = put(public_root, "secrets/ignored-token", mint().token.encode())
    put(public_root, "docs/new-public-guide.md")
    forbid_reads(monkeypatch, {private, ignored, secret})

    files = release.audited_files(public_root)

    assert PurePosixPath("docs/new-public-guide.md") in files
    assert PurePosixPath(".gitignore") in files
    assert PurePosixPath("docs/.private/implementation.md") not in files
    assert PurePosixPath("docs/ignored-local.md") not in files
    assert PurePosixPath("secrets/ignored-token") not in files


def test_gitignore_does_not_hide_a_tracked_public_secret(public_root: Path) -> None:
    git(public_root, "init", "--quiet")
    token = mint().token
    put(public_root, "docs/previously-tracked.md", token.encode())
    git(public_root, "add", "docs/previously-tracked.md")
    put(public_root, ".gitignore", b"docs/previously-tracked.md\n")

    with pytest.raises(ValueError, match="possible platform API key") as caught:
        release.audited_files(public_root)

    assert token not in str(caught.value)


def test_git_objects_and_deleted_tracked_private_sources_do_not_enter_the_archive(
    public_root: Path, tmp_path: Path
) -> None:
    git(public_root, "init", "--quiet")
    private = put(public_root, "agent-context-platform-plan.md", mint().token.encode())
    git(public_root, "add", "README.md", "LICENSE", "pyproject.toml", private.name)
    private.unlink()
    output = tmp_path / "git-checkout.tar.gz"

    files = release.audited_files(public_root)
    release.write_archive(public_root, files, output)

    with tarfile.open(output, "r:gz") as archive:
        assert set(archive.getnames()) == {
            "agent-context-platform/README.md",
            "agent-context-platform/LICENSE",
            "agent-context-platform/pyproject.toml",
        }


@pytest.mark.parametrize("directory_link", [False, True])
def test_public_symlinks_are_refused_without_reading_their_targets(
    public_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory_link: bool
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = put(outside, "token.md", mint().token.encode())
    linked = public_root / "docs" / ("linked" if directory_link else "linked.md")
    linked.parent.mkdir()
    linked.symlink_to(outside if directory_link else secret, target_is_directory=directory_link)
    forbid_reads(monkeypatch, {linked, linked / "token.md", secret})

    with pytest.raises(ValueError, match="symlink is not allowed"):
        release.audited_files(public_root)


def test_a_tracked_file_under_a_replaced_symlink_ancestor_cannot_escape(
    public_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(public_root, "init", "--quiet")
    tracked = put(public_root, "app/module.py")
    git(public_root, "add", "app/module.py")
    tracked.unlink()
    tracked.parent.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = put(outside, "module.py", mint().token.encode())
    (public_root / "app").symlink_to(outside, target_is_directory=True)
    forbid_reads(monkeypatch, {tracked, secret})

    with pytest.raises(ValueError, match="app/module.py: symlink is not allowed"):
        release.audited_files(public_root)


@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("platform API key", None),
        ("GitHub token", "ghp_" + "X" * 36),
        ("GitHub token", "github_pat_" + "Y" * 70),
        ("AWS access key", "AKIA" + "Z" * 16),
        ("AWS access key", "ASIA" + "Y" * 16),
        ("OpenAI API key", "sk-proj-" + "X" * 80),
        ("OpenAI API key", "sk-svcacct-" + "Y" * 64),
        ("private key", "-----BEGIN " + "RSA PRIVATE KEY-----"),
    ],
)
def test_secret_diagnostics_report_the_rule_and_line_without_the_value(
    public_root: Path, label: str, token: str | None
) -> None:
    value = token if token is not None else mint().token
    put(public_root, "docs/credentials.md", ("Header\n" + value + "\n").encode())

    with pytest.raises(ValueError) as caught:
        release.audited_files(public_root)

    message = str(caught.value)
    assert f"docs/credentials.md:2: possible {label}" in message
    assert value not in message


@pytest.mark.parametrize("relative", ["docs/sample.md", "tests/unit/test_unrelated.py"])
def test_known_synthetic_credentials_are_exempt_only_in_the_authorization_fixture(
    public_root: Path, relative: str
) -> None:
    fixture_token = next(iter(release.TEST_API_KEYS))
    put(public_root, "tests/unit/test_api_authorization.py", fixture_token)
    put(public_root, relative, fixture_token)

    with pytest.raises(ValueError) as caught:
        release.audited_files(public_root)

    message = str(caught.value)
    assert f"{relative}:1: possible platform API key" in message
    assert "test_api_authorization.py:" not in message
    assert fixture_token.decode() not in message


def test_an_actual_key_in_the_authorization_fixture_still_blocks_release(public_root: Path) -> None:
    token = mint().token
    put(public_root, "tests/unit/test_api_authorization.py", token.encode())

    with pytest.raises(ValueError, match="possible platform API key") as caught:
        release.audited_files(public_root)

    assert token not in str(caught.value)


def test_forbidden_text_is_case_insensitive_and_is_never_echoed(public_root: Path) -> None:
    private_text = "PrivateOrganizationName"
    put(public_root, "docs/sample.md", private_text.swapcase().encode())

    with pytest.raises(ValueError, match="docs/sample.md: forbidden private text rule 1") as caught:
        release.audited_files(public_root, (private_text,))

    assert private_text.casefold() not in str(caught.value).casefold()


def test_export_is_deterministic_contains_regular_files_and_reaudits_after_extraction(
    public_root: Path, tmp_path: Path
) -> None:
    executable = put(public_root, "scripts/example.sh", b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    put(public_root, "secrets/token", mint().token.encode())
    put(public_root, ".private/design.md")
    files = release.audited_files(public_root)
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"

    release.write_archive(public_root, files, first)
    for relative in files:
        os.utime(public_root / relative, (1_000_000, 2_000_000))
    release.write_archive(public_root, files, second)

    assert first.read_bytes() == second.read_bytes()
    extracted = tmp_path / "extracted"
    with tarfile.open(first, "r:gz") as archive:
        members = archive.getmembers()
        assert {member.name for member in members} == {
            f"agent-context-platform/{relative}" for relative in files
        }
        assert all(member.isfile() for member in members)
        assert all(member.uid == member.gid == member.mtime == 0 for member in members)
        assert all(member.uname == member.gname == "" for member in members)
        assert archive.getmember("agent-context-platform/scripts/example.sh").mode == 0o755
        assert archive.getmember("agent-context-platform/README.md").mode == 0o644
        archive.extractall(extracted, filter="data")
    assert release.audited_files(extracted / "agent-context-platform") == files


def test_export_does_not_overwrite_an_existing_artifact(public_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "release.tar.gz"
    output.write_bytes(b"existing artifact")

    with pytest.raises(FileExistsError):
        release.write_archive(public_root, release.audited_files(public_root), output)

    assert output.read_bytes() == b"existing artifact"


def test_failed_cli_audit_creates_no_archive_and_redacts_the_credential(
    public_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = mint().token
    put(public_root, "docs/credentials.md", token.encode())
    output = tmp_path / "blocked.tar.gz"
    monkeypatch.setattr(
        sys,
        "argv",
        ["prepare-release", "--repository-root", str(public_root), "--output", str(output)],
    )

    assert release.main() == 1

    diagnostics = capsys.readouterr()
    assert token not in diagnostics.out + diagnostics.err
    assert "possible platform API key" in diagnostics.out
    assert not output.exists()


def test_a_required_file_cannot_be_missing_from_the_release(public_root: Path) -> None:
    (public_root / "LICENSE").unlink()

    with pytest.raises(ValueError, match="LICENSE: required public release file is missing"):
        release.audited_files(public_root)


def test_dashboard_source_build_contract_and_licenses_are_in_public_release(public_root):
    public = (
        "dashboard/package.json",
        "dashboard/package-lock.json",
        "dashboard/LICENSE",
        "dashboard/THIRD_PARTY_NOTICES.txt",
        "dashboard/dependency-licenses.json",
        "dashboard/src/main.tsx",
        "dashboard/e2e/auth.spec.ts",
        "dashboard/scripts/check-build.mjs",
        "dashboard/README.md",
        "dashboard/licenses/react-remove-scroll-bar-LICENSE",
    )
    for name in public:
        put(public_root, name)
    for name in (
        "dashboard/node_modules/react/index.js",
        "dashboard/dist/assets/index.js",
        "dashboard/test-results/browser.xml",
        "dashboard/playwright-report/index.html",
        "dashboard/coverage/index.html",
        "dashboard/.private/fixture.json",
    ):
        put(public_root, name, mint().token.encode())
    assert set(release.audited_files(public_root)) == {
        PurePosixPath(name) for name in (*public, "README.md", "LICENSE", "pyproject.toml")
    }
