"""Reusable typed MongoDB CRUD, search, and keyset-pagination primitives."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from math import isfinite
from time import perf_counter
from typing import Literal, cast

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.db.client_types import MongoDocument
from app.db.errors import SearchUnavailableError
from app.models.common import (
    MongoDocumentModel,
    MongoObjectId,
    Page,
    decode_page_cursor,
    deterministic_object_id,
    encode_page_cursor,
    utc_now,
)
from app.observability.instruments import record_search
from app.provenance import attribute
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError
from app.security.tenant import TenantContext, TenantContextRequired, current_tenant

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
SCORE_FIELD = "score"
# Which of a collection's two indexes a search pipeline queries. Named here
# rather than imported from one domain, because every collection has both and
# this base class belongs to none of them.
SearchMethod = Literal["vector", "lexical"]


class MongoRepository[TDocument: MongoDocumentModel]:
    """Protected CRUD operations shared by domain-specific repositories."""

    def __init__(
        self,
        database: AsyncDatabase[MongoDocument],
        collection_name: str,
        model_type: type[TDocument],
        *,
        tenant: TenantContext | None = None,
    ) -> None:
        self._collection: AsyncCollection[MongoDocument] = database[collection_name]
        # Kept as the name this repository was built for rather than read back
        # off the driver handle: it is what labels this collection's search
        # metrics, and a label has to be a string the application chose.
        self._collection_name = collection_name
        self._model_type = model_type
        self._tenant = tenant or current_tenant()

    def _owned_filter(self, filters: Mapping[str, object]) -> MongoDocument:
        query = dict(filters)
        if self._tenant is not None:
            if "org_id" in query and query["org_id"] != self._tenant.org_id:
                raise TenantContextRequired("query organization conflicts with trusted context")
            query["org_id"] = self._tenant.org_id
        return query

    def _identity_filter(self, namespace: str, *parts: str) -> MongoDocument:
        if self._tenant is None:
            return {"_id": deterministic_object_id(namespace, *parts)}
        from app.db.tenant_inventory import IDENTITY_FIELDS

        fields = IDENTITY_FIELDS[namespace]
        values: list[object] = list(parts)
        if namespace == "content_version":
            values[-1] = int(parts[-1])
        return self._owned_filter(dict(zip(fields, values, strict=True)))

    async def _identity_record_id(self, namespace: str, *parts: str) -> ObjectId:
        if self._tenant is None:
            return deterministic_object_id(namespace, *parts)
        document = await self._get(self._identity_filter(namespace, *parts))
        if document is None:
            raise ConcurrencyConflictError("document is absent or was concurrently modified")
        return document.id

    def _validate(self, value: object) -> TDocument:
        document = self._model_type.model_validate(value, context={"from_storage": True})
        if self._tenant is not None and document.org_id != self._tenant.org_id:
            raise TenantContextRequired("document organization conflicts with trusted context")
        return document

    async def _create(self, document: TDocument) -> TDocument:
        if document.identity_version == 1:
            raise ValueError("only adoption may insert legacy tenant identities")
        if document.org_id is not None and self._tenant is None:
            raise TenantContextRequired("tenant writes require trusted organization context")
        document = attribute(document)
        validated = self._validate(document.model_dump(by_alias=True, mode="python"))
        payload = cast(
            MongoDocument,
            validated.model_dump(by_alias=True, mode="python"),
        )
        try:
            await self._collection.insert_one(payload)
        except DuplicateKeyError as error:
            # A deterministic natural key is already taken, which the caller
            # decides how to treat: a conflict, or an idempotent repeat.
            raise DuplicateDocumentError("a document with this key already exists") from error
        return validated

    async def _get(self, filters: Mapping[str, object]) -> TDocument | None:
        raw = await self._collection.find_one(self._owned_filter(filters))
        return self._validate(raw) if raw is not None else None

    async def _page(
        self,
        filters: Mapping[str, object] | None = None,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[TDocument]:
        if isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")

        query_parts: list[MongoDocument] = []
        if filters:
            query_parts.append(dict(filters))
        if cursor is not None:
            created_at, document_id = decode_page_cursor(cursor)
            query_parts.append(
                {
                    "$or": [
                        {"created_at": {"$gt": created_at}},
                        {"created_at": created_at, "_id": {"$gt": document_id}},
                    ]
                }
            )
        query: MongoDocument
        if not query_parts:
            query = {}
        elif len(query_parts) == 1:
            query = query_parts[0]
        else:
            query = {"$and": query_parts}

        mongo_cursor = self._collection.find(self._owned_filter(query))
        mongo_cursor = mongo_cursor.sort([("created_at", ASCENDING), ("_id", ASCENDING)])
        raw_documents = await mongo_cursor.limit(limit + 1).to_list(length=limit + 1)
        has_more = len(raw_documents) > limit
        documents = tuple(self._validate(raw) for raw in raw_documents[:limit])
        next_cursor = None
        if has_more and documents:
            last = documents[-1]
            next_cursor = encode_page_cursor(last.created_at, last.id)
        return Page[TDocument](items=documents, next_cursor=next_cursor)

    async def _recent(
        self,
        filters: Mapping[str, object] | None = None,
        *,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[TDocument, ...]:
        """Return a bounded newest-first slice without weakening collection scope.

        Discovery workflows need records in recency order rather than cursor
        order.  The owning repository supplies the scope filters, while this
        shared primitive keeps the time window and hard limit consistent.
        """
        if isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        query: MongoDocument = {} if filters is None else dict(filters)
        bounds: MongoDocument = {}
        if updated_after is not None:
            bounds["$gte"] = updated_after
        if updated_before is not None:
            bounds["$lte"] = updated_before
        if bounds:
            query["updated_at"] = bounds
        cursor = self._collection.find(self._owned_filter(query))
        cursor = cursor.sort([("updated_at", DESCENDING), ("_id", DESCENDING)])
        raw_documents = await cursor.limit(limit).to_list(length=limit)
        return tuple(self._validate(raw) for raw in raw_documents)

    async def _scored_search(
        self,
        pipeline: Sequence[MongoDocument],
        limit: int,
        *,
        method: SearchMethod,
    ) -> tuple[tuple[TDocument, float], ...]:
        """Run one search pipeline and validate the scored documents it returns.

        A missing or unusable score means the pipeline did not run as written,
        so it is reported as unavailable search rather than ranked as zero.

        Every vector and lexical query in the platform ends up here, which is
        why this is where they are measured: one place gives each collection's
        two indexes their own latency and their own failure count, and no
        future search can be added without being counted.
        """
        started = perf_counter()
        failed = True
        try:
            scored = await self._search(pipeline, limit)
            failed = False
            return scored
        finally:
            record_search(
                method=method,
                collection=self._collection_name,
                seconds=perf_counter() - started,
                failed=failed,
            )

    async def _search(
        self,
        pipeline: Sequence[MongoDocument],
        limit: int,
    ) -> tuple[tuple[TDocument, float], ...]:
        """Run one search pipeline, reporting any refusal as unavailable search."""
        try:
            cursor = await self._collection.aggregate(list(pipeline))
            raw_documents = await cursor.to_list(length=limit)
        except PyMongoError as error:
            raise SearchUnavailableError("the search query could not be served") from error

        scored: list[tuple[TDocument, float]] = []
        for raw in raw_documents:
            document = dict(raw)
            score = document.pop(SCORE_FIELD, None)
            if isinstance(score, bool) or not isinstance(score, int | float) or not isfinite(score):
                raise SearchUnavailableError("the search returned an unusable score")
            scored.append((self._validate(document), float(score)))
        return tuple(scored)

    async def _update(
        self,
        document: TDocument,
        *,
        expected_revision: int,
        filters: Mapping[str, object] | None = None,
    ) -> TDocument:
        if isinstance(expected_revision, bool) or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        if document.revision != expected_revision:
            raise ValueError("document revision must equal expected_revision")

        document = attribute(document)
        validated = self._validate(document.model_dump(by_alias=True, mode="python"))
        changes = cast(
            MongoDocument,
            validated.model_dump(by_alias=True, mode="python"),
        )
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

        query: MongoDocument = {"_id": validated.id, "revision": expected_revision}
        if filters:
            query.update(filters)
        if document.org_id is not None:
            if self._tenant is None:
                raise TenantContextRequired("tenant writes require trusted organization context")
            query.update(
                org_id=document.org_id,
                identity_version=document.identity_version,
                legacy_adoption_id=document.legacy_adoption_id,
            )
        raw = await self._collection.find_one_and_update(
            self._owned_filter(query),
            {"$set": changes, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if raw is None:
            raise ConcurrencyConflictError("document is absent or was concurrently modified")
        return self._validate(raw)

    async def _update_fields(
        self,
        document_id: MongoObjectId,
        *,
        expected_revision: int,
        changes: Mapping[str, object],
        filters: Mapping[str, object] | None = None,
    ) -> TDocument:
        if isinstance(expected_revision, bool) or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        update_fields = dict(changes)
        if any(
            key.split(".", 1)[0] in {"org_id", "identity_version", "legacy_adoption_id", "_id"}
            for key in update_fields
        ):
            raise ValueError("ownership and identity metadata are immutable")
        update_fields["updated_at"] = utc_now()
        query: MongoDocument = {"_id": document_id, "revision": expected_revision}
        if filters:
            query.update(filters)
        raw = await self._collection.find_one_and_update(
            self._owned_filter(query),
            {"$set": update_fields, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if raw is None:
            raise ConcurrencyConflictError("document is absent or was concurrently modified")
        return self._validate(raw)

    async def _delete(
        self,
        document_id: ObjectId,
        *,
        expected_revision: int,
        filters: Mapping[str, object] | None = None,
    ) -> bool:
        query: MongoDocument = {"_id": document_id, "revision": expected_revision}
        if filters:
            query.update(filters)
        result = await self._collection.delete_one(self._owned_filter(query))
        return result.deleted_count == 1
