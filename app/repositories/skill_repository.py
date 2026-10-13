"""MongoDB repository and transactional persistence for versioned skills."""

import re
from collections.abc import Sequence
from datetime import datetime
from typing import cast

from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.models.common import Page, deterministic_object_id, utc_now
from app.models.skill import LifecycleStatus, SkillDocument, SkillTrustLevel
from app.models.skill_chunk import SkillChunkDocument
from app.repositories.base import DEFAULT_PAGE_SIZE, MongoRepository
from app.repositories.errors import ConcurrencyConflictError
from app.skills.types import SkillLexicalMatch, SkillVersionKey

# Skill metadata filters are resolved to version keys before a chunk query, so
# the resolved set stays bounded regardless of catalogue size.
MAX_VERSION_KEYS = 500


class MongoSkillRepository(MongoRepository[SkillDocument]):
    """Persist skill versions while limiting updates to lifecycle status."""

    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        super().__init__(database, "skills", SkillDocument)
        self._database = database
        self._chunks = database["skill_chunks"]

    async def create(self, document: SkillDocument) -> SkillDocument:
        return await self._create(document)

    async def get(self, skill_id: str, version: str) -> SkillDocument | None:
        return await self._get({"_id": deterministic_object_id("skill", skill_id, version)})

    async def get_active(self, skill_id: str) -> SkillDocument | None:
        """Return the single active version of a skill."""
        return await self._get({"skill_id": skill_id, "status": "active"})

    async def list(
        self,
        *,
        status: LifecycleStatus | None = None,
        category: str | None = None,
        tag: str | None = None,
        trust_level: SkillTrustLevel | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> Page[SkillDocument]:
        filters: MongoDocument = {}
        if status is not None:
            filters["status"] = status
        if category is not None:
            filters["category"] = category
        if tag is not None:
            filters["tags"] = tag
        if trust_level is not None:
            filters["trust_level"] = trust_level
        return await self._page(filters, limit=limit, cursor=cursor)

    async def list_recent(
        self,
        *,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[SkillDocument, ...]:
        """Return active skill versions that were most recently changed."""
        return await self._recent(
            {"status": "active"},
            updated_after=updated_after,
            updated_before=updated_before,
            limit=limit,
        )

    async def list_version_keys(
        self,
        *,
        status: LifecycleStatus | None = "active",
        skill_id: str | None = None,
        version: str | None = None,
        category: str | None = None,
        tags: Sequence[str] = (),
        trust_level: SkillTrustLevel | None = None,
        limit: int = MAX_VERSION_KEYS,
    ) -> tuple[SkillVersionKey, ...]:
        """Resolve skill metadata filters to bounded `(skill_id, version)` keys.

        Chunk-level queries such as vector search can only filter on chunk
        fields, so skill metadata is translated into the version keys those
        chunks carry.
        """
        if isinstance(limit, bool) or not 1 <= limit <= MAX_VERSION_KEYS:
            raise ValueError(f"limit must be between 1 and {MAX_VERSION_KEYS}")
        filters: MongoDocument = {}
        if status is not None:
            filters["status"] = status
        if skill_id is not None:
            filters["skill_id"] = skill_id
        if version is not None:
            filters["version"] = version
        if category is not None:
            filters["category"] = category
        if tags:
            filters["tags"] = {"$all": list(tags)}
        if trust_level is not None:
            filters["trust_level"] = trust_level
        cursor = self._collection.find(filters, {"skill_id": 1, "version": 1, "_id": 0})
        cursor = cursor.sort([("skill_id", 1), ("version", 1)])
        raw_documents = await cursor.limit(limit).to_list(length=limit)
        keys: list[SkillVersionKey] = []
        for raw in raw_documents:
            resolved_id = raw.get("skill_id")
            resolved_version = raw.get("version")
            if not isinstance(resolved_id, str) or not isinstance(resolved_version, str):
                raise ValueError("MongoDB returned an invalid skill version key")
            keys.append(SkillVersionKey(resolved_id, resolved_version))
        return tuple(keys)

    async def get_many(self, keys: Sequence[SkillVersionKey]) -> tuple[SkillDocument, ...]:
        """Load a bounded set of exact skill versions in one query."""
        unique = tuple(dict.fromkeys(keys))
        if not unique:
            return ()
        if len(unique) > MAX_VERSION_KEYS:
            raise ValueError(f"at most {MAX_VERSION_KEYS} skill versions can be loaded at once")
        document_ids = [
            deterministic_object_id("skill", key.skill_id, key.version) for key in unique
        ]
        cursor = self._collection.find({"_id": {"$in": document_ids}})
        raw_documents = await cursor.limit(len(document_ids)).to_list(length=len(document_ids))
        return tuple(self._validate(raw) for raw in raw_documents)

    async def list_versions(self, skill_id: str, *, limit: int = 100) -> tuple[SkillDocument, ...]:
        """Return bounded version history in newest-imported-first order."""
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("version limit must be between 1 and 100")
        cursor = self._collection.find({"skill_id": skill_id})
        cursor = cursor.sort([("created_at", -1), ("_id", -1)])
        raw_documents = await cursor.limit(limit).to_list(length=limit)
        return tuple(self._validate(raw) for raw in raw_documents)

    async def search_active_lexical(
        self,
        query: str,
        *,
        category: str | None = None,
        tags: Sequence[str] = (),
        trust_level: SkillTrustLevel | None = None,
        limit: int = 10,
    ) -> tuple[SkillLexicalMatch, ...]:
        """Match escaped literal terms in active metadata and stored chunk content."""
        if isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("search limit must be between 1 and 50")
        normalized = " ".join(query.split())
        if not normalized:
            raise ValueError("search query must not be empty")
        raw_terms = (normalized, *normalized.split())
        terms = tuple(dict.fromkeys(raw_terms))[:8]
        patterns = [re.compile(re.escape(term), re.IGNORECASE) for term in terms]
        metadata_conditions: list[MongoDocument] = [
            {field: pattern}
            for field in ("skill_id", "name", "description", "category", "tags")
            for pattern in patterns
        ]
        chunk_conditions: list[MongoDocument] = [{"content": pattern} for pattern in patterns]
        base_match: MongoDocument = {"status": "active"}
        if category is not None:
            base_match["category"] = category
        if tags:
            base_match["tags"] = {"$all": list(tags)}
        if trust_level is not None:
            base_match["trust_level"] = trust_level

        pipeline: list[MongoDocument] = [
            {"$match": base_match},
            {
                "$lookup": {
                    "from": "skill_chunks",
                    "let": {"skill_id": "$skill_id", "skill_version": "$version"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$skill_id", "$$skill_id"]},
                                        {"$eq": ["$skill_version", "$$skill_version"]},
                                    ]
                                },
                                "$or": chunk_conditions,
                            }
                        },
                        {"$sort": {"chunk_index": 1}},
                        {"$limit": 3},
                    ],
                    "as": "matched_chunks",
                }
            },
            {"$match": {"$or": [*metadata_conditions, {"matched_chunks.0": {"$exists": True}}]}},
            {"$sort": {"name": 1, "version": -1}},
            {"$limit": limit},
        ]
        cursor = await self._collection.aggregate(pipeline)
        raw_matches = await cursor.to_list(length=limit)
        matches: list[SkillLexicalMatch] = []
        for raw_match in raw_matches:
            raw_chunks = raw_match.pop("matched_chunks", [])
            if not isinstance(raw_chunks, list):
                raise ValueError("MongoDB returned invalid matched skill chunks")
            skill = self._validate(raw_match)
            chunks = tuple(SkillChunkDocument.model_validate(raw) for raw in raw_chunks)
            matches.append(SkillLexicalMatch(skill=skill, matched_chunks=chunks))
        return tuple(matches)

    async def install_version(
        self,
        skill: SkillDocument,
        chunks: Sequence[SkillChunkDocument],
    ) -> bool:
        """Atomically preserve the old version and install one active version.

        Returns false when the exact version already exists, allowing the service
        layer to classify a concurrent retry as unchanged or conflicting.
        """
        validated_skill = self._validate(skill.model_dump(by_alias=True, mode="python"))
        if validated_skill.status != "active":
            raise ValueError("a newly installed skill version must be active")
        if not chunks:
            raise ValueError("a skill version must contain at least one chunk")
        validated_chunks = tuple(
            SkillChunkDocument.model_validate(chunk.model_dump(by_alias=True, mode="python"))
            for chunk in chunks
        )
        if any(
            chunk.skill_id != validated_skill.skill_id
            or chunk.skill_version != validated_skill.version
            or chunk.chunk_index != index
            for index, chunk in enumerate(validated_chunks)
        ):
            raise ValueError("skill chunks must be contiguous and match the installed version")
        skill_payload = cast(
            MongoDocument,
            validated_skill.model_dump(by_alias=True, mode="python"),
        )
        chunk_payloads = [
            cast(MongoDocument, chunk.model_dump(by_alias=True, mode="python"))
            for chunk in validated_chunks
        ]

        async def transaction(session: AsyncClientSession) -> bool:
            existing = await self._collection.find_one(
                {"_id": validated_skill.id},
                session=session,
            )
            if existing is not None:
                return False
            current_raw = await self._collection.find_one(
                {"skill_id": validated_skill.skill_id, "status": "active"},
                session=session,
            )
            if current_raw is not None:
                current = self._validate(current_raw)
                result = await self._collection.update_one(
                    {"_id": current.id, "revision": current.revision, "status": "active"},
                    {
                        "$set": {"status": "superseded", "updated_at": utc_now()},
                        "$inc": {"revision": 1},
                    },
                    session=session,
                )
                if result.modified_count != 1:
                    raise ConcurrencyConflictError("active skill version changed during import")
            await self._collection.insert_one(skill_payload, session=session)
            await self._chunks.insert_many(chunk_payloads, ordered=True, session=session)
            return True

        async with self._database.client.start_session() as session:
            return await session.with_transaction(transaction)

    async def set_status(
        self,
        skill_id: str,
        version: str,
        status: LifecycleStatus,
        *,
        expected_revision: int,
    ) -> SkillDocument:
        document_id = deterministic_object_id("skill", skill_id, version)
        return await self._update_fields(
            document_id,
            expected_revision=expected_revision,
            changes={"status": status},
        )

    async def delete(self, skill_id: str, version: str, *, expected_revision: int) -> bool:
        document_id: ObjectId = deterministic_object_id("skill", skill_id, version)
        return await self._delete(document_id, expected_revision=expected_revision)
