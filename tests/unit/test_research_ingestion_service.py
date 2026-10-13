"""V3 Phase 3 safe manual/file/PDF ingestion service tests."""

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from app.embeddings.types import EmbeddingVector
from app.models.research import (
    IngestionJobDocument,
    IngestionJobState,
    ResearchAuthorization,
    ResearchChunkDocument,
    ResearchDocumentDocument,
    ResearchSourceDocument,
    SourceSnapshotDocument,
)
from app.repositories.errors import DuplicateDocumentError
from app.research.providers import MAX_INGESTION_BYTES, FileSourceProvider, IngestionPayload
from app.services.research_ingestion_service import (
    ResearchIngestionCapacityError,
    ResearchIngestionInputError,
    ResearchIngestionLimiter,
    ResearchIngestionService,
)

PROJECT = "payments-api"
RESEARCH = "payment-evidence"
SOURCE = "retry-guide"


@dataclass
class Store:
    snapshots: dict[tuple[str, str], SourceSnapshotDocument] = field(default_factory=dict)
    documents: dict[tuple[str, str, str], ResearchDocumentDocument] = field(default_factory=dict)
    chunks: dict[tuple[str, str], ResearchChunkDocument] = field(default_factory=dict)
    jobs: dict[str, IngestionJobDocument] = field(default_factory=dict)

    def snapshots_for(self, project: str) -> "Snapshots":
        return Snapshots(self, project)

    def documents_for(self, project: str) -> "Documents":
        return Documents(self, project)

    def chunks_for(self, project: str) -> "Chunks":
        return Chunks(self, project)

    def jobs_for(self, project: str) -> "Jobs":
        return Jobs(self, project)


class Snapshots:
    def __init__(self, store: Store, project: str) -> None:
        self.store = store
        self.project = project

    async def create(self, document: SourceSnapshotDocument) -> SourceSnapshotDocument:
        key = (document.source_id, document.snapshot_id)
        if key in self.store.snapshots:
            raise DuplicateDocumentError("snapshot exists")
        self.store.snapshots[key] = document
        return document

    async def get(self, source_id: str, snapshot_id: str) -> SourceSnapshotDocument | None:
        return self.store.snapshots.get((source_id, snapshot_id))

    async def find_by_source_and_content_hash(
        self, source_id: str, content_hash: str
    ) -> SourceSnapshotDocument | None:
        return next(
            (
                snapshot
                for snapshot in self.store.snapshots.values()
                if snapshot.research_project_id == self.project
                and snapshot.source_id == source_id
                and snapshot.content_hash == content_hash
            ),
            None,
        )


class Documents:
    def __init__(self, store: Store, project: str) -> None:
        self.store = store
        self.project = project

    async def create(self, document: ResearchDocumentDocument) -> ResearchDocumentDocument:
        key = (document.source_id, document.snapshot_id, document.document_id)
        if key in self.store.documents:
            raise DuplicateDocumentError("document exists")
        self.store.documents[key] = document
        return document

    async def get(
        self, source_id: str, snapshot_id: str, document_id: str
    ) -> ResearchDocumentDocument | None:
        return self.store.documents.get((source_id, snapshot_id, document_id))


class Chunks:
    def __init__(self, store: Store, project: str) -> None:
        self.store = store
        self.project = project

    async def create(self, document: ResearchChunkDocument) -> ResearchChunkDocument:
        key = (document.document_id, document.chunk_id)
        if key in self.store.chunks:
            raise DuplicateDocumentError("chunk exists")
        self.store.chunks[key] = document
        return document

    async def get(self, document_id: str, chunk_id: str) -> ResearchChunkDocument | None:
        return self.store.chunks.get((document_id, chunk_id))


class Jobs:
    def __init__(self, store: Store, project: str) -> None:
        self.store = store
        self.project = project

    async def create(self, document: IngestionJobDocument) -> IngestionJobDocument:
        if document.job_id in self.store.jobs:
            raise DuplicateDocumentError("job exists")
        self.store.jobs[document.job_id] = document
        return document

    async def get(self, job_id: str) -> IngestionJobDocument | None:
        return self.store.jobs.get(job_id)

    async def update(
        self, document: IngestionJobDocument, *, expected_revision: int
    ) -> IngestionJobDocument:
        current = self.store.jobs[document.job_id]
        assert current.revision == expected_revision
        updated = document.model_copy(update={"revision": expected_revision + 1})
        self.store.jobs[document.job_id] = updated
        return updated


class Embeddings:
    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        return tuple(
            EmbeddingVector(model="local-test", dimensions=3, values=(0.25, 0.5, 0.75))
            for _ in contents
        )


def source(source_type: str = "manual") -> ResearchSourceDocument:
    uri = "manual:retry-guide" if source_type == "manual" else "file:///evidence/retry-guide"
    return ResearchSourceDocument(
        research_project_id=RESEARCH,
        source_id=SOURCE,
        source_type=source_type,
        uri=uri,
        canonical_uri=uri,
        title="Retry guide",
        source_class="user_provided",
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer"),
    )


def service(
    store: Store,
    *,
    embeddings: Embeddings | None = None,
    limiter: ResearchIngestionLimiter | None = None,
    max_chunks_per_ingestion: int = 500,
) -> ResearchIngestionService:
    return ResearchIngestionService(
        store.snapshots_for,
        store.documents_for,
        store.chunks_for,
        store.jobs_for,
        embeddings,
        limiter=limiter,
        max_chunks_per_ingestion=max_chunks_per_ingestion,
    )


@pytest.mark.asyncio
async def test_manual_markdown_ingestion_preserves_heading_lines_provenance_and_embeddings() -> (
    None
):
    store = Store()
    result = await service(store, embeddings=Embeddings()).ingest(
        source=source(),
        payload=IngestionPayload(
            data=b"# Overview\nRetries need explicit keys.\n\n## Limits\nRetry after backoff.",
            content_type="text/markdown",
            title="Retry guide",
            language="en",
        ),
        manually_provided=True,
    )

    assert result.job.status is IngestionJobState.COMPLETED
    assert result.job.documents_created == 1
    assert result.job.chunks_created == 2
    document = next(iter(store.documents.values()))
    chunks = sorted(store.chunks.values(), key=lambda item: item.chunk_index)
    assert document.content_type == "text/markdown"
    assert chunks[0].section == "Overview"
    assert chunks[0].metadata["heading_path"] == ["Overview"]
    assert chunks[0].locator.path == "manual:retry-guide"
    assert chunks[0].locator.line_start == 1
    assert chunks[0].locator.line_end == 2
    assert chunks[1].metadata["heading_path"] == ["Overview", "Limits"]
    assert chunks[0].embedding_model == "local-test"
    assert chunks[0].embedding_dimensions == 3
    assert chunks[0].embedding == [0.25, 0.5, 0.75]


@pytest.mark.asyncio
async def test_file_plain_text_ingestion_preserves_line_locations_without_embedding_service() -> (
    None
):
    store = Store()
    result = await service(store).ingest(
        source=source("file"),
        payload=IngestionPayload(
            data=b"first line\nsecond line",
            content_type="text/plain",
            title="Retry guide",
            language="en",
        ),
        manually_provided=False,
    )

    chunk = next(iter(store.chunks.values()))
    assert result.job.status is IngestionJobState.COMPLETED
    assert chunk.locator.path == "file:///evidence/retry-guide"
    assert (chunk.locator.line_start, chunk.locator.line_end, chunk.locator.page) == (1, 2, None)
    assert chunk.embedding is None
    assert chunk.metadata["page"] is None


def pdf_bytes(text: str) -> bytes:
    body = f"BT\n({text}) Tj\nET".encode()
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>\nendobj\n"
        + f"4 0 obj\n<< /Length {len(body)} >>\nstream\n".encode()
        + body
        + b"\nendstream\nendobj\ntrailer << /Root 1 0 R >>\n%%EOF"
    )


@pytest.mark.asyncio
async def test_pdf_ingestion_extracts_page_citations_and_retains_source_locator() -> None:
    store = Store()
    result = await service(store, embeddings=Embeddings()).ingest(
        source=source("pdf"),
        payload=IngestionPayload(
            data=pdf_bytes("PDF retry requirement"),
            content_type="application/pdf",
            title="PDF retry guide",
            language="en",
            filename="retry.pdf",
        ),
        manually_provided=False,
    )

    document = next(iter(store.documents.values()))
    chunk = next(iter(store.chunks.values()))
    assert result.snapshot.metadata["filename"] == "retry.pdf"
    assert document.content == "PDF retry requirement"
    assert chunk.locator.path == "file:///evidence/retry-guide"
    assert chunk.locator.page == 1
    assert chunk.metadata["page"] == 1
    assert chunk.embedding_model == "local-test"


@pytest.mark.asyncio
async def test_malformed_and_oversized_file_are_refused_and_malformed_job_is_failed() -> None:
    store = Store()
    ingestion = service(store)
    with pytest.raises(ResearchIngestionInputError, match="readable PDF"):
        await ingestion.ingest(
            source=source("pdf"),
            payload=IngestionPayload(
                data=b"%PDF-1.4 broken",
                content_type="application/pdf",
                title="Broken",
                language="und",
            ),
            manually_provided=False,
        )
    assert next(iter(store.jobs.values())).status is IngestionJobState.FAILED

    with pytest.raises(Exception, match="size limit"):
        await FileSourceProvider().extract(
            source("file"),
            IngestionPayload(
                data=b"x" * (MAX_INGESTION_BYTES + 1),
                content_type="text/plain",
                title="Too large",
                language="und",
            ),
        )


@pytest.mark.asyncio
async def test_duplicate_content_is_idempotent_and_never_overwrites_snapshot_or_chunks() -> None:
    store = Store()
    ingestion = service(store, embeddings=Embeddings())
    payload = IngestionPayload(
        data=b"idempotency requires stable keys",
        content_type="text/plain",
        title="Retry",
        language="en",
    )

    first = await ingestion.ingest(source=source(), payload=payload, manually_provided=True)
    second = await ingestion.ingest(source=source(), payload=payload, manually_provided=True)

    assert first.idempotent is False
    assert second.idempotent is True
    assert second.job.job_id == first.job.job_id
    assert len(store.snapshots) == len(store.documents) == len(store.chunks) == len(store.jobs) == 1


@pytest.mark.asyncio
async def test_pdf_provider_refuses_non_pdf_mime_and_never_executes_input() -> None:
    with pytest.raises(Exception, match="requires application/pdf"):
        await FileSourceProvider().extract(
            source("pdf"),
            IngestionPayload(
                data=b"not executable",
                content_type="text/plain",
                title="Wrong MIME",
                language="und",
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "data", "message"),
    [
        ("../outside.md", b"safe text", "filename is unsafe"),
        ("archive.zip", b"PK\x03\x04compressed", "archive uploads"),
        ("program.exe", b"MZbinary", "executable uploads"),
        ("notes.txt", b"\x7fELFbinary", "executable uploads"),
    ],
)
async def test_file_provider_refuses_unsafe_names_archives_and_executables(
    filename: str, data: bytes, message: str
) -> None:
    with pytest.raises(Exception, match=message):
        await FileSourceProvider().extract(
            source("file"),
            IngestionPayload(
                data=data,
                content_type="text/plain",
                title="Unsafe input",
                language="und",
                filename=filename,
            ),
        )


@pytest.mark.asyncio
async def test_chunk_limit_fails_the_job_before_any_document_or_chunk_is_persisted() -> None:
    store = Store()
    with pytest.raises(ResearchIngestionInputError, match="chunk limit"):
        await service(store, max_chunks_per_ingestion=1).ingest(
            source=source(),
            payload=IngestionPayload(
                data=b"# One\nfirst\n\n# Two\nsecond",
                content_type="text/markdown",
                title="Two chunks",
                language="en",
            ),
            manually_provided=True,
        )

    assert len(store.jobs) == 1
    assert next(iter(store.jobs.values())).status is IngestionJobState.FAILED
    assert not store.documents
    assert not store.chunks


@pytest.mark.asyncio
async def test_saturated_ingestion_gate_refuses_work_before_creating_a_job() -> None:
    store = Store()
    limiter = ResearchIngestionLimiter(1)
    async with limiter.reserve():
        with pytest.raises(ResearchIngestionCapacityError, match="capacity"):
            await service(store, limiter=limiter).ingest(
                source=source(),
                payload=IngestionPayload(
                    data=b"bounded work",
                    content_type="text/plain",
                    title="Bounded",
                    language="en",
                ),
                manually_provided=True,
            )

    assert not store.snapshots
    assert not store.documents
    assert not store.chunks
    assert not store.jobs
