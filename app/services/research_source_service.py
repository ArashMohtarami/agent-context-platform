"""Business rules for V3 Phase 2 source registration and immutable snapshots.

The service receives metadata that a caller supplied.  It never dereferences a
URL, reads a file, invokes Git, parses a document, or treats the metadata as
factual knowledge.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.models.common import Metadata, Page
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
from app.research.errors import (
    ResearchProjectNotFoundError,
    ResearchSourceAlreadyExistsError,
    ResearchSourceNotFoundError,
    SourceSnapshotAlreadyExistsError,
)
from app.research.uri import canonicalize_source_uri


class ResearchSourceLimitError(Exception):
    """A project has exhausted its bounded source-registration allowance."""


class ResearchProjectLookup(Protocol):
    """The existing research-project boundary used to inherit authorization."""

    async def get(self, research_project_id: str) -> ResearchProjectDocument | None: ...


class ResearchSourceCatalog(Protocol):
    """An identity lookup that does not itself make an authorization decision."""

    async def get(self, source_id: str) -> ResearchSourceDocument | None: ...


class ResearchSourceRepository(Protocol):
    """Scoped source persistence required by this phase's service."""

    async def create(self, document: ResearchSourceDocument) -> ResearchSourceDocument: ...

    async def get(self, source_id: str) -> ResearchSourceDocument | None: ...

    async def count_up_to(self, maximum: int) -> int: ...

    async def list(
        self,
        *,
        status: ResearchRecordStatus | None = ...,
        source_class: SourceClass | None = ...,
        allowed_project_ids: tuple[str, ...] | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[ResearchSourceDocument]: ...

    async def find_by_canonical_uri(self, canonical_uri: str) -> ResearchSourceDocument | None: ...

    async def find_by_metadata(
        self,
        *,
        source_type: ResearchSourceType,
        title: str,
        publisher: str | None,
        author: str | None,
        published_at: datetime | None,
    ) -> ResearchSourceDocument | None: ...


class SourceSnapshotRepository(Protocol):
    """Scoped append-only snapshot persistence required by this phase."""

    async def create(self, document: SourceSnapshotDocument) -> SourceSnapshotDocument: ...

    async def get(self, source_id: str, snapshot_id: str) -> SourceSnapshotDocument | None: ...

    async def list(
        self,
        *,
        source_id: str | None = ...,
        status: ResearchRecordStatus | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[SourceSnapshotDocument]: ...

    async def find_by_content_hash(self, content_hash: str) -> SourceSnapshotDocument | None: ...

    async def find_by_source_and_content_hash(
        self, source_id: str, content_hash: str
    ) -> SourceSnapshotDocument | None: ...


@dataclass(frozen=True)
class SourceSnapshotRegistration:
    """Caller-provided immutable observation metadata, never acquired by the server."""

    snapshot_id: str
    retrieved_at: datetime
    content_hash: str
    version_identifier: str | None
    metadata: Metadata


@dataclass(frozen=True)
class RegisteredResearchSource:
    """A source registration and its optional first immutable snapshot."""

    source: ResearchSourceDocument
    snapshot: SourceSnapshotDocument | None


class ResearchSourceService:
    """Register source metadata, retain supplied snapshots, and detect exact duplicates."""

    def __init__(
        self,
        projects: ResearchProjectLookup,
        catalog: ResearchSourceCatalog,
        sources: Callable[[str], ResearchSourceRepository],
        snapshots: Callable[[str], SourceSnapshotRepository],
        *,
        max_sources_per_project: int = 500,
    ) -> None:
        if max_sources_per_project < 1:
            raise ValueError("research source limit must be positive")
        self._projects = projects
        self._catalog = catalog
        self._sources = sources
        self._snapshots = snapshots
        self._max_sources_per_project = max_sources_per_project

    async def create_source(
        self,
        *,
        research_project_id: str,
        source_id: str,
        source_type: ResearchSourceType,
        uri: str,
        title: str,
        publisher: str | None,
        author: str | None,
        published_at: datetime | None,
        retrieved_at: datetime | None,
        source_class: SourceClass,
        trust_metadata: Metadata,
        status: ResearchRecordStatus,
        owner_key_id: str | None,
        snapshot: SourceSnapshotRegistration | None = None,
    ) -> RegisteredResearchSource:
        """Store supplied metadata after deterministic, local duplicate checks only."""
        project = await self._get_project(research_project_id)
        canonical_uri = canonicalize_source_uri(source_type, uri)
        authorization = ResearchAuthorization(
            project_ids=list(project.authorization.project_ids),
            owner_key_id=owner_key_id,
        )
        document = ResearchSourceDocument(
            research_project_id=research_project_id,
            source_id=source_id,
            source_type=source_type,
            uri=uri,
            canonical_uri=canonical_uri,
            title=title,
            publisher=publisher,
            author=author,
            published_at=published_at,
            retrieved_at=retrieved_at,
            source_class=source_class,
            trust_metadata=trust_metadata,
            status=status,
            authorization=authorization,
        )
        snapshot_document = (
            SourceSnapshotDocument(
                research_project_id=research_project_id,
                source_id=document.source_id,
                snapshot_id=snapshot.snapshot_id,
                retrieved_at=snapshot.retrieved_at,
                content_hash=snapshot.content_hash,
                version_identifier=snapshot.version_identifier,
                metadata=snapshot.metadata,
                authorization=authorization,
            )
            if snapshot is not None
            else None
        )
        scoped_sources = self._sources(research_project_id)
        # This ordinary metadata endpoint needs a hard project-local ceiling so
        # a caller cannot create an unbounded number of inert source records.
        # The repository remains the sole database access boundary.
        registered_count = await scoped_sources.count_up_to(self._max_sources_per_project)
        if registered_count >= self._max_sources_per_project:
            raise ResearchSourceLimitError("research project has reached its source limit")
        if await self._catalog.get(document.source_id) is not None:
            raise ResearchSourceAlreadyExistsError("research source already exists")
        if await scoped_sources.find_by_canonical_uri(canonical_uri) is not None:
            raise ResearchSourceAlreadyExistsError(
                "an equivalent canonical source URI already exists"
            )
        if (
            await scoped_sources.find_by_metadata(
                source_type=document.source_type,
                title=document.title,
                publisher=document.publisher,
                author=document.author,
                published_at=document.published_at,
            )
            is not None
        ):
            raise ResearchSourceAlreadyExistsError(
                "an equivalent source metadata record already exists"
            )
        if snapshot_document is not None and (
            await self._snapshots(research_project_id).find_by_content_hash(
                snapshot_document.content_hash
            )
            is not None
        ):
            raise ResearchSourceAlreadyExistsError("a source with this content hash already exists")
        try:
            created = await scoped_sources.create(document)
        except DuplicateDocumentError as error:
            raise ResearchSourceAlreadyExistsError("research source already exists") from error
        if snapshot_document is None:
            return RegisteredResearchSource(source=created, snapshot=None)
        try:
            created_snapshot = await self._snapshots(research_project_id).create(snapshot_document)
        except DuplicateDocumentError as error:
            # Snapshot writes are append-only.  No retry path updates an existing
            # observation, even if two callers used the same stable identifier.
            raise SourceSnapshotAlreadyExistsError("source snapshot already exists") from error
        return RegisteredResearchSource(source=created, snapshot=created_snapshot)

    async def get_source(self, source_id: str) -> ResearchSourceDocument:
        """Load an identity; the REST boundary applies source/project authorization."""
        source = await self._catalog.get(source_id)
        if source is None:
            raise ResearchSourceNotFoundError("research source was not found")
        return source

    async def list_sources(
        self,
        *,
        research_project_id: str,
        allowed_project_ids: tuple[str, ...] | None,
        status: ResearchRecordStatus | None = None,
        source_class: SourceClass | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ResearchSourceDocument]:
        """Page sources only after a caller's full V1 grant has been passed through."""
        return await self._sources(research_project_id).list(
            status=status,
            source_class=source_class,
            allowed_project_ids=allowed_project_ids,
            limit=limit,
            cursor=cursor,
        )

    async def create_snapshot(
        self,
        *,
        research_project_id: str,
        source_id: str,
        snapshot: SourceSnapshotRegistration,
    ) -> SourceSnapshotDocument:
        """Append a supplied source observation; no snapshot is ever overwritten."""
        source = await self._sources(research_project_id).get(source_id)
        if source is None:
            raise ResearchSourceNotFoundError("research source was not found")
        document = SourceSnapshotDocument(
            research_project_id=research_project_id,
            source_id=source.source_id,
            snapshot_id=snapshot.snapshot_id,
            retrieved_at=snapshot.retrieved_at,
            content_hash=snapshot.content_hash,
            version_identifier=snapshot.version_identifier,
            metadata=snapshot.metadata,
            authorization=source.authorization,
        )
        try:
            return await self._snapshots(research_project_id).create(document)
        except DuplicateDocumentError as error:
            raise SourceSnapshotAlreadyExistsError("source snapshot already exists") from error

    async def list_snapshots(
        self,
        *,
        research_project_id: str,
        source_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[SourceSnapshotDocument]:
        """Expose the immutable persistence contract to later V3 services, not REST yet."""
        return await self._snapshots(research_project_id).list(
            source_id=source_id, limit=limit, cursor=cursor
        )

    async def _get_project(self, research_project_id: str) -> ResearchProjectDocument:
        project = await self._projects.get(research_project_id)
        if project is None:
            raise ResearchProjectNotFoundError("research project was not found")
        return project
