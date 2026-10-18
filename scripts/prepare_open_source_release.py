#!/usr/bin/env python3
"""Audit public source and export it without local state or private Git history.

Only known project directories and root files are eligible. In a checkout,
Git supplies tracked and nonignored files; an extracted release uses the same
policy over its filesystem. Diagnostics report paths and rules, never matches.
This bounded scan supplements review; it cannot identify every possible secret.
"""

from __future__ import annotations

import argparse
import gzip
import io
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

PUBLIC_DIRECTORIES = frozenset(
    {
        "app",
        "deploy",
        "dashboard",
        "docker",
        "docs",
        "examples",
        "scripts",
        "skills",
        "tests",
        "website",
        ".github",
    }
)
PUBLIC_ROOT_FILES = frozenset(
    {
        ".dockerignore",
        ".env.example",
        ".gitignore",
        ".graphifyignore",
        "CONTRIBUTING.md",
        "Dockerfile",
        "LICENSE",
        "MANIFEST.in",
        "Makefile",
        "README.md",
        "SECURITY.md",
        "docker-compose.yml",
        "docker-compose.website.yml",
        "pyproject.toml",
        "requirements.lock",
        "requirements-dev.lock",
    }
)
PRIVATE_PARTS = frozenset(
    {
        ".git",
        ".private",
        ".codex",
        ".agents",
        ".aws",
        ".venv",
        ".graphify",
        "graphify-out",
        "secrets",
        "backups",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "test-results",
        "playwright-report",
        "coverage",
        "dist",
        "build",
        "htmlcov",
    }
)
SECRET_PATTERNS = (
    ("private key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("platform API key", re.compile(rb"\bacp_[0-9a-f]{16}_[A-Za-z0-9_-]{32,}\b")),
    (
        "GitHub token",
        re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    ),
    ("AWS access key", re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("OpenAI API key", re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{48,}\b")),
)
# Deliberately invalid literals already used by authentication adversarial tests.
TEST_API_KEYS = frozenset(
    {
        b"acp_" + b"0123456789abcdef_" + b"a" * 34,
        b"acp_" + b"0123456789abcdef_" + b"wrongsecret" * 3,
    }
)
PRIVATE_DOCUMENT = re.compile(
    r"(?:agent-context-platform-(?:plan|v\d.*)\.md|my-doc\.md|"
    r"(?:mvp-user-guide|open-source-release|repository-content-launch)\.md|"
    r"(?:acceptance-report|v\d+-readiness-report|v\d+-.*security-review)\.md)$"
)


def eligible(path: PurePosixPath) -> bool:
    """Return whether a relative path belongs to the public source inventory."""
    if path.is_absolute() or ".." in path.parts or any(p in PRIVATE_PARTS for p in path.parts):
        return False
    if PRIVATE_DOCUMENT.fullmatch(path.name):
        return False
    if path.name.startswith(".env") and path.name != ".env.example":
        return False
    if path.suffix in {".pyc", ".pyo", ".sqlite3", ".gz", ".zip", ".pem", ".key"}:
        return False
    if any(p.endswith(".egg-info") for p in path.parts):
        return False
    if len(path.parts) == 1:
        return path.name in PUBLIC_ROOT_FILES
    return path.parts[0] in PUBLIC_DIRECTORIES or path.as_posix() == ".docker-wheels/.gitkeep"


def candidate_paths(root: Path) -> list[PurePosixPath]:
    """List files without traversing ignored private directories or Git history."""
    if (root / ".git").exists():
        git = shutil.which("git")
        if git is None:
            raise ValueError("Git is required to audit a checkout")
        result = subprocess.run(  # noqa: S603 - fixed read-only argv, no shell
            [git, "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            check=True,
            capture_output=True,
        )
        return sorted({PurePosixPath(os.fsdecode(p)) for p in result.stdout.split(b"\0") if p})
    candidates = []
    for directory, subdirectories, filenames in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        subdirectories[:] = [p for p in subdirectories if p not in PRIVATE_PARTS]
        # Directory symlinks cannot be allowed to escape an otherwise public root.
        for name in list(subdirectories):
            child = Path(directory) / name
            if child.is_symlink():
                candidates.append(PurePosixPath((relative / name).as_posix()))
                subdirectories.remove(name)
        candidates.extend(PurePosixPath((relative / name).as_posix()) for name in filenames)
    return sorted(candidates)


def audited_files(root: Path, forbidden: tuple[str, ...] = ()) -> dict[PurePosixPath, bytes]:
    """Read eligible files and reject suspect public contents without printing them."""
    files: dict[PurePosixPath, bytes] = {}
    problems = []
    for relative in candidate_paths(root):
        if not eligible(relative):
            continue
        path = root / relative
        # Check every ancestor too: a tracked directory may have been replaced
        # by a symlink since Git last saw it.
        ancestors = (
            root / PurePosixPath(*relative.parts[:i]) for i in range(1, len(relative.parts) + 1)
        )
        if any(p.is_symlink() for p in ancestors):
            problems.append(f"{relative}: symlink is not allowed in public source")
            continue
        if not path.exists():
            continue  # Tracked worktree deletions, including archived planning files.
        if not path.is_file():
            problems.append(f"{relative}: expected a regular file")
            continue
        contents = path.read_bytes()
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(contents):
                if (
                    label == "platform API key"
                    and relative.as_posix() == "tests/unit/test_api_authorization.py"
                    and match.group() in TEST_API_KEYS
                ):
                    continue
                line = contents.count(b"\n", 0, match.start()) + 1
                problems.append(f"{relative}:{line}: possible {label}")
        decoded = contents.decode("utf-8", errors="replace").casefold()
        for index, value in enumerate(forbidden, 1):
            if value.casefold() in decoded:
                problems.append(f"{relative}: forbidden private text rule {index}")
        files[relative] = contents
    for required in ("README.md", "LICENSE", "pyproject.toml"):
        if PurePosixPath(required) not in files:
            problems.append(f"{required}: required public release file is missing")
    if problems:
        raise ValueError("Public source audit failed:\n" + "\n".join(problems))
    return files


def write_archive(root: Path, files: dict[PurePosixPath, bytes], output: Path) -> None:
    """Write deterministic tar/gzip contents, omitting owner names and timestamps."""
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation keeps repeated runs from replacing an existing artifact.
    with output.open("xb") as destination:
        with gzip.GzipFile(fileobj=destination, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for relative, contents in sorted(files.items()):
                    member = tarfile.TarInfo(f"agent-context-platform/{relative}")
                    member.size = len(contents)
                    member.mode = 0o755 if (root / relative).stat().st_mode & 0o111 else 0o644
                    archive.addfile(member, io.BytesIO(contents))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true", help="Audit without writing an archive")
    parser.add_argument(
        "--output", type=Path, default=Path("dist/agent-context-platform-source.tar.gz")
    )
    parser.add_argument(
        "--forbid-text",
        action="append",
        default=[],
        metavar="TEXT",
        help="Reject a private organization name/path (repeatable; matches are never printed)",
    )
    arguments = parser.parse_args()
    root = arguments.repository_root.resolve()
    try:
        files = audited_files(root, tuple(arguments.forbid_text))
        print(
            f"Public source audit passed: {len(files)} files; "
            "no local state or Git history included."
        )
        if not arguments.check:
            write_archive(root, files, arguments.output.resolve())
            print(f"Source archive: {arguments.output}")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        # subprocess stderr is deliberately not printed; only our path-only
        # diagnostics and ordinary OS errors can reach the operator.
        print(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
