"""Project-bound MongoDB persistence adapters for V3 research records.

Repositories provide typed storage and keyset pagination only.  They do not
fetch sources, run ingestion jobs, search evidence, judge claims, or promote
candidates.  Services in later phases will apply the existing V1
authentication/authorization rules before constructing or using these adapters.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pymongo import DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.research_indexes import RESEARCH_TEXT_INDEXES, RESEARCH_VECTOR_INDEXES
from app.db.research_search import (
    RESEARCH_MAX_NUM_CANDIDATES,
    RESEARCH_SEARCH_LIMIT,
    ResearchSearchScope,
    chunk_text_filter_clauses,
    chunk_text_query,
    chunk_vector_filter,
    claim_text_query,
    note_text_query,
    record_text_filter_clauses,
    record_vector_filter,
)
from app.db.vector_indexes import VECTOR_PATH
from app.models.common import MongoDocumentModel, Page, deterministic_object_id, validate_identifier
from app.models.research import (
    CandidateState,
    ClaimState,
    IngestionJobDocument,
    IngestionJobState,
    KnowledgeCandidateDocument,
    PromotionEventDocument,
    ResearchBriefDocument,
    ResearchBriefStatus,
    ResearchChunkDocument,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchDocumentDocument,
    ResearchNoteDocument,
    ResearchNoteStatus,
    ResearchProjectDocument,
    ResearchProjectStatus,
    ResearchRecordStatus,
    ResearchSourceDocument,
    ResearchSourceType,
    SkillUpdateProposalDocument,
    SkillUpdateProposalStatus,
    SourceClass,
    SourceSnapshotDocument,
)
from app.repositories.base import DEFAULT_PAGE_SIZE, SCORE_FIELD, MongoRepository, SearchMethod
from app.repositories.errors import ProjectIsolationError
from app.repositories.vectors import validated_vector


@dataclass(frozen=True, slots=True)
class ResearchChunkSearchMatch:
    """One scored chunk from an existing V3 Search index."""

    chunk: ResearchChunkDocument
    score: float


@dataclass(frozen=True, slots=True)
class ResearchNoteSearchMatch:
    """One scored working note, included only when a later phase stores one."""

    note: ResearchNoteDocument
    score: float


@dataclass(frozen=True, slots=True)
class ResearchClaimSearchMatch:
    """One scored claim record; its score is relevance, never confidence."""

    claim: ResearchClaimDocument
    score: float


class ResearchProjectRepository(Protocol):
    """Storage contract for research-project boundaries."""

    async def create(self, document: ResearchProjectDocument) -> ResearchProjectDocument: ...

    async def get(self, research_project_id: str) -> ResearchProjectDocument | None: ...

    async def list(
        self,
        *,
        status: ResearchProjectStatus | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ResearchProjectDocument]: ...

    async def update(
        self, document: ResearchProjectDocument, *, expected_revision: int
    ) -> ResearchProjectDocument: ...


class ResearchProjectScopedRepository[TDocument: MongoDocumentModel](Protocol):
    """Minimum contract for records that must remain in one research project."""

    research_project_id: str

    async def create(self, document: TDocument) -> TDocument: ...

    async def list(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> Page[TDocument]: ...

    async def update(self, document: TDocument, *, expected_revision: int) -> TDocument: ...


class MongoResearchProjectRepository(MongoRepository[ResearchProjectDocument]):
    """Persist research-project boundaries by stable public identifier."""

    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        super().__init__(database, "research_projects", ResearchProjectDocument)

    async def create(self, document: ResearchProjectDocument) -> ResearchProjectDocument:
        return await self._create(document)

    async def get(self, research_project_id: str) -> ResearchProjectDocument | None:
        identifier = validate_identifier(research_project_id)
        return await self._get({"_id": deterministic_object_id("research_project", identifier)})

    async def list(
        self,
        *,
        status: ResearchProjectStatus | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ResearchProjectDocument]:
        filters: MongoDocument = {}
        if status is not None:
            filters["status"] = status
        if allowed_project_ids is not None:
            # A record attached to several V1 projects is visible only when
            # *every* attachment belongs to the caller. `$setIsSubset` also
            # admits global research (the empty set) without treating it as an
            # implicit request for every private project.
            filters["$expr"] = {
                "$setIsSubset": ["$authorization.project_ids", list(allowed_project_ids)]
            }
        return await self._page(filters, limit=limit, cursor=cursor)

    async def update(
        self, document: ResearchProjectDocument, *, expected_revision: int
    ) -> ResearchProjectDocument:
        return await self._update(document, expected_revision=expected_revision)


class _MongoResearchProjectScopedRepository[TDocument: MongoDocumentModel](
    MongoRepository[TDocument]
):
    """Keep every child record read/write bound to one research project."""

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        collection_name: str,
        model_type: type[TDocument],
        research_project_id: str,
    ) -> None:
        super().__init__(database, collection_name, model_type)
        self.research_project_id = validate_identifier(research_project_id)

    def _require_scope(self, document: TDocument) -> None:
        if getattr(document, "research_project_id", None) != self.research_project_id:
            raise ProjectIsolationError("research record belongs to a different project")

    async def _create_scoped(self, document: TDocument) -> TDocument:
        self._require_scope(document)
        return await self._create(document)

    async def _get_scoped(self, filters: MongoDocument) -> TDocument | None:
        return await self._get({"research_project_id": self.research_project_id, **filters})

    async def _list_scoped(
        self,
        filters: MongoDocument | None = None,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[TDocument]:
        query: MongoDocument = {"research_project_id": self.research_project_id}
        if filters is not None:
            query.update(filters)
        return await self._page(query, limit=limit, cursor=cursor)

    async def _update_scoped(self, document: TDocument, *, expected_revision: int) -> TDocument:
        self._require_scope(document)
        return await self._update(
            document,
            expected_revision=expected_revision,
            filters={"research_project_id": self.research_project_id},
        )


class MongoResearchSourceCatalogRepository(MongoRepository[ResearchSourceDocument]):
    """Look up a source identity before the service applies its stored project scope."""

    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        super().__init__(database, "research_sources", ResearchSourceDocument)

    async def get(self, source_id: str) -> ResearchSourceDocument | None:
        return await self._get({"source_id": validate_identifier(source_id)})


class MongoResearchSourceRepository(_MongoResearchProjectScopedRepository[ResearchSourceDocument]):
    """Store registered sources without performing a network or file operation."""

    def __init__(self, database: AsyncDatabase[MongoDocument], research_project_id: str) -> None:
        super().__init__(database, "research_sources", ResearchSourceDocument, research_project_id)

    async def create(self, document: ResearchSourceDocument) -> ResearchSourceDocument:
        return await self._create_scoped(document)

    async def get(self, source_id: str) -> ResearchSourceDocument | None:
        return await self._get_scoped({"source_id": validate_identifier(source_id)})

    async def count_up_to(self, maximum: int) -> int:
        """Count only enough scoped IDs to enforce the source-registration quota."""
        if isinstance(maximum, bool) or maximum < 1:
            raise ValueError("maximum must be a positive integer")
        cursor = self._collection.find(
            {"research_project_id": self.research_project_id}, {"_id": 1}
        ).limit(maximum)
        return len(await cursor.to_list(length=maximum))

    async def list(
        self,
        *,
        status: ResearchRecordStatus | None = None,
        source_class: SourceClass | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ResearchSourceDocument]:
        filters: MongoDocument = {}
        if status is not None:
            filters["status"] = status
        if source_class is not None:
            filters["source_class"] = source_class
        if allowed_project_ids is not None:
            filters["$expr"] = {
                "$setIsSubset": ["$authorization.project_ids", list(allowed_project_ids)]
            }
        return await self._list_scoped(filters, limit=limit, cursor=cursor)

    async def find_by_canonical_uri(self, canonical_uri: str) -> ResearchSourceDocument | None:
        """Find a project-local canonical URI without treating it as reachable."""
        return await self._get_scoped({"canonical_uri": canonical_uri})

    async def get_many(self, source_ids: Sequence[str]) -> tuple[ResearchSourceDocument, ...]:
        """Load source provenance for a bounded set of already-scoped chunk IDs."""
        identifiers = tuple(dict.fromkeys(validate_identifier(item) for item in source_ids))
        if not identifiers:
            return ()
        cursor = self._collection.find(
            {
                "research_project_id": self.research_project_id,
                "source_id": {"$in": list(identifiers)},
            }
        )
        documents = await cursor.to_list(length=len(identifiers))
        return tuple(self._validate(item) for item in documents)

    async def find_by_metadata(
        self,
        *,
        source_type: ResearchSourceType,
        title: str,
        publisher: str | None,
        author: str | None,
        published_at: datetime | None,
    ) -> ResearchSourceDocument | None:
        """Find an exact metadata duplicate when a URI alone is insufficient."""
        return await self._get_scoped(
            {
                "source_type": source_type,
                "title": title,
                "publisher": publisher,
                "author": author,
                "published_at": published_at,
            }
        )

    async def update(
        self, document: ResearchSourceDocument, *, expected_revision: int
    ) -> ResearchSourceDocument:
        return await self._update_scoped(document, expected_revision=expected_revision)


class MongoSourceSnapshotRepository(_MongoResearchProjectScopedRepository[SourceSnapshotDocument]):
    """Persist source versions, including commit/ETag metadata, without fetching."""

    def __init__(self, database: AsyncDatabase[MongoDocument], research_project_id: str) -> None:
        super().__init__(database, "source_snapshots", SourceSnapshotDocument, research_project_id)

    async def create(self, document: SourceSnapshotDocument) -> SourceSnapshotDocument:
        return await self._create_scoped(document)

    async def get(self, source_id: str, snapshot_id: str) -> SourceSnapshotDocument | None:
        return await self._get_scoped(
            {
                "source_id": validate_identifier(source_id),
                "snapshot_id": validate_identifier(snapshot_id),
            }
        )

    async def list(
        self,
        *,
        source_id: str | None = None,
        status: ResearchRecordStatus | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[SourceSnapshotDocument]:
        filters: MongoDocument = {}
        if source_id is not None:
            filters["source_id"] = validate_identifier(source_id)
        if status is not None:
            filters["status"] = status
        return await self._list_scoped(filters, limit=limit, cursor=cursor)

    async def latest_for_source(self, source_id: str) -> SourceSnapshotDocument | None:
        """Read one source's newest immutable observation through its standard index."""
        source = validate_identifier(source_id)
        cursor = self._collection.find(
            {"research_project_id": self.research_project_id, "source_id": source}
        ).sort([("retrieved_at", DESCENDING), ("_id", DESCENDING)])
        documents = await cursor.limit(1).to_list(length=1)
        return self._validate(documents[0]) if documents else None

    async def find_by_content_hash(self, content_hash: str) -> SourceSnapshotDocument | None:
        """Find a project-local prior observation with the same supplied hash."""
        return await self._get_scoped({"content_hash": content_hash})

    async def find_by_source_and_content_hash(
        self, source_id: str, content_hash: str
    ) -> SourceSnapshotDocument | None:
        """Find one source's immutable version without collapsing another source's history."""
        return await self._get_scoped(
            {"source_id": validate_identifier(source_id), "content_hash": content_hash}
        )

    async def get_many(
        self, source_snapshot_pairs: Sequence[tuple[str, str]]
    ) -> tuple[SourceSnapshotDocument, ...]:
        """Load immutable snapshot timing for the bounded chunk search result set."""
        pairs = tuple(
            dict.fromkeys(
                (validate_identifier(source_id), validate_identifier(snapshot_id))
                for source_id, snapshot_id in source_snapshot_pairs
            )
        )
        if not pairs:
            return ()
        cursor = self._collection.find(
            {
                "research_project_id": self.research_project_id,
                "$or": [
                    {"source_id": source_id, "snapshot_id": snapshot_id}
                    for source_id, snapshot_id in pairs
                ],
            }
        )
        documents = await cursor.to_list(length=len(pairs))
        return tuple(self._validate(item) for item in documents)

    async def update(
        self, document: SourceSnapshotDocument, *, expected_revision: int
    ) -> SourceSnapshotDocument:
        return await self._update_scoped(document, expected_revision=expected_revision)


class MongoResearchDocumentRepository(
    _MongoResearchProjectScopedRepository[ResearchDocumentDocument]
):
    """Persist normalized retained document material and its storage policy."""

    def __init__(self, database: AsyncDatabase[MongoDocument], research_project_id: str) -> None:
        super().__init__(
            database, "research_documents", ResearchDocumentDocument, research_project_id
        )

    async def create(self, document: ResearchDocumentDocument) -> ResearchDocumentDocument:
        return await self._create_scoped(document)

    async def get(
        self, source_id: str, snapshot_id: str, document_id: str
    ) -> ResearchDocumentDocument | None:
        return await self._get_scoped(
            {
                "source_id": validate_identifier(source_id),
                "snapshot_id": validate_identifier(snapshot_id),
                "document_id": validate_identifier(document_id),
            }
        )

    async def list(
        self,
        *,
        source_id: str | None = None,
        status: ResearchRecordStatus | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ResearchDocumentDocument]:
        filters: MongoDocument = {}
        if source_id is not None:
            filters["source_id"] = validate_identifier(source_id)
        if status is not None:
            filters["status"] = status
        return await self._list_scoped(filters, limit=limit, cursor=cursor)

    async def update(
        self, document: ResearchDocumentDocument, *, expected_revision: int
    ) -> ResearchDocumentDocument:
        return await self._update_scoped(document, expected_revision=expected_revision)


class MongoResearchChunkRepository(_MongoResearchProjectScopedRepository[ResearchChunkDocument]):
    """Store citable chunks and optional local-model embeddings; never create them here."""

    def __init__(self, database: AsyncDatabase[MongoDocument], research_project_id: str) -> None:
        super().__init__(database, "research_chunks", ResearchChunkDocument, research_project_id)

    async def create(self, document: ResearchChunkDocument) -> ResearchChunkDocument:
        return await self._create_scoped(document)

    async def get(self, document_id: str, chunk_id: str) -> ResearchChunkDocument | None:
        return await self._get_scoped(
            {
                "document_id": validate_identifier(document_id),
                "chunk_id": validate_identifier(chunk_id),
            }
        )

    async def get_by_source_snapshot_chunk(
        self, source_id: str, snapshot_id: str, chunk_id: str
    ) -> ResearchChunkDocument | None:
        """Resolve a citation target without accepting an unscoped document lookup."""
        return await self._get_scoped(
            {
                "source_id": validate_identifier(source_id),
                "snapshot_id": validate_identifier(snapshot_id),
                "chunk_id": validate_identifier(chunk_id),
            }
        )

    async def list(
        self,
        *,
        document_id: str | None = None,
        status: ResearchRecordStatus | None = None,
        tags: Sequence[str] = (),
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ResearchChunkDocument]:
        filters: MongoDocument = {}
        if document_id is not None:
            filters["document_id"] = validate_identifier(document_id)
        if status is not None:
            filters["status"] = status
        if tags:
            filters["tags"] = {"$all": [validate_identifier(tag) for tag in tags]}
        return await self._list_scoped(filters, limit=limit, cursor=cursor)

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        num_candidates: int,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchChunkSearchMatch, ...]:
        """Search only active project chunks through the existing vector index."""
        self._require_search_scope(scope)
        self._validate_search_limit(limit, num_candidates)
        pipeline: list[MongoDocument] = [
            {
                "$vectorSearch": {
                    "index": RESEARCH_VECTOR_INDEXES["chunk"],
                    "path": VECTOR_PATH,
                    "queryVector": validated_vector(embedding),
                    "numCandidates": num_candidates,
                    "limit": limit,
                    "filter": chunk_vector_filter(scope),
                }
            },
            {"$addFields": {SCORE_FIELD: {"$meta": "vectorSearchScore"}}},
        ]
        return await self._chunk_matches(pipeline, limit, method="vector")

    async def text_search(
        self,
        query: str,
        *,
        limit: int,
        phrase: bool,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchChunkSearchMatch, ...]:
        """Search only active project chunks through the existing lexical index."""
        self._require_search_scope(scope)
        if not 1 <= limit <= RESEARCH_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {RESEARCH_SEARCH_LIMIT}")
        compound = {
            **chunk_text_query(query, phrase=phrase),
            "filter": chunk_text_filter_clauses(scope),
        }
        pipeline: list[MongoDocument] = [
            {"$search": {"index": RESEARCH_TEXT_INDEXES["chunk"], "compound": compound}},
            {"$addFields": {SCORE_FIELD: {"$meta": "searchScore"}}},
            {"$limit": limit},
        ]
        return await self._chunk_matches(pipeline, limit, method="lexical")

    def _require_search_scope(self, scope: ResearchSearchScope) -> None:
        if scope.research_project_id != self.research_project_id:
            raise ProjectIsolationError("research search belongs to a different project")

    @staticmethod
    def _validate_search_limit(limit: int, num_candidates: int) -> None:
        if isinstance(limit, bool) or not 1 <= limit <= RESEARCH_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {RESEARCH_SEARCH_LIMIT}")
        if (
            isinstance(num_candidates, bool)
            or not limit <= num_candidates <= RESEARCH_MAX_NUM_CANDIDATES
        ):
            raise ValueError(
                f"num_candidates must be between limit and {RESEARCH_MAX_NUM_CANDIDATES}"
            )

    async def _chunk_matches(
        self, pipeline: Sequence[MongoDocument], limit: int, *, method: SearchMethod
    ) -> tuple[ResearchChunkSearchMatch, ...]:
        scored = await self._scored_search(pipeline, limit, method=method)
        return tuple(
            ResearchChunkSearchMatch(chunk=document, score=score) for document, score in scored
        )

    async def update(
        self, document: ResearchChunkDocument, *, expected_revision: int
    ) -> ResearchChunkDocument:
        return await self._update_scoped(document, expected_revision=expected_revision)


class MongoResearchNoteRepository(_MongoResearchProjectScopedRepository[ResearchNoteDocument]):
    """Persist working notes without interpreting them as facts."""

    def __init__(self, database: AsyncDatabase[MongoDocument], research_project_id: str) -> None:
        super().__init__(database, "research_notes", ResearchNoteDocument, research_project_id)

    async def create(self, document: ResearchNoteDocument) -> ResearchNoteDocument:
        return await self._create_scoped(document)

    async def get(self, note_id: str) -> ResearchNoteDocument | None:
        return await self._get_scoped({"note_id": validate_identifier(note_id)})

    async def list(
        self,
        *,
        status: ResearchNoteStatus | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ResearchNoteDocument]:
        filters: MongoDocument = {} if status is None else {"status": status}
        if allowed_project_ids is not None:
            filters["$expr"] = {
                "$setIsSubset": ["$authorization.project_ids", list(allowed_project_ids)]
            }
        return await self._list_scoped(filters, limit=limit, cursor=cursor)

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        num_candidates: int,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchNoteSearchMatch, ...]:
        self._require_search_scope(scope)
        MongoResearchChunkRepository._validate_search_limit(limit, num_candidates)
        pipeline: list[MongoDocument] = [
            {
                "$vectorSearch": {
                    "index": RESEARCH_VECTOR_INDEXES["note"],
                    "path": VECTOR_PATH,
                    "queryVector": validated_vector(embedding),
                    "numCandidates": num_candidates,
                    "limit": limit,
                    "filter": record_vector_filter(scope),
                }
            },
            {"$addFields": {SCORE_FIELD: {"$meta": "vectorSearchScore"}}},
        ]
        scored = await self._scored_search(pipeline, limit, method="vector")
        return tuple(
            ResearchNoteSearchMatch(note=document, score=score) for document, score in scored
        )

    async def text_search(
        self, query: str, *, limit: int, phrase: bool, scope: ResearchSearchScope
    ) -> tuple[ResearchNoteSearchMatch, ...]:
        self._require_search_scope(scope)
        if not 1 <= limit <= RESEARCH_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {RESEARCH_SEARCH_LIMIT}")
        compound = {
            **note_text_query(query, phrase=phrase),
            "filter": record_text_filter_clauses(scope),
        }
        pipeline: list[MongoDocument] = [
            {"$search": {"index": RESEARCH_TEXT_INDEXES["note"], "compound": compound}},
            {"$addFields": {SCORE_FIELD: {"$meta": "searchScore"}}},
            {"$limit": limit},
        ]
        scored = await self._scored_search(pipeline, limit, method="lexical")
        return tuple(
            ResearchNoteSearchMatch(note=document, score=score) for document, score in scored
        )

    def _require_search_scope(self, scope: ResearchSearchScope) -> None:
        if scope.research_project_id != self.research_project_id:
            raise ProjectIsolationError("research search belongs to a different project")

    async def update(
        self, document: ResearchNoteDocument, *, expected_revision: int
    ) -> ResearchNoteDocument:
        return await self._update_scoped(document, expected_revision=expected_revision)


class MongoResearchClaimRepository(_MongoResearchProjectScopedRepository[ResearchClaimDocument]):
    """Persist explicit claim state; no repository derives support or confidence."""

    def __init__(self, database: AsyncDatabase[MongoDocument], research_project_id: str) -> None:
        super().__init__(database, "research_claims", ResearchClaimDocument, research_project_id)

    async def create(self, document: ResearchClaimDocument) -> ResearchClaimDocument:
        return await self._create_scoped(document)

    async def get(self, claim_id: str) -> ResearchClaimDocument | None:
        return await self._get_scoped({"claim_id": validate_identifier(claim_id)})

    async def list(
        self,
        *,
        status: ClaimState | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ResearchClaimDocument]:
        filters: MongoDocument = {} if status is None else {"status": status}
        if allowed_project_ids is not None:
            filters["$expr"] = {
                "$setIsSubset": ["$authorization.project_ids", list(allowed_project_ids)]
            }
        return await self._list_scoped(filters, limit=limit, cursor=cursor)

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        num_candidates: int,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchClaimSearchMatch, ...]:
        self._require_search_scope(scope)
        MongoResearchChunkRepository._validate_search_limit(limit, num_candidates)
        pipeline: list[MongoDocument] = [
            {
                "$vectorSearch": {
                    "index": RESEARCH_VECTOR_INDEXES["claim"],
                    "path": VECTOR_PATH,
                    "queryVector": validated_vector(embedding),
                    "numCandidates": num_candidates,
                    "limit": limit,
                    "filter": record_vector_filter(scope),
                }
            },
            {"$addFields": {SCORE_FIELD: {"$meta": "vectorSearchScore"}}},
        ]
        scored = await self._scored_search(pipeline, limit, method="vector")
        return tuple(
            ResearchClaimSearchMatch(claim=document, score=score) for document, score in scored
        )

    async def text_search(
        self, query: str, *, limit: int, phrase: bool, scope: ResearchSearchScope
    ) -> tuple[ResearchClaimSearchMatch, ...]:
        self._require_search_scope(scope)
        if not 1 <= limit <= RESEARCH_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {RESEARCH_SEARCH_LIMIT}")
        compound = {
            **claim_text_query(query, phrase=phrase),
            "filter": record_text_filter_clauses(scope),
        }
        pipeline: list[MongoDocument] = [
            {"$search": {"index": RESEARCH_TEXT_INDEXES["claim"], "compound": compound}},
            {"$addFields": {SCORE_FIELD: {"$meta": "searchScore"}}},
            {"$limit": limit},
        ]
        scored = await self._scored_search(pipeline, limit, method="lexical")
        return tuple(
            ResearchClaimSearchMatch(claim=document, score=score) for document, score in scored
        )

    def _require_search_scope(self, scope: ResearchSearchScope) -> None:
        if scope.research_project_id != self.research_project_id:
            raise ProjectIsolationError("research search belongs to a different project")

    async def update(
        self, document: ResearchClaimDocument, *, expected_revision: int
    ) -> ResearchClaimDocument:
        return await self._update_scoped(document, expected_revision=expected_revision)


class MongoResearchCitationRepository(
    _MongoResearchProjectScopedRepository[ResearchCitationDocument]
):
    """Persist citable source-version evidence relations without claim evaluation."""

    def __init__(self, database: AsyncDatabase[MongoDocument], research_project_id: str) -> None:
        super().__init__(
            database, "research_citations", ResearchCitationDocument, research_project_id
        )

    async def create(self, document: ResearchCitationDocument) -> ResearchCitationDocument:
        return await self._create_scoped(document)

    async def get(self, claim_id: str, citation_id: str) -> ResearchCitationDocument | None:
        return await self._get_scoped(
            {
                "claim_id": validate_identifier(claim_id),
                "citation_id": validate_identifier(citation_id),
            }
        )

    async def list(
        self,
        *,
        claim_id: str | None = None,
        source_id: str | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ResearchCitationDocument]:
        filters: MongoDocument = {}
        if claim_id is not None:
            filters["claim_id"] = validate_identifier(claim_id)
        if source_id is not None:
            filters["source_id"] = validate_identifier(source_id)
        if allowed_project_ids is not None:
            filters["$expr"] = {
                "$setIsSubset": ["$authorization.project_ids", list(allowed_project_ids)]
            }
        return await self._list_scoped(filters, limit=limit, cursor=cursor)

    async def update(
        self, document: ResearchCitationDocument, *, expected_revision: int
    ) -> ResearchCitationDocument:
        return await self._update_scoped(document, expected_revision=expected_revision)


class MongoResearchBriefRepository(_MongoResearchProjectScopedRepository[ResearchBriefDocument]):
    """Persist structured briefs with evidence and uncertainty fields intact."""

    def __init__(self, database: AsyncDatabase[MongoDocument], research_project_id: str) -> None:
        super().__init__(database, "research_briefs", ResearchBriefDocument, research_project_id)

    async def create(self, document: ResearchBriefDocument) -> ResearchBriefDocument:
        return await self._create_scoped(document)

    async def get(self, brief_id: str) -> ResearchBriefDocument | None:
        return await self._get_scoped({"brief_id": validate_identifier(brief_id)})

    async def list(
        self,
        *,
        status: ResearchBriefStatus | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ResearchBriefDocument]:
        filters: MongoDocument = {} if status is None else {"status": status}
        if allowed_project_ids is not None:
            filters["$expr"] = {
                "$setIsSubset": ["$authorization.project_ids", list(allowed_project_ids)]
            }
        return await self._list_scoped(filters, limit=limit, cursor=cursor)

    async def update(
        self, document: ResearchBriefDocument, *, expected_revision: int
    ) -> ResearchBriefDocument:
        return await self._update_scoped(document, expected_revision=expected_revision)


class MongoKnowledgeCandidateRepository(
    _MongoResearchProjectScopedRepository[KnowledgeCandidateDocument]
):
    """Persist review-gated proposals without a promotion operation."""

    def __init__(self, database: AsyncDatabase[MongoDocument], research_project_id: str) -> None:
        super().__init__(
            database, "knowledge_candidates", KnowledgeCandidateDocument, research_project_id
        )

    async def create(self, document: KnowledgeCandidateDocument) -> KnowledgeCandidateDocument:
        return await self._create_scoped(document)

    async def get(self, candidate_id: str) -> KnowledgeCandidateDocument | None:
        return await self._get_scoped({"candidate_id": validate_identifier(candidate_id)})

    async def list(
        self,
        *,
        status: CandidateState | None = None,
        target_project_id: str | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[KnowledgeCandidateDocument]:
        filters: MongoDocument = {}
        if status is not None:
            filters["status"] = status
        if target_project_id is not None:
            filters["target_project_id"] = validate_identifier(target_project_id)
        if allowed_project_ids is not None:
            filters["$expr"] = {
                "$setIsSubset": ["$authorization.project_ids", list(allowed_project_ids)]
            }
        return await self._list_scoped(filters, limit=limit, cursor=cursor)

    async def update(
        self, document: KnowledgeCandidateDocument, *, expected_revision: int
    ) -> KnowledgeCandidateDocument:
        return await self._update_scoped(document, expected_revision=expected_revision)


class MongoSkillUpdateProposalRepository(
    _MongoResearchProjectScopedRepository[SkillUpdateProposalDocument]
):
    """Persist review-only generated patches without access to V1 skill writes or Git."""

    def __init__(self, database: AsyncDatabase[MongoDocument], research_project_id: str) -> None:
        super().__init__(
            database,
            "skill_update_proposals",
            SkillUpdateProposalDocument,
            research_project_id,
        )

    async def create(self, document: SkillUpdateProposalDocument) -> SkillUpdateProposalDocument:
        return await self._create_scoped(document)

    async def get(self, proposal_id: str) -> SkillUpdateProposalDocument | None:
        return await self._get_scoped({"proposal_id": validate_identifier(proposal_id)})

    async def list(
        self,
        *,
        status: SkillUpdateProposalStatus | None = None,
        skill_id: str | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[SkillUpdateProposalDocument]:
        filters: MongoDocument = {}
        if status is not None:
            filters["status"] = status
        if skill_id is not None:
            filters["skill_id"] = validate_identifier(skill_id)
        if allowed_project_ids is not None:
            filters["$expr"] = {
                "$setIsSubset": ["$authorization.project_ids", list(allowed_project_ids)]
            }
        return await self._list_scoped(filters, limit=limit, cursor=cursor)

    async def update(
        self, document: SkillUpdateProposalDocument, *, expected_revision: int
    ) -> SkillUpdateProposalDocument:
        return await self._update_scoped(document, expected_revision=expected_revision)


class MongoPromotionEventRepository(_MongoResearchProjectScopedRepository[PromotionEventDocument]):
    """Append-only persistence adapter for decisions made elsewhere."""

    def __init__(self, database: AsyncDatabase[MongoDocument], research_project_id: str) -> None:
        super().__init__(database, "promotion_events", PromotionEventDocument, research_project_id)

    async def create(self, document: PromotionEventDocument) -> PromotionEventDocument:
        return await self._create_scoped(document)

    async def get(self, candidate_id: str, event_id: str) -> PromotionEventDocument | None:
        return await self._get_scoped(
            {
                "candidate_id": validate_identifier(candidate_id),
                "event_id": validate_identifier(event_id),
            }
        )

    async def list(
        self,
        *,
        candidate_id: str | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[PromotionEventDocument]:
        filters: MongoDocument = {}
        if candidate_id is not None:
            filters["candidate_id"] = validate_identifier(candidate_id)
        if allowed_project_ids is not None:
            filters["$expr"] = {
                "$setIsSubset": ["$authorization.project_ids", list(allowed_project_ids)]
            }
        return await self._list_scoped(filters, limit=limit, cursor=cursor)


class MongoIngestionJobRepository(_MongoResearchProjectScopedRepository[IngestionJobDocument]):
    """Persist job lifecycle metadata without a queue, worker, or source execution."""

    def __init__(self, database: AsyncDatabase[MongoDocument], research_project_id: str) -> None:
        super().__init__(database, "ingestion_jobs", IngestionJobDocument, research_project_id)

    async def create(self, document: IngestionJobDocument) -> IngestionJobDocument:
        return await self._create_scoped(document)

    async def get(self, job_id: str) -> IngestionJobDocument | None:
        return await self._get_scoped({"job_id": validate_identifier(job_id)})

    async def list(
        self,
        *,
        source_id: str | None = None,
        status: IngestionJobState | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[IngestionJobDocument]:
        filters: MongoDocument = {}
        if source_id is not None:
            filters["source_id"] = validate_identifier(source_id)
        if status is not None:
            filters["status"] = status
        return await self._list_scoped(filters, limit=limit, cursor=cursor)

    async def update(
        self, document: IngestionJobDocument, *, expected_revision: int
    ) -> IngestionJobDocument:
        return await self._update_scoped(document, expected_revision=expected_revision)


__all__ = [
    "MongoIngestionJobRepository",
    "MongoKnowledgeCandidateRepository",
    "MongoPromotionEventRepository",
    "MongoResearchBriefRepository",
    "MongoResearchChunkRepository",
    "MongoResearchCitationRepository",
    "MongoResearchClaimRepository",
    "MongoResearchDocumentRepository",
    "MongoResearchNoteRepository",
    "MongoResearchProjectRepository",
    "MongoResearchSourceRepository",
    "MongoSkillUpdateProposalRepository",
    "MongoSourceSnapshotRepository",
    "ResearchProjectRepository",
    "ResearchProjectScopedRepository",
]
