"""V3 Phase 3 constrained manual and uploaded-file ingestion.

This service is the only place that coordinates parsing, immutable snapshot
history, normalization, local embeddings, and job transitions. URL and Git
acquisition remain provider-scoped; it accepts no arbitrary URL, path, or shell
command and cannot execute repository content, claims, candidates, or promotion.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol, cast

from app.embeddings.types import EmbeddingVector
from app.models.common import Metadata
from app.models.job_authority import JobAuthority
from app.models.research import (
    CitationLocator,
    IngestionJobDocument,
    IngestionJobState,
    ResearchChunkDocument,
    ResearchDocumentDocument,
    ResearchRecordStatus,
    ResearchSourceDocument,
    ResearchSourceType,
    SourceSnapshotDocument,
)
from app.repositories.errors import DuplicateDocumentError
from app.research.chunking import (
    RepositoryCodeChunker,
    ResearchChunkDraft,
    ResearchChunkingError,
    ResearchDocumentChunker,
)
from app.research.providers import (
    ExtractedResearchDocument,
    FileSourceProvider,
    IngestionPayload,
    ManualSourceProvider,
    SourceProvider,
    SourceProviderError,
    URLSourceProvider,
)
from app.research.repository import (
    ExtractedRepository,
    GitHubSourceProvider,
    GitSourceProvider,
    RepositoryFile,
    RepositorySourceProvider,
)
from app.security.job_authority import (
    JobAuthorityError,
    MongoJobAuthorityChecker,
    job_authority_scope,
)
from app.security.tenant import require_tenant
from app.security.tenant_policy import current_policy, policy_allows
from app.security.tenant_service import tenant_service
from app.services.embedding_service import EmbeddingService


class ResearchIngestionError(Exception):
    """Base class for caller-safe Phase 3 ingestion outcomes."""


class UnsupportedResearchSourceError(ResearchIngestionError):
    """The registered source has no provider in this explicitly scoped phase."""


class ResearchIngestionInputError(ResearchIngestionError):
    """Submitted untrusted bytes could not be safely normalized."""


class ResearchIngestionJobNotFoundError(ResearchIngestionError):
    """A requested job is absent from its source's authorized project boundary."""


class ResearchIngestionCapacityError(ResearchIngestionError):
    """No bounded ingestion worker slot is currently available."""


class ResearchIngestionLimiter:
    """A shared, non-queueing gate for expensive untrusted-content work."""

    def __init__(self, maximum_concurrent_ingestions: int) -> None:
        if maximum_concurrent_ingestions < 1:
            raise ValueError("research ingestion concurrency must be positive")
        self._slots = asyncio.BoundedSemaphore(maximum_concurrent_ingestions)

    @asynccontextmanager
    async def reserve(self) -> AsyncIterator[None]:
        """Reserve immediately or fail; request handlers never queue unbounded work."""
        if self._slots.locked():
            raise ResearchIngestionCapacityError("research ingestion capacity is exhausted")
        await self._slots.acquire()
        try:
            yield
        finally:
            self._slots.release()


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """The durable outcome for one deterministic source-content ingestion attempt."""

    job: IngestionJobDocument
    snapshot: SourceSnapshotDocument
    idempotent: bool


class SourceSnapshotRepository(Protocol):
    """The immutable source-version writes Phase 3 needs."""

    async def create(self, document: SourceSnapshotDocument) -> SourceSnapshotDocument: ...

    async def get(self, source_id: str, snapshot_id: str) -> SourceSnapshotDocument | None: ...

    async def find_by_source_and_content_hash(
        self, source_id: str, content_hash: str
    ) -> SourceSnapshotDocument | None: ...


class ResearchDocumentRepository(Protocol):
    """Normalized document persistence, scoped by one research project."""

    async def create(self, document: ResearchDocumentDocument) -> ResearchDocumentDocument: ...

    async def get(
        self, source_id: str, snapshot_id: str, document_id: str
    ) -> ResearchDocumentDocument | None: ...


class ResearchChunkRepository(Protocol):
    """Citable research chunks, scoped by one research project."""

    async def create(self, document: ResearchChunkDocument) -> ResearchChunkDocument: ...

    async def get(self, document_id: str, chunk_id: str) -> ResearchChunkDocument | None: ...


class IngestionJobRepository(Protocol):
    """Persisted synchronous job lifecycle; no queue or worker is introduced."""

    async def create(self, document: IngestionJobDocument) -> IngestionJobDocument: ...

    async def get(self, job_id: str) -> IngestionJobDocument | None: ...

    async def update(
        self, document: IngestionJobDocument, *, expected_revision: int
    ) -> IngestionJobDocument: ...


@tenant_service
class ResearchIngestionService:
    """Store one safe source version, its normalized content, chunks, and vectors."""

    def __init__(
        self,
        snapshots: Callable[[str], SourceSnapshotRepository],
        documents: Callable[[str], ResearchDocumentRepository],
        chunks: Callable[[str], ResearchChunkRepository],
        jobs: Callable[[str], IngestionJobRepository],
        embeddings: EmbeddingService | None,
        *,
        manual_provider: SourceProvider | None = None,
        file_provider: SourceProvider | None = None,
        url_provider: SourceProvider | None = None,
        git_provider: RepositorySourceProvider | None = None,
        github_provider: RepositorySourceProvider | None = None,
        chunker: ResearchDocumentChunker | None = None,
        repository_chunker: RepositoryCodeChunker | None = None,
        limiter: ResearchIngestionLimiter | None = None,
        max_chunks_per_ingestion: int = 500,
        authority_checker: MongoJobAuthorityChecker | None = None,
    ) -> None:
        if max_chunks_per_ingestion < 1:
            raise ValueError("research chunk limit must be positive")
        self._authority_checker = authority_checker
        self._snapshots = snapshots
        self._documents = documents
        self._chunks = chunks
        self._jobs = jobs
        self._embeddings = embeddings
        self._manual_provider = manual_provider or ManualSourceProvider()
        self._file_provider = file_provider or FileSourceProvider()
        self._url_provider = url_provider or URLSourceProvider()
        self._git_provider = git_provider or GitSourceProvider()
        self._github_provider = github_provider or GitHubSourceProvider()
        self._chunker = chunker or ResearchDocumentChunker()
        self._repository_chunker = repository_chunker or RepositoryCodeChunker()
        self._limiter = limiter or ResearchIngestionLimiter(2)
        self._max_chunks_per_ingestion = max_chunks_per_ingestion

    async def ingest(
        self,
        *,
        source: ResearchSourceDocument,
        payload: IngestionPayload,
        manually_provided: bool,
    ) -> IngestionResult:
        """Safely retain exactly one immutable source observation.

        Registered URL and repository sources are acquired before their
        content-addressed snapshot/job identity is known. Manual and file
        submissions keep the established failed-job lifecycle. Repository
        acquisition remains read-only and provider-scoped: the service has no
        URL, filesystem, or command input of its own.
        """
        if source.org_id != require_tenant().org_id or not policy_allows(
            source.model_dump(by_alias=True), "research_sources"
        ):
            raise ResearchIngestionInputError("source is unavailable")
        authority = self._authority(source)

        async def recheck() -> None:
            await self._check_authority(authority)

        await recheck()
        if source.status is not ResearchRecordStatus.ACTIVE:
            raise ResearchIngestionInputError("only active sources can be ingested")
        provider = self._provider_for(source, manually_provided, payload)
        with job_authority_scope(recheck):
            async with self._limiter.reserve():
                return await self._ingest_with_provider(source, payload, provider)

    def _authority(self, source: ResearchSourceDocument) -> JobAuthority:
        policy = current_policy()
        return JobAuthority(
            worker_schema_version=policy.engine.schema_version if policy and policy.engine else 37,
            subject_kind="human"
            if policy and policy.managed and policy.subject_kind == "human"
            else "operator"
            if policy is None or policy.operator
            else "legacy",
            user_id=policy.subject_id
            if policy and policy.managed and policy.subject_kind == "human"
            else None,
            membership_revision=policy.subject_revision
            if policy and policy.managed and policy.subject_kind == "human"
            else None,
            session_id=policy.session_id
            if policy and policy.managed and policy.subject_kind == "human"
            else None,
            org_id=require_tenant().org_id,
            key_id=None
            if policy is None
            or policy.operator
            or (policy.managed and policy.subject_kind == "human")
            else policy.subject_id,
            client_id=policy.client_id if policy else None,
            project_ids=policy.selector("research:write", "project") if policy else None,
            profile_ids=policy.selector("research:write", "creator_profile") if policy else None,
            research_project_id=source.research_project_id,
            source_id=source.source_id,
        )

    async def _check_authority(self, authority: JobAuthority | None) -> None:
        if authority is None or authority.org_id != require_tenant().org_id:
            raise JobAuthorityError("job initiating authority is unavailable")
        if self._authority_checker is None:
            if authority.key_id is not None:
                raise JobAuthorityError("job authority rechecker is not configured")
            return
        await self._authority_checker.check(authority)

    async def _ingest_with_provider(
        self,
        source: ResearchSourceDocument,
        payload: IngestionPayload,
        provider: SourceProvider | RepositorySourceProvider,
    ) -> IngestionResult:
        """Run the bounded work after a shared capacity slot has been reserved."""
        await self._check_authority(self._authority(source))
        extracted: ExtractedResearchDocument | ExtractedRepository | None = None
        if payload.acquire_url or payload.acquire_repository:
            try:
                extracted = await self._acquire(provider, source, payload)
            except SourceProviderError as error:
                raise ResearchIngestionInputError(str(error)) from error
            if isinstance(extracted, ExtractedRepository):
                snapshot = await self._snapshot_for(
                    source,
                    content_hash=extracted.source_hash,
                    retrieved_at=extracted.retrieved_at,
                    version_identifier=extracted.commit,
                    metadata=extracted.snapshot_metadata,
                )
            else:
                snapshot = await self._snapshot_for(
                    source,
                    content_hash=extracted.source_hash,
                    retrieved_at=extracted.retrieved_at or datetime.now(UTC),
                    version_identifier=extracted.version_identifier,
                    metadata=extracted.snapshot_metadata or {},
                )
        else:
            snapshot = await self._snapshot_for(
                source,
                content_hash=payload.source_hash,
                retrieved_at=datetime.now(UTC),
                version_identifier=None,
                metadata=_snapshot_metadata(payload),
            )

        jobs = self._jobs(source.research_project_id)
        job_id = _job_id(source.source_id, snapshot.content_hash)
        existing = await jobs.get(job_id)
        if existing is not None:
            await self._check_authority(existing.initiating_authority)
            return IngestionResult(
                job=existing,
                snapshot=snapshot,
                idempotent=existing.status is IngestionJobState.COMPLETED,
            )

        pending = IngestionJobDocument(
            initiating_authority=self._authority(source),
            research_project_id=source.research_project_id,
            job_id=job_id,
            source_id=source.source_id,
            snapshot_id=snapshot.snapshot_id,
            authorization=source.authorization,
        )
        try:
            job = await jobs.create(pending)
        except DuplicateDocumentError:
            concurrent = await jobs.get(job_id)
            if concurrent is None:
                raise
            await self._check_authority(concurrent.initiating_authority)
            return IngestionResult(
                job=concurrent,
                snapshot=snapshot,
                idempotent=concurrent.status is IngestionJobState.COMPLETED,
            )
        job = await self._running(jobs, job)
        try:
            normalized = extracted or await self._acquire(provider, source, payload)
            if isinstance(normalized, ExtractedRepository):
                repository_drafts = tuple(
                    (file, draft)
                    for file in normalized.files
                    for draft in self._repository_chunker.chunk(
                        path=file.path,
                        language=file.language,
                        content_type=file.content_type,
                        content=file.content,
                    )
                )
                self._enforce_chunk_limit(len(repository_drafts))
                vectors = await self._embed(tuple(draft.content for _, draft in repository_drafts))
                created_documents, created_chunks = await self._persist_repository(
                    source=source,
                    snapshot=snapshot,
                    extracted=normalized,
                    drafts=repository_drafts,
                    vectors=vectors,
                )
            else:
                drafts = self._chunker.chunk(
                    content_type=normalized.content_type,
                    pages=normalized.pages,
                )
                self._enforce_chunk_limit(len(drafts))
                vectors = await self._embed(tuple(draft.content for draft in drafts))
                created_documents, created_chunks = await self._persist(
                    source=source,
                    snapshot=snapshot,
                    extracted=normalized,
                    drafts=drafts,
                    vectors=vectors,
                )
        except (SourceProviderError, ResearchChunkingError) as error:
            await self._failed(jobs, job, str(error))
            raise ResearchIngestionInputError(str(error)) from error
        except Exception as error:
            await self._failed(jobs, job, "normalization, chunking, or embedding failed")
            raise ResearchIngestionError("research ingestion failed") from error

        completed = await self._completed(
            jobs,
            job,
            documents_created=created_documents,
            chunks_created=created_chunks,
        )
        await self._check_authority(completed.initiating_authority)
        return IngestionResult(job=completed, snapshot=snapshot, idempotent=False)

    def _enforce_chunk_limit(self, count: int) -> None:
        if count > self._max_chunks_per_ingestion:
            raise ResearchChunkingError("research document exceeds the ingestion chunk limit")

    async def _acquire(
        self,
        provider: SourceProvider | RepositorySourceProvider,
        source: ResearchSourceDocument,
        payload: IngestionPayload,
    ) -> ExtractedResearchDocument | ExtractedRepository:
        if payload.acquire_repository:
            repository_provider = cast(RepositorySourceProvider, provider)
            return await repository_provider.extract_repository(source, payload)
        return await cast(SourceProvider, provider).extract(source, payload)

    def _provider_for(
        self,
        source: ResearchSourceDocument,
        manually_provided: bool,
        payload: IngestionPayload,
    ) -> SourceProvider | RepositorySourceProvider:
        if source.source_type is ResearchSourceType.URL:
            if manually_provided or not payload.acquire_url or payload.acquire_repository:
                raise UnsupportedResearchSourceError(
                    "a URL source requires explicit registered URL acquisition"
                )
            return self._url_provider
        if source.source_type is ResearchSourceType.GIT:
            if manually_provided or not payload.acquire_repository or payload.acquire_url:
                raise UnsupportedResearchSourceError(
                    "a Git source requires explicit registered repository acquisition"
                )
            return self._git_provider
        if source.source_type is ResearchSourceType.GITHUB:
            if manually_provided or not payload.acquire_repository or payload.acquire_url:
                raise UnsupportedResearchSourceError(
                    "a GitHub source requires explicit registered repository acquisition"
                )
            return self._github_provider
        if payload.acquire_url or payload.acquire_repository:
            raise UnsupportedResearchSourceError(
                "registered URL or repository acquisition requires a matching source type"
            )
        provider = self._manual_provider if manually_provided else self._file_provider
        if not provider.supports(source.source_type):
            raise UnsupportedResearchSourceError(
                "this source type is not supported by manual or file ingestion"
            )
        return provider

    async def get_job(self, *, research_project_id: str, job_id: str) -> IngestionJobDocument:
        """Load a job only through its already-authorized research-project scope."""
        job = await self._jobs(research_project_id).get(job_id)
        if job is None:
            raise ResearchIngestionJobNotFoundError("research ingestion job was not found")
        try:
            await self._check_authority(job.initiating_authority)
        except JobAuthorityError as error:
            raise ResearchIngestionJobNotFoundError(
                "research ingestion job was not found"
            ) from error
        return job

    async def _snapshot_for(
        self,
        source: ResearchSourceDocument,
        *,
        content_hash: str,
        retrieved_at: datetime,
        version_identifier: str | None,
        metadata: Metadata,
    ) -> SourceSnapshotDocument:
        snapshots = self._snapshots(source.research_project_id)
        existing = await snapshots.find_by_source_and_content_hash(source.source_id, content_hash)
        if existing is not None:
            return existing
        snapshot = SourceSnapshotDocument(
            research_project_id=source.research_project_id,
            source_id=source.source_id,
            snapshot_id=f"snapshot-{content_hash}",
            retrieved_at=retrieved_at,
            content_hash=content_hash,
            version_identifier=version_identifier or f"sha256:{content_hash}",
            metadata=metadata,
            authorization=source.authorization,
        )
        try:
            return await snapshots.create(snapshot)
        except DuplicateDocumentError:
            concurrent = await snapshots.find_by_source_and_content_hash(
                source.source_id, content_hash
            )
            if concurrent is None:
                raise
            return concurrent

    async def _embed(self, contents: Sequence[str]) -> tuple[EmbeddingVector | None, ...]:
        if self._embeddings is None:
            return tuple(None for _ in contents)
        return tuple(await self._embeddings.embed_chunks(contents))

    async def _persist(
        self,
        *,
        source: ResearchSourceDocument,
        snapshot: SourceSnapshotDocument,
        extracted: ExtractedResearchDocument,
        drafts: Sequence[ResearchChunkDraft],
        vectors: Sequence[EmbeddingVector | None],
    ) -> tuple[int, int]:
        # Kept deliberately after parsing/embedding: malformed input or a local
        # model failure cannot leave a partial document behind.
        if len(vectors) != len(drafts):
            raise RuntimeError("ingestion drafts and embeddings are inconsistent")

        document_id = _document_id(extracted.content_hash)
        documents = self._documents(source.research_project_id)
        existing = await documents.get(source.source_id, snapshot.snapshot_id, document_id)
        documents_created = 0
        if existing is None:
            document = ResearchDocumentDocument(
                research_project_id=source.research_project_id,
                source_id=source.source_id,
                snapshot_id=snapshot.snapshot_id,
                document_id=document_id,
                title=extracted.title,
                content_type=extracted.content_type,
                language=extracted.language,
                content=extracted.content,
                content_hash=extracted.content_hash,
                source_class=source.source_class,
                authorization=source.authorization,
            )
            try:
                await documents.create(document)
                documents_created = 1
            except DuplicateDocumentError:
                pass

        chunks = self._chunks(source.research_project_id)
        chunks_created = 0
        embedding_time = datetime.now(UTC)
        for index, (draft, vector) in enumerate(zip(drafts, vectors, strict=True)):
            chunk_id = f"chunk-{index:05d}"
            if await chunks.get(document_id, chunk_id) is not None:
                continue
            locator = CitationLocator(
                url=extracted.locator_url,
                anchor=draft.anchor,
                path=None if extracted.locator_url is not None else source.uri,
                page=draft.page,
                line_start=draft.line_start,
                line_end=draft.line_end,
            )
            metadata: Metadata = {
                "heading_path": list(draft.heading_path),
                "heading_anchor": draft.anchor,
                "page": draft.page,
                "line_start": draft.line_start,
                "line_end": draft.line_end,
                "content_type": extracted.content_type,
            }
            chunk_fields = {
                "research_project_id": source.research_project_id,
                "source_id": source.source_id,
                "snapshot_id": snapshot.snapshot_id,
                "document_id": document_id,
                "chunk_id": chunk_id,
                "chunk_index": index,
                "section": draft.section,
                "content": draft.content,
                "source_class": source.source_class,
                "locator": locator,
                "metadata": metadata,
                "authorization": source.authorization,
            }
            if vector is not None:
                chunk_fields.update(
                    {
                        "embedding": list(vector.values),
                        "embedding_model": vector.model,
                        "embedding_dimensions": vector.dimensions,
                        "embedding_generated_at": embedding_time,
                    }
                )
            chunk = ResearchChunkDocument.model_validate(chunk_fields)
            try:
                await chunks.create(chunk)
                chunks_created += 1
            except DuplicateDocumentError:
                continue
        return documents_created, chunks_created

    async def _persist_repository(
        self,
        *,
        source: ResearchSourceDocument,
        snapshot: SourceSnapshotDocument,
        extracted: ExtractedRepository,
        drafts: Sequence[tuple[RepositoryFile, ResearchChunkDraft]],
        vectors: Sequence[EmbeddingVector | None],
    ) -> tuple[int, int]:
        """Persist repository files as separate citable documents without mutation."""
        if len(drafts) != len(vectors):
            raise RuntimeError("repository ingestion drafts and embeddings are inconsistent")
        documents = self._documents(source.research_project_id)
        chunks = self._chunks(source.research_project_id)
        vectors_by_path: dict[str, list[tuple[ResearchChunkDraft, EmbeddingVector | None]]] = {}
        for (file, draft), vector in zip(drafts, vectors, strict=True):
            vectors_by_path.setdefault(file.path, []).append((draft, vector))
        documents_created = 0
        chunks_created = 0
        embedding_time = datetime.now(UTC)
        for file in extracted.files:
            document_id = _repository_document_id(file.path, file.content_hash)
            existing = await documents.get(source.source_id, snapshot.snapshot_id, document_id)
            if existing is None:
                document = ResearchDocumentDocument(
                    research_project_id=source.research_project_id,
                    source_id=source.source_id,
                    snapshot_id=snapshot.snapshot_id,
                    document_id=document_id,
                    title=_repository_document_title(source.title, file.path),
                    content_type=file.content_type,
                    language=file.language,
                    content=file.content,
                    content_hash=file.content_hash,
                    source_class=source.source_class,
                    authorization=source.authorization,
                )
                try:
                    await documents.create(document)
                    documents_created += 1
                except DuplicateDocumentError:
                    pass
            for index, (draft, vector) in enumerate(vectors_by_path.get(file.path, ())):
                chunk_id = f"chunk-{index:05d}"
                if await chunks.get(document_id, chunk_id) is not None:
                    continue
                metadata: Metadata = {
                    "heading_path": list(draft.heading_path),
                    "heading_anchor": draft.anchor,
                    "page": None,
                    "line_start": draft.line_start,
                    "line_end": draft.line_end,
                    "content_type": file.content_type,
                    "repository": extracted.repository,
                    "branch": extracted.branch,
                    "commit": extracted.commit,
                    "path": file.path,
                    "language": file.language,
                    "symbol": draft.symbol,
                }
                if extracted.tag is not None:
                    metadata["tag"] = extracted.tag
                chunk_fields: dict[str, object] = {
                    "research_project_id": source.research_project_id,
                    "source_id": source.source_id,
                    "snapshot_id": snapshot.snapshot_id,
                    "document_id": document_id,
                    "chunk_id": chunk_id,
                    "chunk_index": index,
                    "section": draft.section,
                    "content": draft.content,
                    "source_class": source.source_class,
                    "locator": CitationLocator(
                        path=file.path,
                        page=None,
                        line_start=draft.line_start,
                        line_end=draft.line_end,
                        commit=extracted.commit,
                    ),
                    "metadata": metadata,
                    "authorization": source.authorization,
                }
                if vector is not None:
                    chunk_fields.update(
                        {
                            "embedding": list(vector.values),
                            "embedding_model": vector.model,
                            "embedding_dimensions": vector.dimensions,
                            "embedding_generated_at": embedding_time,
                        }
                    )
                chunk = ResearchChunkDocument.model_validate(chunk_fields)
                try:
                    await chunks.create(chunk)
                    chunks_created += 1
                except DuplicateDocumentError:
                    continue
        return documents_created, chunks_created

    async def _running(
        self, repository: IngestionJobRepository, job: IngestionJobDocument
    ) -> IngestionJobDocument:
        return await repository.update(
            _updated_job(job, status=IngestionJobState.RUNNING, started_at=datetime.now(UTC)),
            expected_revision=job.revision,
        )

    async def _completed(
        self,
        repository: IngestionJobRepository,
        job: IngestionJobDocument,
        *,
        documents_created: int,
        chunks_created: int,
    ) -> IngestionJobDocument:
        return await repository.update(
            _updated_job(
                job,
                status=IngestionJobState.COMPLETED,
                completed_at=datetime.now(UTC),
                error=None,
                documents_created=documents_created,
                chunks_created=chunks_created,
            ),
            expected_revision=job.revision,
        )

    async def _failed(
        self, repository: IngestionJobRepository, job: IngestionJobDocument, detail: str
    ) -> IngestionJobDocument:
        return await repository.update(
            _updated_job(
                job,
                status=IngestionJobState.FAILED,
                completed_at=datetime.now(UTC),
                error=detail[:4_000],
            ),
            expected_revision=job.revision,
        )


def _snapshot_metadata(payload: IngestionPayload) -> Metadata:
    metadata: Metadata = {"content_type": payload.content_type}
    if payload.filename is not None:
        metadata["filename"] = payload.filename
    return metadata


def _job_id(source_id: str, content_hash: str) -> str:
    digest = sha256(f"{source_id}\x00{content_hash}".encode()).hexdigest()
    return f"ingest-{digest}"


def _document_id(content_hash: str) -> str:
    return f"document-{content_hash}"


def _repository_document_id(path: str, content_hash: str) -> str:
    digest = sha256(f"{path}\x00{content_hash}".encode()).hexdigest()
    return f"document-{digest}"


def _repository_document_title(source_title: str, path: str) -> str:
    title = f"{source_title} — {path}"
    return title[:500].rstrip()


def _updated_job(job: IngestionJobDocument, **changes: object) -> IngestionJobDocument:
    raw = job.model_dump(by_alias=True, mode="python")
    raw.update(changes)
    return IngestionJobDocument.model_validate(raw, context={"from_storage": True})


__all__ = [
    "IngestionResult",
    "ResearchIngestionCapacityError",
    "ResearchIngestionError",
    "ResearchIngestionInputError",
    "ResearchIngestionJobNotFoundError",
    "ResearchIngestionLimiter",
    "ResearchIngestionService",
    "UnsupportedResearchSourceError",
]
