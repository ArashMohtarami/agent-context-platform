"""MongoDB repository for skill chunks, including vector retrieval."""

from collections.abc import Sequence

from bson import ObjectId
from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.text_indexes import (
    SKILL_CHUNK_TEXT_INDEX,
    skill_chunk_text_query,
    version_filter_clauses,
)
from app.db.vector_indexes import SKILL_CHUNK_VECTOR_INDEX, VECTOR_PATH
from app.models.common import Page
from app.models.skill_chunk import SkillChunkDocument
from app.repositories.base import (
    DEFAULT_PAGE_SIZE,
    SCORE_FIELD,
    MongoRepository,
    SearchMethod,
)
from app.repositories.vectors import validated_vector
from app.security.tenant import TenantContext
from app.security.tenant_cursor import binding, decode_chunk_cursor, encode_chunk_cursor
from app.skills.types import SkillChunkMatch, SkillVersionKey

DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50
# MongoDB rejects a larger approximate-nearest-neighbour candidate pool.
MAX_NUM_CANDIDATES = 10_000


class MongoSkillChunkRepository(MongoRepository[SkillChunkDocument]):
    """Persist deterministic chunks and run vector queries over their embeddings."""

    def __init__(
        self, database: AsyncDatabase[MongoDocument], *, tenant: TenantContext | None = None
    ) -> None:
        super().__init__(database, "skill_chunks", SkillChunkDocument, tenant=tenant)

    async def create(self, document: SkillChunkDocument) -> SkillChunkDocument:
        return await self._create(document)

    async def get(
        self,
        skill_id: str,
        skill_version: str,
        chunk_id: str,
    ) -> SkillChunkDocument | None:
        return await self._get(
            self._identity_filter("skill_chunk", skill_id, skill_version, chunk_id)
        )

    async def list_for_version(
        self,
        skill_id: str,
        skill_version: str,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[SkillChunkDocument]:
        """List chunks in canonical chunk-index order with an opaque cursor."""
        if isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        filters: MongoDocument = {"skill_id": skill_id, "skill_version": skill_version}
        cursor_scope = binding("skill_chunks", filters)
        if cursor is not None:
            chunk_index, document_id = decode_chunk_cursor(cursor, cursor_scope)
            filters["$or"] = [
                {"chunk_index": {"$gt": chunk_index}},
                {"chunk_index": chunk_index, "_id": {"$gt": document_id}},
            ]
        mongo_cursor = self._collection.find(filters)
        mongo_cursor = mongo_cursor.sort([("chunk_index", ASCENDING), ("_id", ASCENDING)])
        raw_documents = await mongo_cursor.limit(limit + 1).to_list(length=limit + 1)
        has_more = len(raw_documents) > limit
        documents = tuple(self._validate(raw) for raw in raw_documents[:limit])
        next_cursor = None
        if has_more and documents:
            last = documents[-1]
            next_cursor = encode_chunk_cursor(last.chunk_index, last.id, cursor_scope)
        return Page(items=documents, next_cursor=next_cursor)

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        num_candidates: int,
        skill_versions: Sequence[SkillVersionKey] = (),
    ) -> tuple[SkillChunkMatch, ...]:
        """Return the most similar chunks with their similarity scores.

        `skill_versions` pre-filters inside the approximate search, which keeps
        the requested number of results reachable instead of discarding them
        afterwards. An empty sequence searches every indexed chunk.
        """
        query_vector = validated_vector(embedding)
        if isinstance(limit, bool) or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        if isinstance(num_candidates, bool) or not limit <= num_candidates <= MAX_NUM_CANDIDATES:
            raise ValueError(f"num_candidates must be between limit and {MAX_NUM_CANDIDATES}")

        search_stage: MongoDocument = {
            "index": SKILL_CHUNK_VECTOR_INDEX,
            "path": VECTOR_PATH,
            "queryVector": query_vector,
            "numCandidates": num_candidates,
            "limit": limit,
        }
        version_filter = _version_filter(skill_versions)
        if version_filter is not None:
            search_stage["filter"] = version_filter
        pipeline: list[MongoDocument] = [
            {"$vectorSearch": search_stage},
            {"$addFields": {SCORE_FIELD: {"$meta": "vectorSearchScore"}}},
        ]
        return await self._scored_matches(pipeline, limit, method="vector")

    async def text_search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        phrase: bool = False,
        skill_versions: Sequence[SkillVersionKey] = (),
    ) -> tuple[SkillChunkMatch, ...]:
        """Return the chunks matching a lexical query with relevance scores.

        `skill_versions` scopes the query with non-scoring filter clauses, so
        metadata filtering changes which chunks are eligible without distorting
        how relevant they are to each other.
        """
        if isinstance(limit, bool) or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        compound = skill_chunk_text_query(query, phrase=phrase)
        filters = version_filter_clauses(skill_versions)
        if filters:
            compound = {**compound, "filter": filters}
        pipeline: list[MongoDocument] = [
            {"$search": {"index": SKILL_CHUNK_TEXT_INDEX, "compound": compound}},
            {"$addFields": {SCORE_FIELD: {"$meta": "searchScore"}}},
            {"$limit": limit},
        ]
        return await self._scored_matches(pipeline, limit, method="lexical")

    async def _scored_matches(
        self,
        pipeline: list[MongoDocument],
        limit: int,
        *,
        method: SearchMethod,
    ) -> tuple[SkillChunkMatch, ...]:
        """Run one search pipeline and return its scored chunks."""
        scored = await self._scored_search(pipeline, limit, method=method)
        return tuple(SkillChunkMatch(chunk=chunk, score=score) for chunk, score in scored)

    async def update(
        self,
        document: SkillChunkDocument,
        *,
        expected_revision: int,
    ) -> SkillChunkDocument:
        return await self._update(document, expected_revision=expected_revision)

    async def delete(
        self,
        skill_id: str,
        skill_version: str,
        chunk_id: str,
        *,
        expected_revision: int,
    ) -> bool:
        document_id: ObjectId = await self._identity_record_id(
            "skill_chunk", skill_id, skill_version, chunk_id
        )
        return await self._delete(document_id, expected_revision=expected_revision)


def _version_filter(skill_versions: Sequence[SkillVersionKey]) -> MongoDocument | None:
    """Build a vector-search pre-filter over indexed chunk filter fields."""
    clauses: list[MongoDocument] = [
        {
            "$and": [
                {"skill_id": {"$eq": key.skill_id}},
                {"skill_version": {"$eq": key.version}},
            ]
        }
        for key in skill_versions
    ]
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$or": clauses}
