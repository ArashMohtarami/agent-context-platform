"""MongoDB Search and Vector Search definitions for V2 content retrieval.

Each collection has its own index because its searchable representation and
provenance shape differ.  The query filter is nevertheless one typed value, so
the API, repository, and both index kinds apply the same ownership and metadata
constraints before ranking.
"""

from dataclasses import dataclass
from typing import Final, Literal

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.search_indexes import SearchIndexAction, describe_search_index, reconcile_search_index
from app.db.text_indexes import (
    compound_text_query,
    identifier_analyzer_definition,
    searchable_text_field,
    text_definition_matches,
)
from app.db.vector_indexes import (
    VECTOR_INDEX_TYPE,
    VECTOR_PATH,
    VECTOR_SIMILARITY,
    vector_definition_matches,
)
from app.embeddings.types import MAX_EMBEDDING_DIMENSIONS
from app.models.content import ContentPlatform

ContentCollectionKind = Literal["idea", "brief", "item"]

CONTENT_COLLECTIONS: Final[dict[ContentCollectionKind, str]] = {
    "idea": "content_ideas",
    "brief": "content_briefs",
    "item": "content_items",
}
CONTENT_VECTOR_INDEXES: Final[dict[ContentCollectionKind, str]] = {
    "idea": "content_idea_vector_index",
    "brief": "content_brief_vector_index",
    "item": "content_item_vector_index",
}
CONTENT_TEXT_INDEXES: Final[dict[ContentCollectionKind, str]] = {
    "idea": "content_idea_text_index",
    "brief": "content_brief_text_index",
    "item": "content_item_text_index",
}

_PROFILE_PATH: Final = "profile_id"
_PLATFORM_PATHS: Final[dict[ContentCollectionKind, str]] = {
    "idea": "platforms",
    "brief": "platform",
    "item": "platform",
}
_PROJECT_PATHS: Final[dict[ContentCollectionKind, str]] = {
    "idea": "origin.project_id",
    "brief": "source_refs.project_id",
    "item": "project_ids",
}
_PILLAR_PATHS: Final[dict[ContentCollectionKind, str | None]] = {
    "idea": "pillar_id",
    "brief": None,
    "item": "pillar_id",
}
_STATUS_PATH: Final = "status"
_TAGS_PATH: Final = "tags"
_FILTER_PATHS: Final[dict[ContentCollectionKind, tuple[str, ...]]] = {
    kind: tuple(
        path
        for path in (
            _PROFILE_PATH,
            _PLATFORM_PATHS[kind],
            _PROJECT_PATHS[kind],
            _PILLAR_PATHS[kind],
            _STATUS_PATH,
            _TAGS_PATH,
        )
        if path is not None
    )
    for kind in CONTENT_COLLECTIONS
}
_PRIMARY_TEXT_PATHS: Final[dict[ContentCollectionKind, str]] = {
    "idea": "description",
    "brief": "primary_message",
    "item": "body",
}
_SECONDARY_TEXT_PATHS: Final[dict[ContentCollectionKind, tuple[tuple[str, float], ...]]] = {
    "idea": (("title", 1.5),),
    "brief": (("objective", 1.5), ("supporting_points", 0.75)),
    "item": (("title", 1.5),),
}


@dataclass(frozen=True, slots=True)
class ContentQueryFilters:
    """One content query bound to an owning profile and optional restrictions."""

    profile_id: str
    platform: ContentPlatform | None = None
    project_id: str | None = None
    status: str | None = None
    pillar_id: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("content queries require a creator profile")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("content query tags must be unique")


def content_vector_definition(kind: ContentCollectionKind, dimensions: int) -> MongoDocument:
    """Build one collection's vector definition and every pre-filter it supports."""
    if isinstance(dimensions, bool) or not isinstance(dimensions, int):
        raise ValueError("vector dimensions must be an integer")
    if not 1 <= dimensions <= MAX_EMBEDDING_DIMENSIONS:
        raise ValueError(f"vector dimensions must be between 1 and {MAX_EMBEDDING_DIMENSIONS}")
    _require_kind(kind)
    return {
        "fields": [
            {
                "type": "vector",
                "path": VECTOR_PATH,
                "numDimensions": dimensions,
                "similarity": VECTOR_SIMILARITY,
            },
            *({"type": "filter", "path": path} for path in _FILTER_PATHS[kind]),
        ]
    }


def content_text_definition(kind: ContentCollectionKind) -> MongoDocument:
    """Build one collection's lexical mapping, including all filter paths."""
    _require_kind(kind)
    fields: MongoDocument = {
        _PRIMARY_TEXT_PATHS[kind]: searchable_text_field(exact=True),
        _PROFILE_PATH: {"type": "token"},
        _PLATFORM_PATHS[kind]: {"type": "token"},
        _PROJECT_PATHS[kind]: {"type": "token"},
        _STATUS_PATH: {"type": "token"},
        _TAGS_PATH: {"type": "token"},
    }
    pillar = _PILLAR_PATHS[kind]
    if pillar is not None:
        fields[pillar] = {"type": "token"}
    for path, _ in _SECONDARY_TEXT_PATHS[kind]:
        fields[path] = searchable_text_field()
    return {
        "analyzers": [identifier_analyzer_definition()],
        "mappings": {"dynamic": False, "fields": fields},
    }


def content_text_query(
    kind: ContentCollectionKind, query: str, *, phrase: bool = False
) -> MongoDocument:
    """Build the lexical relevance query for one content representation."""
    _require_kind(kind)
    return compound_text_query(
        query,
        content_path=_PRIMARY_TEXT_PATHS[kind],
        secondary=_SECONDARY_TEXT_PATHS[kind],
        phrase=phrase,
    )


def content_vector_filter(
    kind: ContentCollectionKind, filters: ContentQueryFilters
) -> MongoDocument:
    """Build the `$vectorSearch` filter; profile ownership is never optional."""
    clauses = _vector_filter_clauses(kind, filters)
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def content_filter_clauses(
    kind: ContentCollectionKind, filters: ContentQueryFilters
) -> list[MongoDocument]:
    """Build `$search.compound.filter` clauses matching the vector filter exactly."""
    _require_kind(kind)
    clauses: list[MongoDocument] = [
        {"equals": {"path": _PROFILE_PATH, "value": filters.profile_id}}
    ]
    platform_path = _PLATFORM_PATHS[kind]
    if filters.platform is not None:
        clauses.append({"equals": {"path": platform_path, "value": str(filters.platform)}})
    if filters.project_id is not None:
        clauses.append({"equals": {"path": _PROJECT_PATHS[kind], "value": filters.project_id}})
    pillar_path = _PILLAR_PATHS[kind]
    if filters.pillar_id is not None and pillar_path is not None:
        clauses.append({"equals": {"path": pillar_path, "value": filters.pillar_id}})
    if filters.status is not None:
        clauses.append({"equals": {"path": _STATUS_PATH, "value": filters.status}})
    clauses.extend({"equals": {"path": _TAGS_PATH, "value": tag}} for tag in filters.tags)
    return clauses


async def ensure_content_vector_indexes(
    database: AsyncDatabase[MongoDocument], *, dimensions: int
) -> tuple[SearchIndexAction, ...]:
    """Idempotently reconcile all current-content vector indexes."""
    actions: list[SearchIndexAction] = []
    for kind in CONTENT_COLLECTIONS:
        actions.append(await _ensure_vector_index(database, kind, dimensions))
    return tuple(actions)


async def ensure_content_text_indexes(
    database: AsyncDatabase[MongoDocument],
) -> tuple[SearchIndexAction, ...]:
    """Idempotently reconcile all current-content lexical indexes."""
    actions: list[SearchIndexAction] = []
    for kind in CONTENT_COLLECTIONS:
        actions.append(await _ensure_text_index(database, kind))
    return tuple(actions)


async def _ensure_vector_index(
    database: AsyncDatabase[MongoDocument], kind: ContentCollectionKind, dimensions: int
) -> SearchIndexAction:
    collection = CONTENT_COLLECTIONS[kind]
    name = CONTENT_VECTOR_INDEXES[kind]
    definition = content_vector_definition(kind, dimensions)
    existing = await describe_search_index(database, collection, name)
    return await reconcile_search_index(
        database,
        collection,
        name=name,
        index_type=VECTOR_INDEX_TYPE,
        definition=definition,
        matches=vector_definition_matches(existing.definition, definition),
        existing=existing,
    )


async def _ensure_text_index(
    database: AsyncDatabase[MongoDocument], kind: ContentCollectionKind
) -> SearchIndexAction:
    collection = CONTENT_COLLECTIONS[kind]
    name = CONTENT_TEXT_INDEXES[kind]
    definition = content_text_definition(kind)
    existing = await describe_search_index(database, collection, name)
    return await reconcile_search_index(
        database,
        collection,
        name=name,
        index_type="search",
        definition=definition,
        matches=text_definition_matches(existing.definition, definition),
        existing=existing,
    )


def _vector_filter_clauses(
    kind: ContentCollectionKind, filters: ContentQueryFilters
) -> list[MongoDocument]:
    _require_kind(kind)
    clauses: list[MongoDocument] = [{_PROFILE_PATH: {"$eq": filters.profile_id}}]
    if filters.platform is not None:
        clauses.append({_PLATFORM_PATHS[kind]: {"$eq": str(filters.platform)}})
    if filters.project_id is not None:
        clauses.append({_PROJECT_PATHS[kind]: {"$eq": filters.project_id}})
    pillar_path = _PILLAR_PATHS[kind]
    if filters.pillar_id is not None and pillar_path is not None:
        clauses.append({pillar_path: {"$eq": filters.pillar_id}})
    if filters.status is not None:
        clauses.append({_STATUS_PATH: {"$eq": filters.status}})
    clauses.extend({_TAGS_PATH: {"$eq": tag}} for tag in filters.tags)
    return clauses


def _require_kind(kind: ContentCollectionKind) -> None:
    if kind not in CONTENT_COLLECTIONS:
        raise ValueError("unknown content collection kind")


__all__ = [
    "CONTENT_COLLECTIONS",
    "CONTENT_TEXT_INDEXES",
    "CONTENT_VECTOR_INDEXES",
    "ContentCollectionKind",
    "ContentQueryFilters",
    "content_filter_clauses",
    "content_text_definition",
    "content_text_query",
    "content_vector_definition",
    "content_vector_filter",
    "ensure_content_text_indexes",
    "ensure_content_vector_indexes",
]
