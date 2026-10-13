"""V3 Phase 2 unit tests for inert source registration and snapshots."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from app.models.common import Page
from app.models.research import (
    ResearchAuthorization,
    ResearchProjectDocument,
    ResearchRecordStatus,
    ResearchSourceDocument,
    ResearchSourceType,
    SourceClass,
    SourceSnapshotDocument,
)
from app.repositories.errors import DuplicateDocumentError
from app.research.errors import ResearchSourceAlreadyExistsError, SourceSnapshotAlreadyExistsError
from app.research.uri import InvalidResearchSourceUriError, canonicalize_source_uri
from app.services.research_source_service import (
    ResearchSourceLimitError,
    ResearchSourceService,
    SourceSnapshotRegistration,
)
from tests.research_doubles import FakeResearchProjectRepository

PROJECT = "payments-api"
RESEARCH_PROJECT = "source-registry"
WHEN = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)
HASH = "a" * 64


class SourceStore:
    """In-memory Phase 2 persistence with the same project and natural-key boundaries."""

    def __init__(self) -> None:
        self.sources: dict[str, ResearchSourceDocument] = {}
        self.snapshots: dict[tuple[str, str], SourceSnapshotDocument] = {}

    def source_repository(self, research_project_id: str) -> "FakeSourceRepository":
        return FakeSourceRepository(self, research_project_id)

    def snapshot_repository(self, research_project_id: str) -> "FakeSnapshotRepository":
        return FakeSnapshotRepository(self, research_project_id)

    async def get(self, source_id: str) -> ResearchSourceDocument | None:
        return self.sources.get(source_id)


class FakeSourceRepository:
    def __init__(self, store: SourceStore, research_project_id: str) -> None:
        self.store = store
        self.research_project_id = research_project_id

    async def create(self, document: ResearchSourceDocument) -> ResearchSourceDocument:
        if document.source_id in self.store.sources:
            raise DuplicateDocumentError("source exists")
        self.store.sources[document.source_id] = document
        return document

    async def get(self, source_id: str) -> ResearchSourceDocument | None:
        document = self.store.sources.get(source_id)
        if document is None or document.research_project_id != self.research_project_id:
            return None
        return document

    async def count_up_to(self, maximum: int) -> int:
        return min(
            maximum,
            sum(
                record.research_project_id == self.research_project_id
                for record in self.store.sources.values()
            ),
        )

    async def list(
        self,
        *,
        status: ResearchRecordStatus | None = None,
        source_class: SourceClass | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ResearchSourceDocument]:
        del cursor
        records = [
            record
            for record in self.store.sources.values()
            if record.research_project_id == self.research_project_id
            and (status is None or record.status == status)
            and (source_class is None or record.source_class == source_class)
            and (
                allowed_project_ids is None
                or set(record.authorization.project_ids).issubset(allowed_project_ids)
            )
        ]
        records.sort(key=lambda item: (item.created_at, item.id))
        return Page(items=tuple(records[:limit]))

    async def find_by_canonical_uri(self, canonical_uri: str) -> ResearchSourceDocument | None:
        return next(
            (
                record
                for record in self.store.sources.values()
                if record.research_project_id == self.research_project_id
                and record.canonical_uri == canonical_uri
            ),
            None,
        )

    async def find_by_metadata(
        self,
        *,
        source_type: ResearchSourceType,
        title: str,
        publisher: str | None,
        author: str | None,
        published_at: datetime | None,
    ) -> ResearchSourceDocument | None:
        return next(
            (
                record
                for record in self.store.sources.values()
                if record.research_project_id == self.research_project_id
                and record.source_type == source_type
                and record.title == title
                and record.publisher == publisher
                and record.author == author
                and record.published_at == published_at
            ),
            None,
        )


class FakeSnapshotRepository:
    def __init__(self, store: SourceStore, research_project_id: str) -> None:
        self.store = store
        self.research_project_id = research_project_id

    async def create(self, document: SourceSnapshotDocument) -> SourceSnapshotDocument:
        key = (document.source_id, document.snapshot_id)
        if key in self.store.snapshots:
            raise DuplicateDocumentError("snapshot exists")
        self.store.snapshots[key] = document
        return document

    async def get(self, source_id: str, snapshot_id: str) -> SourceSnapshotDocument | None:
        document = self.store.snapshots.get((source_id, snapshot_id))
        if document is None or document.research_project_id != self.research_project_id:
            return None
        return document

    async def list(
        self,
        *,
        source_id: str | None = None,
        status: ResearchRecordStatus | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[SourceSnapshotDocument]:
        del cursor
        records = [
            record
            for record in self.store.snapshots.values()
            if record.research_project_id == self.research_project_id
            and (source_id is None or record.source_id == source_id)
            and (status is None or record.status == status)
        ]
        records.sort(key=lambda item: (item.created_at, item.id))
        return Page(items=tuple(records[:limit]))

    async def find_by_content_hash(self, content_hash: str) -> SourceSnapshotDocument | None:
        return next(
            (
                record
                for record in self.store.snapshots.values()
                if record.research_project_id == self.research_project_id
                and record.content_hash == content_hash
            ),
            None,
        )


def research_project(related: Sequence[str] = (PROJECT,)) -> ResearchProjectDocument:
    return ResearchProjectDocument(
        research_project_id=RESEARCH_PROJECT,
        name="Source registry",
        question="Which sources describe retry semantics?",
        type="technical_research",
        related_project_ids=list(related),
        authorization=ResearchAuthorization(project_ids=list(related), owner_key_id="owner-key"),
    )


def service(
    related: Sequence[str] = (PROJECT,), *, max_sources_per_project: int = 500
) -> tuple[ResearchSourceService, SourceStore]:
    store = SourceStore()
    return (
        ResearchSourceService(
            FakeResearchProjectRepository([research_project(related)]),
            store,
            store.source_repository,
            store.snapshot_repository,
            max_sources_per_project=max_sources_per_project,
        ),
        store,
    )


def snapshot(
    snapshot_id: str = "snapshot-1", content_hash: str = HASH
) -> SourceSnapshotRegistration:
    return SourceSnapshotRegistration(
        snapshot_id=snapshot_id,
        retrieved_at=WHEN,
        content_hash=content_hash,
        version_identifier="etag-1",
        metadata={"etag": "etag-1"},
    )


async def add(
    registry: ResearchSourceService,
    *,
    source_id: str = "provider-docs",
    source_type: ResearchSourceType = ResearchSourceType.URL,
    uri: str = "https://example.test/retries",
    title: str = "Provider retry guide",
    source_class: SourceClass = SourceClass.OFFICIAL_DOCUMENTATION,
    initial_snapshot: SourceSnapshotRegistration | None = None,
) -> ResearchSourceDocument:
    registered = await registry.create_source(
        research_project_id=RESEARCH_PROJECT,
        source_id=source_id,
        source_type=source_type,
        uri=uri,
        title=title,
        publisher="Example",
        author="Documentation team",
        published_at=WHEN,
        retrieved_at=WHEN,
        source_class=source_class,
        trust_metadata={"origin": "caller-provided"},
        status=ResearchRecordStatus.ACTIVE,
        owner_key_id="writer-key",
        snapshot=initial_snapshot,
    )
    return registered.source


@pytest.mark.asyncio
async def test_source_registration_normalizes_url_preserves_provenance_and_appends_snapshot() -> (
    None
):
    registry, _ = service()
    source = await add(
        registry,
        uri="HTTPS://Example.TEST:443/guides/../retries/?utm_source=weekly&b=2&a=1#part",
        initial_snapshot=snapshot(),
    )

    snapshots = await registry.list_snapshots(
        research_project_id=RESEARCH_PROJECT, source_id=source.source_id
    )

    assert source.uri.startswith("HTTPS://")
    assert source.canonical_uri == "https://example.test/retries/?a=1&b=2"
    assert source.authorization.project_ids == [PROJECT]
    assert source.authorization.owner_key_id == "writer-key"
    assert len(snapshots.items) == 1
    assert snapshots.items[0].content_hash == HASH
    assert snapshots.items[0].metadata == {"etag": "etag-1"}


@pytest.mark.asyncio
async def test_duplicate_detection_uses_canonical_uri_metadata_and_supplied_content_hash() -> None:
    registry, _ = service()
    await add(registry, initial_snapshot=snapshot())

    with pytest.raises(ResearchSourceAlreadyExistsError, match="canonical"):
        await add(
            registry,
            source_id="provider-docs-duplicate",
            uri="https://EXAMPLE.test:443/retries#latest",
            title="Different title does not bypass URI duplicate detection",
        )
    await add(
        registry,
        source_id="manual-metadata-original",
        source_type=ResearchSourceType.MANUAL,
        uri="manual:retry-guide-original",
    )
    with pytest.raises(ResearchSourceAlreadyExistsError, match="metadata"):
        await add(
            registry,
            source_id="manual-metadata-duplicate",
            source_type=ResearchSourceType.MANUAL,
            uri="manual:retry-guide-copy",
        )
    with pytest.raises(ResearchSourceAlreadyExistsError, match="content hash"):
        await add(
            registry,
            source_id="manual-content-duplicate",
            source_type=ResearchSourceType.MANUAL,
            uri="manual:another-guide",
            title="Different manual guide",
            initial_snapshot=snapshot("snapshot-2"),
        )


@pytest.mark.asyncio
async def test_source_snapshots_are_append_only_and_source_class_is_only_descriptive_metadata() -> (
    None
):
    registry, store = service()
    source = await add(
        registry,
        source_type=ResearchSourceType.MANUAL,
        uri="manual:incident-timeline",
        source_class=SourceClass.GENERATED,
        initial_snapshot=snapshot(),
    )
    appended = await registry.create_snapshot(
        research_project_id=RESEARCH_PROJECT,
        source_id=source.source_id,
        snapshot=snapshot("snapshot-2", "b" * 64),
    )

    with pytest.raises(SourceSnapshotAlreadyExistsError):
        await registry.create_snapshot(
            research_project_id=RESEARCH_PROJECT,
            source_id=source.source_id,
            snapshot=snapshot("snapshot-1", "c" * 64),
        )

    assert source.source_class is SourceClass.GENERATED
    assert not hasattr(source, "reliable")
    assert appended.snapshot_id == "snapshot-2"
    assert store.snapshots[(source.source_id, "snapshot-1")].content_hash == HASH


@pytest.mark.parametrize(
    ("source_type", "uri", "canonical"),
    [
        (ResearchSourceType.MANUAL, "manual:interview-1", "manual:interview-1"),
        (ResearchSourceType.FILE, "file:///var/data/report.pdf", "file:///var/data/report.pdf"),
        (ResearchSourceType.GIT, "git://Example.TEST/repo.git", "git://example.test/repo.git"),
    ],
)
def test_supported_source_uri_schemes_are_normalized_without_access(
    source_type: ResearchSourceType, uri: str, canonical: str
) -> None:
    assert canonicalize_source_uri(source_type, uri) == canonical


@pytest.mark.parametrize(
    ("source_type", "uri"),
    [
        (ResearchSourceType.URL, "ftp://example.test/report"),
        (ResearchSourceType.FILE, "file://server/share/report.pdf"),
        (ResearchSourceType.MANUAL, "https://example.test/note"),
        (ResearchSourceType.GITHUB, "https://gitlab.com/example/repo"),
        (ResearchSourceType.URL, "https://user:secret@example.test/private"),
    ],
)
def test_invalid_source_uris_are_refused_before_any_acquisition(
    source_type: ResearchSourceType, uri: str
) -> None:
    with pytest.raises(InvalidResearchSourceUriError):
        canonicalize_source_uri(source_type, uri)


@pytest.mark.asyncio
async def test_source_registration_refuses_an_exhausted_project_quota() -> None:
    registry, store = service(max_sources_per_project=1)
    await add(registry, source_type=ResearchSourceType.MANUAL, uri="manual:first")

    with pytest.raises(ResearchSourceLimitError, match="source limit"):
        await add(
            registry,
            source_id="second-source",
            source_type=ResearchSourceType.MANUAL,
            uri="manual:second",
            title="Second source",
        )

    assert tuple(store.sources) == ("provider-docs",)
