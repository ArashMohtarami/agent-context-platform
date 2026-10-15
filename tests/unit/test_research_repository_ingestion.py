"""V3 Phase 12 repository ingestion tests with an inert Git fixture transport."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.embeddings.types import EmbeddingVector
from app.models.research import ResearchAuthorization, ResearchSourceDocument
from app.research.providers import IngestionPayload, SourceProviderError
from app.research.repository import (
    MAX_REPOSITORY_FILE_BYTES,
    MAX_REPOSITORY_FILES,
    GitCommandResult,
    GitHubSourceProvider,
    GitSourceProvider,
)
from app.services.research_ingestion_service import ResearchIngestionService
from tests.unit.test_research_ingestion_service import Store

PROJECT = "payments-api"
RESEARCH = "repository-evidence"
SOURCE = "payments-repository"
COMMIT = "a" * 40
NOW = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)


def source(uri: str = "https://public.example/acme/payments.git") -> ResearchSourceDocument:
    return ResearchSourceDocument(
        research_project_id=RESEARCH,
        source_id=SOURCE,
        source_type="git",
        uri=uri,
        canonical_uri=uri,
        title="Payments service repository",
        source_class="source_code",
        trust_metadata={"branch": "main"},
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer"),
    )


def payload() -> IngestionPayload:
    return IngestionPayload(
        data=b"",
        content_type="application/octet-stream",
        title="Payments service repository",
        language="und",
        acquire_repository=True,
    )


def tree_entry(path: str, size: int) -> bytes:
    return f"100644 blob {'b' * 40} {size}\t{path}".encode() + b"\0"


@dataclass
class FixtureGitRunner:
    """A Git object transport: it records commands but never starts a process."""

    files: dict[str, bytes]
    unknown_blob_sizes: bool = False

    def __post_init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    async def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path | None = None,
        maximum_output_bytes: int = 2_000_000,
    ) -> GitCommandResult:
        values = tuple(arguments)
        self.commands.append(values)
        if "clone" in values:
            Path(values[-1]).mkdir(parents=True, exist_ok=True)
            return GitCommandResult(stdout=b"")
        if "fetch" in values:
            return GitCommandResult(stdout=b"")
        if "rev-parse" in values:
            return GitCommandResult(stdout=f"{COMMIT}\n".encode())
        if "symbolic-ref" in values:
            return GitCommandResult(stdout=b"origin/main\n")
        if "ls-tree" in values:
            return GitCommandResult(
                stdout=b"".join(
                    tree_entry(path, -1 if self.unknown_blob_sizes else len(content))
                    for path, content in self.files.items()
                ).replace(b" -1\t", b" -\t")
            )
        if "cat-file" in values:
            requested = values[-1].split(":", 1)[1]
            return GitCommandResult(stdout=f"{len(self.files[requested])}\n".encode())
        if "show" in values:
            requested = values[-1].split(":", 1)[1]
            return GitCommandResult(stdout=self.files[requested])
        raise AssertionError(f"unexpected fixed Git operation: {values}")


class Embeddings:
    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        return tuple(
            EmbeddingVector(model="local-test", dimensions=3, values=(0.25, 0.5, 0.75))
            for _ in contents
        )


def provider(runner: FixtureGitRunner) -> GitSourceProvider:
    async def resolve(host: str, port: int) -> tuple[str, ...]:
        assert host == "public.example"
        assert port == 443
        return ("93.184.216.34",)

    return GitSourceProvider(resolver=resolve, runner=runner, clock=lambda: NOW)


def service(store: Store, provider: GitSourceProvider) -> ResearchIngestionService:
    return ResearchIngestionService(
        store.snapshots_for,
        store.documents_for,
        store.chunks_for,
        store.jobs_for,
        Embeddings(),
        git_provider=provider,
    )


@pytest.mark.asyncio
async def test_fixture_repository_is_ingested_as_versioned_documents_with_code_locations() -> None:
    runner = FixtureGitRunner(
        {
            "README.md": b"# Payments\n\nAuthentication is handled by the API service.\n",
            "docs/adr/0001-idempotency.md": b"# Idempotency\n\nUse a stable idempotency key.\n",
            "src/auth.py": (
                b"import secrets\n\n\ndef authenticate(token: str) -> bool:\n"
                b"    return bool(token)\n\n\ndef unrelated() -> None:\n    pass\n"
            ),
            "docker-compose.yml": b"services:\n  worker:\n    image: payments\n",
            "tests/test_auth.py": b"def test_authentication() -> None:\n    assert True\n",
            ".env": b"DATABASE_PASSWORD=not-retained\n",
            "assets/logo.png": b"\x89PNG\r\n\x1a\n",
        }
    )
    store = Store()
    first = await service(store, provider(runner)).ingest(
        source=source(), payload=payload(), manually_provided=False
    )
    second = await service(store, provider(runner)).ingest(
        source=source(), payload=payload(), manually_provided=False
    )

    assert first.idempotent is False
    assert second.idempotent is True
    assert first.snapshot.version_identifier == COMMIT
    assert first.snapshot.metadata == {
        "repository": "https://public.example/acme/payments.git",
        "branch": "main",
        "commit": COMMIT,
        "ingested_at": "2026-10-01T10:00:00+00:00",
        "files_seen": 7,
        "files_ingested": 5,
        "files_skipped_binary": 1,
        "files_skipped_secret": 1,
    }
    assert len(store.documents) == 5
    auth = next(chunk for chunk in store.chunks.values() if chunk.locator.path == "src/auth.py")
    assert auth.locator.commit == COMMIT
    assert (auth.locator.line_start, auth.locator.line_end) == (1, 3)
    assert auth.metadata["repository"] == "https://public.example/acme/payments.git"
    assert auth.metadata["path"] == "src/auth.py"
    assert auth.metadata["language"] == "python"
    assert auth.metadata["symbol"] is None
    function = next(
        chunk
        for chunk in store.chunks.values()
        if chunk.locator.path == "src/auth.py" and chunk.metadata["symbol"] == "authenticate"
    )
    assert (function.locator.line_start, function.locator.line_end) == (4, 7)
    assert function.embedding_model == "local-test"
    assert function.embedding_dimensions == 3

    clone = next(values for values in runner.commands if "clone" in values)
    assert "--no-checkout" in clone
    assert "--no-recurse-submodules" in clone
    assert not any(
        forbidden in values
        for values in runner.commands
        for forbidden in ("checkout", "submodule", "hook", "make", "npm", "python")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("uri", "addresses", "message"),
    [
        ("https://localhost/acme/payments.git", ("93.184.216.34",), "not permitted"),
        ("https://10.0.0.8/acme/payments.git", ("10.0.0.8",), "forbidden address"),
        (
            "https://169.254.169.254/latest/meta-data",
            ("169.254.169.254",),
            "forbidden address",
        ),
        ("git://public.example/acme/payments.git", ("93.184.216.34",), "only https"),
    ],
)
async def test_repository_provider_rejects_private_cloud_and_unsupported_network_targets(
    uri: str, addresses: tuple[str, ...], message: str
) -> None:
    runner = FixtureGitRunner({"README.md": b"read me"})

    async def resolve(_host: str, _port: int) -> tuple[str, ...]:
        return addresses

    with pytest.raises(SourceProviderError, match=message):
        await GitSourceProvider(resolver=resolve, runner=runner).extract_repository(
            source(uri), payload()
        )
    assert runner.commands == []


@pytest.mark.asyncio
async def test_repository_limits_and_secret_filtering_are_enforced_before_blob_reads() -> None:
    files = {
        f"src/{index}.py": b"def useful() -> None:\n    pass\n"
        for index in range(MAX_REPOSITORY_FILES + 1)
    }
    runner = FixtureGitRunner(files)
    with pytest.raises(SourceProviderError, match="file-count limit"):
        await provider(runner).extract_repository(source(), payload())
    assert not any("show" in values for values in runner.commands)


@pytest.mark.asyncio
async def test_unknown_blob_sizes_use_bounded_metadata_check_and_explicit_commit_fetch() -> None:
    runner = FixtureGitRunner(
        {"src/auth.py": b"def authenticate() -> bool:\n    return True\n"}, True
    )
    explicit = source().model_copy(update={"trust_metadata": {"commit": COMMIT}})

    extracted = await provider(runner).extract_repository(explicit, payload())

    assert extracted.commit == COMMIT
    assert any("fetch" in values for values in runner.commands)
    assert any("cat-file" in values for values in runner.commands)
    assert extracted.files[0].path == "src/auth.py"


@pytest.mark.asyncio
async def test_oversized_repository_file_is_skipped_without_reading_its_blob() -> None:
    runner = FixtureGitRunner(
        {
            "README.md": b"# retained",
            "src/oversized.py": b"x" * (MAX_REPOSITORY_FILE_BYTES + 1),
        }
    )

    extracted = await provider(runner).extract_repository(source(), payload())

    assert [item.path for item in extracted.files] == ["README.md"]
    assert extracted.snapshot_metadata["files_skipped_binary"] == 1
    assert not any(values[-1].endswith(":src/oversized.py") for values in runner.commands)


@pytest.mark.asyncio
async def test_github_provider_accepts_only_one_public_repository_path() -> None:
    runner = FixtureGitRunner({"README.md": b"# Payments"})

    async def resolve(host: str, port: int) -> tuple[str, ...]:
        assert (host, port) == ("github.com", 443)
        return ("140.82.112.4",)

    github = GitHubSourceProvider(
        git_provider=GitSourceProvider(resolver=resolve, runner=runner, clock=lambda: NOW)
    )
    github_source = source("https://github.com/acme/payments.git").model_copy(
        update={"source_type": "github"}
    )

    extracted = await github.extract_repository(github_source, payload())
    assert extracted.repository == "https://github.com/acme/payments.git"
    assert extracted.commit == COMMIT

    malformed = github_source.model_copy(
        update={
            "uri": "https://github.com/acme/payments/tree/main",
            "canonical_uri": "https://github.com/acme/payments/tree/main",
        }
    )
    with pytest.raises(SourceProviderError, match="exactly one owner"):
        await github.extract_repository(malformed, payload())

    redirected_host = github_source.model_copy(
        update={
            "uri": "https://www.github.com/acme/payments.git",
            "canonical_uri": "https://www.github.com/acme/payments.git",
        }
    )
    with pytest.raises(SourceProviderError, match="GitHub sources"):
        await github.extract_repository(redirected_host, payload())


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
