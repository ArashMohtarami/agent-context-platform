"""Profile-isolated MongoDB repositories for V2 creator/content records.

They are storage adapters only.  No repository advances a lifecycle, creates
versions, selects sources, or publishes content; later services coordinate
those operations using the guarded primitives here.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from pymongo import DESCENDING
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.db.client_types import MongoDocument
from app.db.content_indexes import (
    CONTENT_TEXT_INDEXES,
    CONTENT_VECTOR_INDEXES,
    ContentCollectionKind,
    ContentQueryFilters,
    content_filter_clauses,
    content_text_query,
    content_vector_filter,
)
from app.db.vector_indexes import VECTOR_PATH
from app.models.common import (
    MongoDocumentModel,
    MongoObjectId,
    Page,
    decode_page_cursor,
    encode_page_cursor,
    utc_now,
    validate_identifier,
)
from app.models.content import (
    ContentBriefDocument,
    ContentCampaignDocument,
    ContentIdeaDocument,
    ContentItemDocument,
    ContentLinkDocument,
    ContentMetricDocument,
    ContentPillarDocument,
    ContentPlatform,
    ContentStatus,
    ContentVersionDocument,
    CreatorProfileDocument,
)
from app.provenance import attribute
from app.repositories.base import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SCORE_FIELD,
    MongoRepository,
    SearchMethod,
)
from app.repositories.errors import (
    ConcurrencyConflictError,
    DuplicateDocumentError,
    ProjectIsolationError,
)
from app.repositories.vectors import validated_vector
from app.security.tenant import TenantContext

DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50
MAX_NUM_CANDIDATES = 10_000


@dataclass(frozen=True, slots=True)
class ContentSearchMatch:
    """A scored current content representation from one Mongo search method."""

    document: ContentIdeaDocument | ContentBriefDocument | ContentItemDocument
    score: float


class ProfileOwnedRepository[TDocument: MongoDocumentModel](Protocol):
    """Minimum contract later V2 services need for mutable profile-owned data."""

    profile_id: str

    async def create(self, document: TDocument) -> TDocument:
        """Store one document owned by this repository's profile."""

    async def list(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> Page[TDocument]:
        """Return profile-owned documents in a bounded keyset page."""

    async def update(self, document: TDocument, *, expected_revision: int) -> TDocument:
        """Replace a document only when its expected revision still matches."""


class ContentVersionRepository(Protocol):
    """Append-only contract for content history snapshots."""

    profile_id: str

    async def create(self, document: ContentVersionDocument) -> ContentVersionDocument:
        """Append one immutable snapshot."""

    async def list(
        self,
        *,
        content_id: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ContentVersionDocument]:
        """Page immutable snapshots in this profile."""


class _ProfileRepository[TDocument: MongoDocumentModel](MongoRepository[TDocument]):
    """Bind every V2 content operation to exactly one creator profile."""

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        collection_name: str,
        model_type: type[TDocument],
        profile_id: str,
        *,
        tenant: TenantContext | None = None,
    ) -> None:
        super().__init__(database, collection_name, model_type, tenant=tenant)
        self._database = database
        self.profile_id = validate_identifier(profile_id)

    def _require_profile(self, document: TDocument) -> None:
        profile_id = getattr(document, "profile_id", None)
        if profile_id != self.profile_id:
            raise ProjectIsolationError("content belongs to a different creator profile")

    async def _create_for_profile(self, document: TDocument) -> TDocument:
        self._require_profile(document)
        return await self._create(document)

    async def _get_for_profile(self, document_id: MongoObjectId) -> TDocument | None:
        return await self._get({"_id": document_id, "profile_id": self.profile_id})

    async def _list_for_profile(
        self,
        filters: MongoDocument | None = None,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[TDocument]:
        query: MongoDocument = {"profile_id": self.profile_id}
        if filters:
            query.update(filters)
        return await self._page(query, limit=limit, cursor=cursor)

    async def _update_for_profile(
        self,
        document: TDocument,
        *,
        expected_revision: int,
    ) -> TDocument:
        self._require_profile(document)
        return await self._update(
            document,
            expected_revision=expected_revision,
            filters={"profile_id": self.profile_id},
        )

    async def _recent_for_profile(
        self,
        filters: MongoDocument | None = None,
        *,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[TDocument, ...]:
        query: MongoDocument = {"profile_id": self.profile_id}
        if filters:
            query.update(filters)
        return await self._recent(
            query,
            updated_after=updated_after,
            updated_before=updated_before,
            limit=limit,
        )


class _ContentSearchRepository[TDocument: MongoDocumentModel](_ProfileRepository[TDocument]):
    """Shared vector and lexical search for one profile-owned content collection."""

    _search_kind: ContentCollectionKind

    def _require_query_scope(self, filters: ContentQueryFilters) -> None:
        if filters.profile_id != self.profile_id:
            raise ProjectIsolationError("content query belongs to a different creator profile")

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        num_candidates: int,
        filters: ContentQueryFilters,
    ) -> tuple[ContentSearchMatch, ...]:
        """Search only embedded current records inside this creator profile."""
        self._require_query_scope(filters)
        query_vector = validated_vector(embedding)
        _validate_search_limit(limit)
        if isinstance(num_candidates, bool) or not limit <= num_candidates <= MAX_NUM_CANDIDATES:
            raise ValueError(f"num_candidates must be between limit and {MAX_NUM_CANDIDATES}")
        pipeline: list[MongoDocument] = [
            {
                "$vectorSearch": {
                    "index": CONTENT_VECTOR_INDEXES[self._search_kind],
                    "path": VECTOR_PATH,
                    "queryVector": query_vector,
                    "numCandidates": num_candidates,
                    "limit": limit,
                    "filter": content_vector_filter(self._search_kind, filters),
                }
            },
            {"$addFields": {SCORE_FIELD: {"$meta": "vectorSearchScore"}}},
        ]
        return await self._matches(pipeline, limit, method="vector")

    async def text_search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        phrase: bool = False,
        filters: ContentQueryFilters,
    ) -> tuple[ContentSearchMatch, ...]:
        """Search lexical current records inside this creator profile."""
        self._require_query_scope(filters)
        _validate_search_limit(limit)
        compound = {
            **content_text_query(self._search_kind, query, phrase=phrase),
            "filter": content_filter_clauses(self._search_kind, filters),
        }
        pipeline: list[MongoDocument] = [
            {"$search": {"index": CONTENT_TEXT_INDEXES[self._search_kind], "compound": compound}},
            {"$addFields": {SCORE_FIELD: {"$meta": "searchScore"}}},
            {"$limit": limit},
        ]
        return await self._matches(pipeline, limit, method="lexical")

    async def _matches(
        self,
        pipeline: Sequence[MongoDocument],
        limit: int,
        *,
        method: SearchMethod,
    ) -> tuple[ContentSearchMatch, ...]:
        scored = await self._scored_search(pipeline, limit, method=method)
        return tuple(
            ContentSearchMatch(
                document=cast(
                    ContentIdeaDocument | ContentBriefDocument | ContentItemDocument,
                    document,
                ),
                score=score,
            )
            for document, score in scored
        )


class MongoCreatorProfileRepository(MongoRepository[CreatorProfileDocument]):
    """Persist globally listed creator profiles by deterministic profile ID."""

    def __init__(
        self, database: AsyncDatabase[MongoDocument], *, tenant: TenantContext | None = None
    ) -> None:
        super().__init__(database, "creator_profiles", CreatorProfileDocument, tenant=tenant)

    async def create(self, document: CreatorProfileDocument) -> CreatorProfileDocument:
        return await self._create(document)

    async def get(self, profile_id: str) -> CreatorProfileDocument | None:
        identifier = validate_identifier(profile_id)
        return await self._get(self._identity_filter("creator_profile", identifier))

    async def list(
        self,
        *,
        profile_ids: tuple[str, ...] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[CreatorProfileDocument]:
        filters = None
        if profile_ids is not None:
            filters = {"profile_id": {"$in": list(profile_ids)}}
        return await self._page(filters, limit=limit, cursor=cursor)

    async def update(
        self, document: CreatorProfileDocument, *, expected_revision: int
    ) -> CreatorProfileDocument:
        return await self._update(document, expected_revision=expected_revision)


class MongoContentPillarRepository(_ProfileRepository[ContentPillarDocument]):
    """Persist one profile's thematic pillars without cross-profile reads."""

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        profile_id: str,
        *,
        tenant: TenantContext | None = None,
    ) -> None:
        super().__init__(
            database, "content_pillars", ContentPillarDocument, profile_id, tenant=tenant
        )

    async def create(self, document: ContentPillarDocument) -> ContentPillarDocument:
        return await self._create_for_profile(document)

    async def get(self, pillar_id: str) -> ContentPillarDocument | None:
        identifier = validate_identifier(pillar_id)
        return await self._get(
            {
                **self._identity_filter("content_pillar", self.profile_id, identifier),
                "profile_id": self.profile_id,
            }
        )

    async def list(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> Page[ContentPillarDocument]:
        return await self._list_for_profile(limit=limit, cursor=cursor)

    async def update(
        self, document: ContentPillarDocument, *, expected_revision: int
    ) -> ContentPillarDocument:
        return await self._update_for_profile(document, expected_revision=expected_revision)


class MongoContentIdeaRepository(_ContentSearchRepository[ContentIdeaDocument]):
    """Persist one profile's ideas with revision-guarded changes."""

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        profile_id: str,
        *,
        tenant: TenantContext | None = None,
    ) -> None:
        super().__init__(database, "content_ideas", ContentIdeaDocument, profile_id, tenant=tenant)
        self._search_kind = "idea"

    async def create(self, document: ContentIdeaDocument) -> ContentIdeaDocument:
        return await self._create_for_profile(document)

    async def get(self, idea_id: str) -> ContentIdeaDocument | None:
        identifier = validate_identifier(idea_id)
        return await self._get(
            {
                **self._identity_filter("content_idea", self.profile_id, identifier),
                "profile_id": self.profile_id,
            }
        )

    async def list(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> Page[ContentIdeaDocument]:
        return await self._list_for_profile(limit=limit, cursor=cursor)

    async def list_recent(
        self,
        *,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[ContentIdeaDocument, ...]:
        """Return recent ideas for duplicate checks without leaving the profile."""
        return await self._recent_for_profile(
            updated_after=updated_after,
            updated_before=updated_before,
            limit=limit,
        )

    async def update(
        self, document: ContentIdeaDocument, *, expected_revision: int
    ) -> ContentIdeaDocument:
        return await self._update_for_profile(document, expected_revision=expected_revision)


class MongoContentBriefRepository(_ContentSearchRepository[ContentBriefDocument]):
    """Persist profile-scoped content briefs and their source references."""

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        profile_id: str,
        *,
        tenant: TenantContext | None = None,
    ) -> None:
        super().__init__(
            database, "content_briefs", ContentBriefDocument, profile_id, tenant=tenant
        )
        self._search_kind = "brief"

    async def create(self, document: ContentBriefDocument) -> ContentBriefDocument:
        return await self._create_for_profile(document)

    async def get(self, brief_id: str) -> ContentBriefDocument | None:
        identifier = validate_identifier(brief_id)
        return await self._get(
            {
                **self._identity_filter("content_brief", self.profile_id, identifier),
                "profile_id": self.profile_id,
            }
        )

    async def list(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> Page[ContentBriefDocument]:
        return await self._list_for_profile(limit=limit, cursor=cursor)

    async def update(
        self, document: ContentBriefDocument, *, expected_revision: int
    ) -> ContentBriefDocument:
        return await self._update_for_profile(document, expected_revision=expected_revision)


class MongoContentItemRepository(_ContentSearchRepository[ContentItemDocument]):
    """Persist current content copies; append-only history is stored separately."""

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        profile_id: str,
        *,
        tenant: TenantContext | None = None,
    ) -> None:
        super().__init__(database, "content_items", ContentItemDocument, profile_id, tenant=tenant)
        self._versions = database["content_versions"]
        self._search_kind = "item"

    async def create(self, document: ContentItemDocument) -> ContentItemDocument:
        return await self._create_for_profile(document)

    async def get(self, content_id: str) -> ContentItemDocument | None:
        identifier = validate_identifier(content_id)
        return await self._get(
            {
                **self._identity_filter("content_item", self.profile_id, identifier),
                "profile_id": self.profile_id,
            }
        )

    async def list(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> Page[ContentItemDocument]:
        return await self._list_for_profile(limit=limit, cursor=cursor)

    async def list_recent(
        self,
        *,
        statuses: Sequence[ContentStatus] = (),
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[ContentItemDocument, ...]:
        """Return newest current copies, optionally constrained to lifecycle states."""
        filters: MongoDocument = {}
        if statuses:
            filters["status"] = {"$in": list(statuses)}
        return await self._recent_for_profile(
            filters,
            updated_after=updated_after,
            updated_before=updated_before,
            limit=limit,
        )

    async def update(
        self, document: ContentItemDocument, *, expected_revision: int
    ) -> ContentItemDocument:
        return await self._update_for_profile(document, expected_revision=expected_revision)

    async def create_with_initial_version(
        self,
        document: ContentItemDocument,
        version: ContentVersionDocument,
    ) -> ContentItemDocument:
        """Create a current copy and its first immutable snapshot atomically."""
        if document.identity_version == 1 or version.identity_version == 1:
            raise ValueError("only adoption may insert legacy tenant identities")
        self._require_profile(document)
        if version.profile_id != self.profile_id:
            raise ProjectIsolationError("content belongs to a different creator profile")
        if (
            version.content_id != document.content_id
            or version.version != 1
            or document.current_version != 1
        ):
            raise ValueError("initial content version must be version 1 for the item")
        document = attribute(document)
        version = attribute(version)
        validated_item = self._validate(document.model_dump(by_alias=True, mode="python"))
        validated_version = ContentVersionDocument.model_validate(
            version.model_dump(by_alias=True, mode="python")
        )
        item_payload = cast(MongoDocument, validated_item.model_dump(by_alias=True, mode="python"))
        version_payload = cast(
            MongoDocument, validated_version.model_dump(by_alias=True, mode="python")
        )

        async def transaction(session: AsyncClientSession) -> ContentItemDocument:
            await self._collection.insert_one(item_payload, session=session)
            await self._versions.insert_one(version_payload, session=session)
            return validated_item

        try:
            async with self._database.client.start_session() as session:
                return await session.with_transaction(transaction)
        except DuplicateKeyError as error:
            raise DuplicateDocumentError("a document with this key already exists") from error

    async def append_version(
        self,
        document: ContentItemDocument,
        version: ContentVersionDocument,
        *,
        expected_revision: int,
    ) -> ContentItemDocument:
        """Append a snapshot and advance its current copy in one transaction.

        The service chooses the lifecycle transition and snapshot data.  This
        repository only makes their persistence indivisible and refuses a
        stale current-copy revision, so an item can never point to a version
        that was not written.
        """
        self._require_profile(document)
        if version.profile_id != self.profile_id:
            raise ProjectIsolationError("content belongs to a different creator profile")
        if isinstance(expected_revision, bool) or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        if document.revision != expected_revision:
            raise ValueError("document revision must equal expected_revision")
        if (
            version.content_id != document.content_id
            or version.version != document.current_version
            or version.version < 2
        ):
            raise ValueError("version must be the next current version for the item")
        document = attribute(document)
        version = attribute(version)
        validated_item = self._validate(document.model_dump(by_alias=True, mode="python"))
        validated_version = ContentVersionDocument.model_validate(
            version.model_dump(by_alias=True, mode="python")
        )
        changes = cast(MongoDocument, validated_item.model_dump(by_alias=True, mode="python"))
        for immutable_field in (
            "_id",
            "created_at",
            "updated_at",
            "revision",
            "org_id",
            "identity_version",
            "legacy_adoption_id",
        ):
            changes.pop(immutable_field, None)
        changes["updated_at"] = utc_now()
        version_payload = cast(
            MongoDocument, validated_version.model_dump(by_alias=True, mode="python")
        )

        async def transaction(session: AsyncClientSession) -> ContentItemDocument:
            await self._versions.insert_one(version_payload, session=session)
            raw = await self._collection.find_one_and_update(
                self._owned_filter(
                    {
                        "_id": validated_item.id,
                        "profile_id": self.profile_id,
                        "revision": expected_revision,
                        **(
                            {
                                "identity_version": validated_item.identity_version,
                                "legacy_adoption_id": validated_item.legacy_adoption_id,
                            }
                            if validated_item.org_id
                            else {}
                        ),
                    }
                ),
                {"$set": changes, "$inc": {"revision": 1}},
                session=session,
                return_document=True,
            )
            if raw is None:
                raise ConcurrencyConflictError("content item changed during version creation")
            return self._validate(raw)

        try:
            async with self._database.client.start_session() as session:
                return await session.with_transaction(transaction)
        except DuplicateKeyError as error:
            raise DuplicateDocumentError("a document with this key already exists") from error


class MongoContentVersionRepository(_ProfileRepository[ContentVersionDocument]):
    """Append and page immutable content-version snapshots."""

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        profile_id: str,
        *,
        tenant: TenantContext | None = None,
    ) -> None:
        super().__init__(
            database, "content_versions", ContentVersionDocument, profile_id, tenant=tenant
        )

    async def create(self, document: ContentVersionDocument) -> ContentVersionDocument:
        return await self._create_for_profile(document)

    async def get(self, content_id: str, version: int) -> ContentVersionDocument | None:
        content_identifier = validate_identifier(content_id)
        if isinstance(version, bool) or version < 1:
            raise ValueError("version must be a positive integer")
        return await self._get(
            {
                **self._identity_filter(
                    "content_version", self.profile_id, content_identifier, str(version)
                ),
                "profile_id": self.profile_id,
            }
        )

    async def list(
        self,
        *,
        content_id: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ContentVersionDocument]:
        filters: MongoDocument | None = None
        if content_id is not None:
            filters = {"content_id": validate_identifier(content_id)}
        return await self._list_for_profile(filters, limit=limit, cursor=cursor)


class MongoContentLinkRepository(_ProfileRepository[ContentLinkDocument]):
    """Persist profile-scoped source provenance links."""

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        profile_id: str,
        *,
        tenant: TenantContext | None = None,
    ) -> None:
        super().__init__(database, "content_links", ContentLinkDocument, profile_id, tenant=tenant)

    async def create(self, document: ContentLinkDocument) -> ContentLinkDocument:
        return await self._create_for_profile(document)

    async def get(self, link_id: str) -> ContentLinkDocument | None:
        identifier = validate_identifier(link_id)
        return await self._get(
            {
                **self._identity_filter("content_link", self.profile_id, identifier),
                "profile_id": self.profile_id,
            }
        )

    async def list(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> Page[ContentLinkDocument]:
        return await self._list_for_profile(limit=limit, cursor=cursor)

    async def update(
        self, document: ContentLinkDocument, *, expected_revision: int
    ) -> ContentLinkDocument:
        return await self._update_for_profile(document, expected_revision=expected_revision)


class MongoContentMetricRepository(_ProfileRepository[ContentMetricDocument]):
    """Persist immutable observations for later, explicitly requested analysis."""

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        profile_id: str,
        *,
        tenant: TenantContext | None = None,
    ) -> None:
        super().__init__(
            database, "content_metrics", ContentMetricDocument, profile_id, tenant=tenant
        )

    async def create(self, document: ContentMetricDocument) -> ContentMetricDocument:
        return await self._create_for_profile(document)

    async def get(self, metric_id: str) -> ContentMetricDocument | None:
        identifier = validate_identifier(metric_id)
        return await self._get(
            {
                **self._identity_filter("content_metric", self.profile_id, identifier),
                "profile_id": self.profile_id,
            }
        )

    async def list(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> Page[ContentMetricDocument]:
        return await self._list_for_profile(limit=limit, cursor=cursor)

    async def list_for_content(
        self,
        *,
        content_id: str,
        platform: ContentPlatform | None = None,
        captured_after: datetime | None = None,
        captured_before: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[ContentMetricDocument]:
        """Page an immutable metric time series newest-first by capture time."""
        if isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if (
            captured_after is not None
            and captured_before is not None
            and captured_after > captured_before
        ):
            raise ValueError("captured_after must be on or before captured_before")

        filters: MongoDocument = {
            "profile_id": self.profile_id,
            "content_id": validate_identifier(content_id),
        }
        if platform is not None:
            filters["platform"] = platform.value
        capture_bounds: MongoDocument = {}
        if captured_after is not None:
            capture_bounds["$gte"] = captured_after
        if captured_before is not None:
            capture_bounds["$lte"] = captured_before
        if capture_bounds:
            filters["captured_at"] = capture_bounds

        query_parts: list[MongoDocument] = [filters]
        if cursor is not None:
            captured_at, document_id = decode_page_cursor(cursor)
            query_parts.append(
                {
                    "$or": [
                        {"captured_at": {"$lt": captured_at}},
                        {"captured_at": captured_at, "_id": {"$lt": document_id}},
                    ]
                }
            )
        query: MongoDocument = query_parts[0] if len(query_parts) == 1 else {"$and": query_parts}
        mongo_cursor = self._collection.find(query).sort(
            [("captured_at", DESCENDING), ("_id", DESCENDING)]
        )
        raw_documents = await mongo_cursor.limit(limit + 1).to_list(length=limit + 1)
        documents = tuple(self._validate(raw) for raw in raw_documents[:limit])
        next_cursor = (
            encode_page_cursor(documents[-1].captured_at, documents[-1].id)
            if len(raw_documents) > limit and documents
            else None
        )
        return Page(items=documents, next_cursor=next_cursor)


class MongoContentCampaignRepository(_ProfileRepository[ContentCampaignDocument]):
    """Persist profile-owned content campaign metadata."""

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        profile_id: str,
        *,
        tenant: TenantContext | None = None,
    ) -> None:
        super().__init__(
            database, "content_campaigns", ContentCampaignDocument, profile_id, tenant=tenant
        )

    async def create(self, document: ContentCampaignDocument) -> ContentCampaignDocument:
        return await self._create_for_profile(document)

    async def get(self, campaign_id: str) -> ContentCampaignDocument | None:
        identifier = validate_identifier(campaign_id)
        return await self._get(
            {
                **self._identity_filter("content_campaign", self.profile_id, identifier),
                "profile_id": self.profile_id,
            }
        )

    async def list(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> Page[ContentCampaignDocument]:
        return await self._list_for_profile(limit=limit, cursor=cursor)

    async def update(
        self, document: ContentCampaignDocument, *, expected_revision: int
    ) -> ContentCampaignDocument:
        return await self._update_for_profile(document, expected_revision=expected_revision)


def _validate_search_limit(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
