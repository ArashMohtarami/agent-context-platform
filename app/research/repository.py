"""Read-only, bounded Git repository acquisition for V3 research.

The providers in this module are intentionally narrow.  They accept only an
already-registered HTTPS repository URI, preflight its host as a public
address, and invoke a fixed Git binary with a sterile configuration.  Git is
used only to read tree objects and blobs: no checkout, hook, submodule,
package script, build tool, repository binary, or repository source code is
ever executed.

The resulting records are inert text and provenance.  They are consumed by
``ResearchIngestionService`` and stored in the existing V3 snapshot, document,
and chunk collections.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Final, Protocol
from urllib.parse import urlsplit

from app.models.common import Metadata
from app.models.research import ResearchSourceDocument, ResearchSourceType
from app.research.providers import IngestionPayload, SourceProviderError

MAX_REPOSITORY_FILES: Final = 2_000
MAX_REPOSITORY_FILE_BYTES: Final = 200_000
MAX_REPOSITORY_TOTAL_BYTES: Final = 5_000_000
MAX_REPOSITORY_GIT_BYTES: Final = 50_000_000
MAX_REPOSITORY_TREE_BYTES: Final = 2_000_000
GIT_COMMAND_TIMEOUT_SECONDS: Final = 20.0
_GIT_REF: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_COMMIT: Final = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_SECRET_PATH: Final = re.compile(
    r"(?:^|/)(?:\.env(?:\.[^/]+)?|(?:id|identity)_(?:rsa|dsa|ecdsa|ed25519)|"
    r"(?:secrets?|credentials?)(?:\.[^/]*)?|.*\.(?:pem|p12|pfx|key))$",
    re.IGNORECASE,
)
_SECRET_CONTENT: Final = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|"
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"AKIA[0-9A-Z]{16})"
)
_IGNORED_PARTS: Final[frozenset[str]] = frozenset(
    {".git", "node_modules", ".venv", "venv", "vendor", "dist", "build", ".tox"}
)
_BINARY_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".7z",
        ".a",
        ".bin",
        ".class",
        ".dll",
        ".dylib",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".lock",
        ".mp3",
        ".mp4",
        ".o",
        ".pdf",
        ".png",
        ".so",
        ".tar",
        ".woff",
        ".woff2",
        ".zip",
    }
)
_CLOUD_METADATA_HOSTS: Final[frozenset[str]] = frozenset(
    {"metadata.google.internal", "metadata.google.com", "metadata.azure.com", "instance-data"}
)


class GitCommandError(RuntimeError):
    """A bounded Git invocation failed without exposing remote diagnostics."""


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    """The only Git output repository ingestion needs."""

    stdout: bytes


class GitCommandRunner(Protocol):
    """Fixed-command interface, deliberately not an arbitrary command runner."""

    async def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path | None = None,
        maximum_output_bytes: int = MAX_REPOSITORY_TREE_BYTES,
    ) -> GitCommandResult: ...


class SubprocessGitCommandRunner:
    """Run the trusted Git executable without a shell or ambient Git config."""

    async def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path | None = None,
        maximum_output_bytes: int = MAX_REPOSITORY_TREE_BYTES,
    ) -> GitCommandResult:
        if maximum_output_bytes < 1:
            raise ValueError("maximum Git output must be positive")
        environment = {
            "PATH": os.defpath,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_ATTR_NOSYSTEM": "1",
            "HOME": tempfile.gettempdir(),
            # Never let a deployment proxy turn a public-host validation into
            # an implicit internal request. Git has no HTTPX-style peer hook,
            # so it uses direct HTTPS and refuses redirects below.
            "http_proxy": "",
            "https_proxy": "",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
            "all_proxy": "",
        }
        process = await asyncio.create_subprocess_exec(
            "git",
            *arguments,
            cwd=None if cwd is None else str(cwd),
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stdout_overflow, stderr, stderr_overflow = await asyncio.wait_for(
                _read_process(process, maximum_output_bytes), timeout=GIT_COMMAND_TIMEOUT_SECONDS
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise GitCommandError("repository Git command exceeded its time limit") from error
        if stdout_overflow or stderr_overflow:
            raise GitCommandError("repository Git command exceeded its output limit")
        if process.returncode != 0:
            # Remote-side diagnostics can contain untrusted data. In
            # particular, do not copy a potentially credential-bearing URI to
            # logs, HTTP responses, snapshots, or job errors.
            raise GitCommandError("repository Git command failed")
        return GitCommandResult(stdout=stdout)


async def _read_process(
    process: asyncio.subprocess.Process, maximum_output_bytes: int
) -> tuple[bytes, bool, bytes, bool]:
    """Drain both pipes while terminating a command that exceeds its cap."""

    async def collect(stream: asyncio.StreamReader | None) -> tuple[bytes, bool]:
        if stream is None:
            return b"", False
        output = bytearray()
        overflow = False
        while data := await stream.read(64 * 1024):
            available = maximum_output_bytes + 1 - len(output)
            output.extend(data[: max(available, 0)])
            if len(data) > available:
                overflow = True
                if process.returncode is None:
                    process.kill()
        return bytes(output), overflow

    stdout_task = asyncio.create_task(collect(process.stdout))
    stderr_task = asyncio.create_task(collect(process.stderr))
    await process.wait()
    stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
    return stdout[0], stdout[1], stderr[0], stderr[1]


type Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


@dataclass(frozen=True, slots=True)
class RepositoryFile:
    """One selected UTF-8 text blob read from a repository tree object."""

    path: str
    language: str
    content_type: str
    content: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class ExtractedRepository:
    """An immutable repository observation and its selected text files."""

    repository: str
    branch: str
    commit: str
    tag: str | None
    retrieved_at: datetime
    source_hash: str
    snapshot_metadata: Metadata
    files: tuple[RepositoryFile, ...]


class RepositorySourceProvider(Protocol):
    """A source provider that can acquire only a registered repository URI."""

    def supports(self, source_type: ResearchSourceType) -> bool: ...

    async def extract_repository(
        self, source: ResearchSourceDocument, payload: IngestionPayload
    ) -> ExtractedRepository: ...


@dataclass(frozen=True, slots=True)
class _RepositorySelection:
    branch: str | None
    tag: str | None
    commit: str | None

    @property
    def ref(self) -> str | None:
        return self.branch or self.tag


class GitSourceProvider:
    """Read public HTTPS Git repositories without checking out or executing them."""

    _types: Final[frozenset[ResearchSourceType]] = frozenset({ResearchSourceType.GIT})

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        runner: GitCommandRunner | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._resolver = resolver or _resolve_public_host
        self._runner = runner or SubprocessGitCommandRunner()
        self._clock = clock or (lambda: datetime.now(UTC))

    def supports(self, source_type: ResearchSourceType) -> bool:
        return source_type in self._types

    async def extract_repository(
        self, source: ResearchSourceDocument, payload: IngestionPayload
    ) -> ExtractedRepository:
        if not self.supports(source.source_type) or not payload.acquire_repository:
            raise SourceProviderError("this source type requires registered repository acquisition")
        repository = source.canonical_uri or source.uri
        await _validate_public_https_repository(repository, self._resolver)
        selection = _selection(source.trust_metadata)
        return await self._read_repository(repository, selection)

    async def _read_repository(
        self, repository: str, selection: _RepositorySelection
    ) -> ExtractedRepository:
        with tempfile.TemporaryDirectory(prefix="research-git-") as temporary:
            root = Path(temporary)
            checkout = root / "repository"
            clone = [
                "-c",
                f"core.hooksPath={root / 'no-hooks'}",
                "-c",
                "protocol.file.allow=never",
                "-c",
                "protocol.ext.allow=never",
                "-c",
                "http.followRedirects=false",
                "clone",
                "--no-checkout",
                "--no-recurse-submodules",
                "--single-branch",
                "--depth",
                "1",
                "--filter=blob:none",
            ]
            if selection.ref is not None:
                clone.extend(("--branch", selection.ref))
            clone.extend((repository, str(checkout)))
            try:
                await self._runner.run(clone, maximum_output_bytes=32_000)
                _require_git_size(checkout)
                revision = "HEAD"
                if selection.commit is not None:
                    # Read one explicit immutable object without a checkout.
                    # Servers that decline SHA fetches fail closed; no ref is
                    # broadened and no project code is invoked.
                    await self._runner.run(
                        (
                            "-C",
                            str(checkout),
                            "fetch",
                            "--no-tags",
                            "--depth",
                            "1",
                            "--filter=blob:none",
                            "origin",
                            selection.commit,
                        ),
                        maximum_output_bytes=32_000,
                    )
                    _require_git_size(checkout)
                    revision = "FETCH_HEAD"
                commit = (
                    (
                        await self._runner.run(
                            ("-C", str(checkout), "rev-parse", revision), maximum_output_bytes=512
                        )
                    )
                    .stdout.decode("ascii", errors="strict")
                    .strip()
                    .lower()
                )
                if not _COMMIT.fullmatch(commit):
                    raise SourceProviderError("repository did not resolve to a commit")
                if selection.commit is not None and selection.commit.lower() != commit:
                    raise SourceProviderError("repository did not resolve to the requested commit")
                branch = selection.branch or await _default_branch(self._runner, checkout) or "HEAD"
                files, skipped_binary, skipped_secret = await self._files(checkout, commit)
            except (GitCommandError, UnicodeDecodeError) as error:
                raise SourceProviderError("repository could not be read safely") from error

        if not files:
            raise SourceProviderError("repository did not contain eligible text files")
        observed = self._clock().astimezone(UTC)
        source_hash = sha256(f"{repository}\x00{commit}".encode()).hexdigest()
        metadata: Metadata = {
            "repository": repository,
            "branch": branch,
            "commit": commit,
            "ingested_at": observed.isoformat(),
            "files_seen": len(files) + skipped_binary + skipped_secret,
            "files_ingested": len(files),
            "files_skipped_binary": skipped_binary,
            "files_skipped_secret": skipped_secret,
        }
        if selection.tag is not None:
            metadata["tag"] = selection.tag
        return ExtractedRepository(
            repository=repository,
            branch=branch,
            commit=commit,
            tag=selection.tag,
            retrieved_at=observed,
            source_hash=source_hash,
            snapshot_metadata=metadata,
            files=tuple(files),
        )

    async def _files(self, checkout: Path, commit: str) -> tuple[list[RepositoryFile], int, int]:
        tree = await self._runner.run(
            ("-C", str(checkout), "ls-tree", "-r", "-z", "-l", commit),
            maximum_output_bytes=MAX_REPOSITORY_TREE_BYTES,
        )
        entries = _tree_entries(tree.stdout)
        if len(entries) > MAX_REPOSITORY_FILES:
            raise SourceProviderError("repository exceeds the file-count limit")
        files: list[RepositoryFile] = []
        total = 0
        skipped_binary = 0
        skipped_secret = 0
        for mode, kind, size, path in entries:
            if _SECRET_PATH.search(path):
                skipped_secret += 1
                continue
            if kind != "blob" or mode == "120000" or not _eligible_path(path):
                skipped_binary += 1
                continue
            blob_size = size
            if blob_size is None:
                blob_size = await self._blob_size(checkout, commit, path)
                _require_git_size(checkout)
            if blob_size > MAX_REPOSITORY_FILE_BYTES:
                skipped_binary += 1
                continue
            blob = await self._runner.run(
                ("-C", str(checkout), "show", "--no-textconv", f"{commit}:{path}"),
                maximum_output_bytes=MAX_REPOSITORY_FILE_BYTES,
            )
            _require_git_size(checkout)
            if b"\x00" in blob.stdout or _looks_binary(blob.stdout):
                skipped_binary += 1
                continue
            try:
                text = blob.stdout.decode("utf-8-sig", errors="strict")
            except UnicodeDecodeError:
                skipped_binary += 1
                continue
            text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
            if not text:
                continue
            if _SECRET_CONTENT.search(text):
                skipped_secret += 1
                continue
            encoded = text.encode("utf-8")
            total += len(encoded)
            if total > MAX_REPOSITORY_TOTAL_BYTES:
                raise SourceProviderError("repository exceeds the retained-text limit")
            language, content_type = _language(path)
            files.append(
                RepositoryFile(
                    path=path,
                    language=language,
                    content_type=content_type,
                    content=text,
                    content_hash=sha256(encoded).hexdigest(),
                )
            )
        return files, skipped_binary, skipped_secret

    async def _blob_size(self, checkout: Path, commit: str, path: str) -> int:
        size = await self._runner.run(
            ("-C", str(checkout), "cat-file", "-s", f"{commit}:{path}"),
            maximum_output_bytes=512,
        )
        try:
            value = int(size.stdout.decode("ascii", errors="strict").strip())
        except (UnicodeDecodeError, ValueError) as error:
            raise SourceProviderError("repository blob size was invalid") from error
        if value < 0:
            raise SourceProviderError("repository blob size was invalid")
        return value


class GitHubSourceProvider:
    """GitHub's public HTTPS clone endpoint, with the same inert Git reader."""

    _types: Final[frozenset[ResearchSourceType]] = frozenset({ResearchSourceType.GITHUB})

    def __init__(self, *, git_provider: GitSourceProvider | None = None) -> None:
        self._git = git_provider or GitSourceProvider()

    def supports(self, source_type: ResearchSourceType) -> bool:
        return source_type in self._types

    async def extract_repository(
        self, source: ResearchSourceDocument, payload: IngestionPayload
    ) -> ExtractedRepository:
        if not self.supports(source.source_type) or not payload.acquire_repository:
            raise SourceProviderError("this source type requires registered repository acquisition")
        repository = source.canonical_uri or source.uri
        _validate_github_repository(repository)
        # Rebind the already-safe underlying reader to GitHub's source type;
        # no GitHub token, API access, credentials, or write capability exists.
        delegated = source.model_copy(update={"source_type": ResearchSourceType.GIT})
        return await self._git.extract_repository(delegated, payload)


async def _resolve_public_host(host: str, port: int) -> Sequence[str]:
    try:
        records = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise SourceProviderError("repository host could not be resolved") from error
    return tuple(str(record[4][0]) for record in records)


async def _validate_public_https_repository(uri: str, resolver: Resolver) -> None:
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as error:
        raise SourceProviderError("repository URI has an invalid port") from error
    if parsed.scheme.lower() != "https":
        raise SourceProviderError("repository ingestion supports only https URIs")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SourceProviderError("repository URI cannot contain credentials, query, or fragments")
    host = parsed.hostname
    if host is None:
        raise SourceProviderError("repository URI must include a host")
    normalized = host.rstrip(".").lower()
    if (
        normalized == "localhost"
        or normalized.endswith(".localhost")
        or normalized in _CLOUD_METADATA_HOSTS
    ):
        raise SourceProviderError("repository host is not permitted")
    if port not in {None, 443}:
        raise SourceProviderError("repository HTTPS port is not permitted")
    try:
        literal = ipaddress.ip_address(normalized)
    except ValueError:
        addresses = await resolver(normalized, 443)
    else:
        addresses = (str(literal),)
    if not addresses:
        raise SourceProviderError("repository host did not resolve to an address")
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as error:
            raise SourceProviderError("repository DNS response was invalid") from error
        if not address.is_global:
            raise SourceProviderError("repository host resolves to a forbidden address")


def _validate_github_repository(uri: str) -> None:
    parsed = urlsplit(uri)
    if parsed.scheme.lower() != "https" or parsed.hostname != "github.com":
        raise SourceProviderError("GitHub sources must use github.com over https")
    parts = tuple(part for part in parsed.path.split("/") if part)
    if len(parts) != 2 or any(part in {".", ".."} for part in parts):
        raise SourceProviderError("GitHub sources must identify exactly one owner and repository")
    if (
        parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SourceProviderError("GitHub repository URI contains unsupported components")


def _selection(metadata: Metadata) -> _RepositorySelection:
    values: dict[str, str | None] = {}
    for field in ("branch", "tag", "commit"):
        value = metadata.get(field)
        if value is not None and not isinstance(value, str):
            raise SourceProviderError(f"repository {field} metadata must be a string")
        values[field] = value
    if sum(value is not None for value in values.values()) > 1:
        raise SourceProviderError(
            "repository metadata may specify only one of branch, tag, or commit"
        )
    for field in ("branch", "tag"):
        value = values[field]
        if value is not None and (
            not _GIT_REF.fullmatch(value)
            or ".." in value
            or "@{" in value
            or value.startswith((".", "/"))
            or value.endswith((".", "/"))
        ):
            raise SourceProviderError(f"repository {field} metadata is not a safe Git ref")
    commit = values["commit"]
    if commit is not None and not _COMMIT.fullmatch(commit):
        raise SourceProviderError("repository commit metadata must be a full commit hash")
    return _RepositorySelection(values["branch"], values["tag"], commit)


async def _default_branch(runner: GitCommandRunner, checkout: Path) -> str | None:
    try:
        raw = await runner.run(
            ("-C", str(checkout), "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"),
            maximum_output_bytes=512,
        )
    except GitCommandError:
        return None
    value = raw.stdout.decode("ascii", errors="strict").strip()
    return value.removeprefix("origin/") or None


def _require_git_size(checkout: Path) -> None:
    total = 0
    try:
        for candidate in checkout.rglob("*"):
            if candidate.is_file():
                total += candidate.stat().st_size
                if total > MAX_REPOSITORY_GIT_BYTES:
                    raise SourceProviderError("repository exceeds the clone-size limit")
    except OSError as error:
        raise SourceProviderError("repository clone could not be inspected") from error


def _tree_entries(data: bytes) -> tuple[tuple[str, str, int | None, str], ...]:
    result: list[tuple[str, str, int | None, str]] = []
    for raw in data.split(b"\x00"):
        if not raw:
            continue
        header, separator, raw_path = raw.partition(b"\t")
        values = header.split()
        if not separator or len(values) != 4:
            raise SourceProviderError("repository tree entry was malformed")
        mode, kind, _, raw_size = values
        try:
            path = raw_path.decode("utf-8", errors="strict")
            size = None if raw_size == b"-" else int(raw_size)
        except (UnicodeDecodeError, ValueError) as error:
            raise SourceProviderError("repository tree entry was invalid") from error
        if (size is not None and size < 0) or "\x00" in path:
            raise SourceProviderError("repository tree entry was invalid")
        result.append((mode.decode("ascii"), kind.decode("ascii"), size, path))
    return tuple(result)


def _eligible_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return False
    if any(part.casefold() in _IGNORED_PARTS for part in candidate.parts):
        return False
    name = candidate.name
    if not name or (name.startswith(".") and name != ".github"):
        return False
    return candidate.suffix.casefold() not in _BINARY_SUFFIXES


def _looks_binary(data: bytes) -> bool:
    if data.startswith((b"\x7fELF", b"MZ", b"\xca\xfe\xba\xbe")):
        return True
    return False


def _language(path: str) -> tuple[str, str]:
    suffix = PurePosixPath(path).suffix.casefold()
    mapping: dict[str, tuple[str, str]] = {
        ".c": ("c", "text/x-c"),
        ".cc": ("cpp", "text/x-cpp"),
        ".cpp": ("cpp", "text/x-cpp"),
        ".cs": ("csharp", "text/x-csharp"),
        ".go": ("go", "text/x-go"),
        ".h": ("c", "text/x-c"),
        ".java": ("java", "text/x-java"),
        ".js": ("javascript", "text/javascript"),
        ".json": ("json", "application/json"),
        ".jsx": ("javascript", "text/javascript"),
        ".kts": ("kotlin", "text/x-kotlin"),
        ".md": ("markdown", "text/markdown"),
        ".php": ("php", "text/x-php"),
        ".py": ("python", "text/x-python"),
        ".rb": ("ruby", "text/x-ruby"),
        ".rs": ("rust", "text/x-rust"),
        ".rst": ("rst", "text/plain"),
        ".sh": ("shell", "text/x-shellscript"),
        ".sql": ("sql", "text/x-sql"),
        ".tf": ("terraform", "text/x-terraform"),
        ".toml": ("toml", "application/toml"),
        ".ts": ("typescript", "text/typescript"),
        ".tsx": ("typescript", "text/typescript"),
        ".xml": ("xml", "application/xml"),
        ".yaml": ("yaml", "application/yaml"),
        ".yml": ("yaml", "application/yaml"),
    }
    if suffix in mapping:
        return mapping[suffix]
    names = {
        "dockerfile": ("dockerfile", "text/plain"),
        "makefile": ("make", "text/plain"),
        "go.mod": ("go", "text/plain"),
        "package.json": ("json", "application/json"),
        "pyproject.toml": ("toml", "application/toml"),
        "docker-compose.yml": ("yaml", "application/yaml"),
        "docker-compose.yaml": ("yaml", "application/yaml"),
    }
    return names.get(PurePosixPath(path).name.casefold(), ("text", "text/plain"))


__all__ = [
    "MAX_REPOSITORY_FILES",
    "MAX_REPOSITORY_FILE_BYTES",
    "MAX_REPOSITORY_TOTAL_BYTES",
    "ExtractedRepository",
    "GitCommandError",
    "GitCommandResult",
    "GitCommandRunner",
    "GitHubSourceProvider",
    "GitSourceProvider",
    "RepositoryFile",
    "RepositorySourceProvider",
    "SubprocessGitCommandRunner",
]
