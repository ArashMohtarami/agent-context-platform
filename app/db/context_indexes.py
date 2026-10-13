"""Search indexes for project context: definitions, queries, and health.

Project context is retrieved the same two ways skill chunks are — by vector
similarity and by exact lexical match — so it carries its own pair of indexes on
its own collection, built from the shared primitives.

Unlike skill chunks, a context document carries its own metadata, so every
supported filter, project isolation included, is a pre-filter on the same
document rather than a scope resolved from another collection.
"""

from dataclasses import dataclass
from typing import Final

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.errors import SearchUnavailableError
from app.db.search_indexes import (
    SearchIndexAction,
    describe_search_index,
    positive_int,
    reconcile_search_index,
    text_value,
)
from app.db.text_indexes import (
    STANDARD_ANALYZER,
    TextIndexStatus,
    TextSearchHealth,
    compound_text_query,
    content_analyzer,
    identifier_analyzer_definition,
    searchable_text_field,
    text_definition_matches,
)
from app.db.vector_indexes import (
    VECTOR_INDEX_TYPE,
    VECTOR_PATH,
    VECTOR_SIMILARITY,
    VectorIndexStatus,
    VectorSearchHealth,
    vector_definition_matches,
    vector_field,
)
from app.embeddings.types import MAX_EMBEDDING_DIMENSIONS
from app.models.project_context import ContextType
from app.models.skill import LifecycleStatus

CONTEXT_COLLECTION: Final = "project_context"
CONTEXT_VECTOR_INDEX: Final = "project_context_vector_index"
CONTEXT_TEXT_INDEX: Final = "project_context_text_index"
CONTENT_PATH: Final = "content"
TITLE_PATH: Final = "title"
PROJECT_PATH: Final = "project_id"
TYPE_PATH: Final = "type"
STATUS_PATH: Final = "status"
TAGS_PATH: Final = "tags"
IMPORTANCE_PATH: Final = "importance"
# Every field a context query may restrict, so no filter costs recall by being
# applied after the search instead of inside it.
CONTEXT_FILTER_PATHS: Final[tuple[str, ...]] = (
    PROJECT_PATH,
    TYPE_PATH,
    STATUS_PATH,
    TAGS_PATH,
    IMPORTANCE_PATH,
)
# A context title states what the entry is about, so it is a stronger signal
# than a section heading inside a longer document.
TITLE_BOOST: Final = 1.5

ContextIndexAction = SearchIndexAction


@dataclass(frozen=True, slots=True)
class ContextQueryFilters:
    """The scope one project-context query is restricted to.

    `project_id` has no default and is never optional: a context query that is
    not bound to a project cannot exist.
    """

    project_id: str
    types: tuple[ContextType, ...] = ()
    tags: tuple[str, ...] = ()
    status: LifecycleStatus | None = "active"
    minimum_importance: float = 0.0

    def __post_init__(self) -> None:
        """Reject a scope that could not be applied as written."""
        if not self.project_id:
            raise ValueError("project-context queries require a project")
        if not 0.0 <= self.minimum_importance <= 1.0:
            raise ValueError("minimum_importance must be between 0 and 1")


def context_vector_definition(dimensions: int) -> MongoDocument:
    """Build the project-context vector index definition."""
    if isinstance(dimensions, bool) or not isinstance(dimensions, int):
        raise ValueError("vector dimensions must be an integer")
    if not 1 <= dimensions <= MAX_EMBEDDING_DIMENSIONS:
        raise ValueError(f"vector dimensions must be between 1 and {MAX_EMBEDDING_DIMENSIONS}")
    return {
        "fields": [
            {
                "type": "vector",
                "path": VECTOR_PATH,
                "numDimensions": dimensions,
                "similarity": VECTOR_SIMILARITY,
            },
            *({"type": "filter", "path": path} for path in CONTEXT_FILTER_PATHS),
        ]
    }


def context_text_definition() -> MongoDocument:
    """Build the project-context Search index definition."""
    return {
        "analyzers": [identifier_analyzer_definition()],
        "mappings": {
            "dynamic": False,
            "fields": {
                CONTENT_PATH: searchable_text_field(exact=True),
                TITLE_PATH: searchable_text_field(),
                PROJECT_PATH: {"type": "token"},
                TYPE_PATH: {"type": "token"},
                STATUS_PATH: {"type": "token"},
                TAGS_PATH: {"type": "token"},
                IMPORTANCE_PATH: {"type": "number"},
            },
        },
    }


def context_text_query(query: str, *, phrase: bool = False) -> MongoDocument:
    """Build the compound operator for one lexical context query."""
    return compound_text_query(
        query,
        content_path=CONTENT_PATH,
        secondary=((TITLE_PATH, TITLE_BOOST),),
        phrase=phrase,
    )


def context_vector_filter(filters: ContextQueryFilters) -> MongoDocument:
    """Build the `$vectorSearch` pre-filter for one context scope."""
    clauses: list[MongoDocument] = [{PROJECT_PATH: {"$eq": filters.project_id}}]
    if filters.types:
        clauses.append({TYPE_PATH: {"$in": list(filters.types)}})
    if filters.status is not None:
        clauses.append({STATUS_PATH: {"$eq": filters.status}})
    for tag in filters.tags:
        clauses.append({TAGS_PATH: {"$eq": tag}})
    if filters.minimum_importance > 0.0:
        clauses.append({IMPORTANCE_PATH: {"$gte": filters.minimum_importance}})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def context_filter_clauses(filters: ContextQueryFilters) -> list[MongoDocument]:
    """Build non-scoring `$search` filter clauses for one context scope."""
    clauses: list[MongoDocument] = [{"equals": {"path": PROJECT_PATH, "value": filters.project_id}}]
    if filters.types:
        clauses.append(
            {
                "compound": {
                    "should": [
                        {"equals": {"path": TYPE_PATH, "value": value}} for value in filters.types
                    ],
                    "minimumShouldMatch": 1,
                }
            }
        )
    if filters.status is not None:
        clauses.append({"equals": {"path": STATUS_PATH, "value": filters.status}})
    clauses.extend({"equals": {"path": TAGS_PATH, "value": tag}} for tag in filters.tags)
    if filters.minimum_importance > 0.0:
        clauses.append({"range": {"path": IMPORTANCE_PATH, "gte": filters.minimum_importance}})
    return clauses


async def describe_context_vector_index(
    database: AsyncDatabase[MongoDocument],
    name: str = CONTEXT_VECTOR_INDEX,
) -> VectorIndexStatus:
    """Read the context vector index's state through `mongod`."""
    state = await describe_search_index(database, CONTEXT_COLLECTION, name)
    vector = vector_field(state.definition)
    return VectorIndexStatus(
        name=state.name,
        exists=state.exists,
        queryable=state.queryable,
        status=state.status,
        dimensions=positive_int(vector.get("numDimensions")) if vector else None,
        similarity=text_value(vector.get("similarity")) if vector else None,
    )


async def describe_context_text_index(
    database: AsyncDatabase[MongoDocument],
    name: str = CONTEXT_TEXT_INDEX,
) -> TextIndexStatus:
    """Read the context Search index's state through `mongod`."""
    state = await describe_search_index(database, CONTEXT_COLLECTION, name)
    return TextIndexStatus(
        name=state.name,
        exists=state.exists,
        queryable=state.queryable,
        status=state.status,
        analyzer=content_analyzer(state.definition),
    )


async def ensure_context_vector_index(
    database: AsyncDatabase[MongoDocument],
    *,
    dimensions: int,
) -> ContextIndexAction:
    """Create or reconcile the project-context vector index."""
    definition = context_vector_definition(dimensions)
    existing = await describe_search_index(database, CONTEXT_COLLECTION, CONTEXT_VECTOR_INDEX)
    return await reconcile_search_index(
        database,
        CONTEXT_COLLECTION,
        name=CONTEXT_VECTOR_INDEX,
        index_type=VECTOR_INDEX_TYPE,
        definition=definition,
        matches=vector_definition_matches(existing.definition, definition),
        existing=existing,
    )


async def ensure_context_text_index(
    database: AsyncDatabase[MongoDocument],
) -> ContextIndexAction:
    """Create or reconcile the project-context Search index."""
    definition = context_text_definition()
    existing = await describe_search_index(database, CONTEXT_COLLECTION, CONTEXT_TEXT_INDEX)
    return await reconcile_search_index(
        database,
        CONTEXT_COLLECTION,
        name=CONTEXT_TEXT_INDEX,
        index_type="search",
        definition=definition,
        matches=text_definition_matches(existing.definition, definition),
        existing=existing,
    )


async def check_context_vector_health(
    database: AsyncDatabase[MongoDocument],
    expected_dimensions: int | None = None,
) -> VectorSearchHealth:
    """Evaluate context vector readiness without raising or leaking internals."""
    try:
        index = await describe_context_vector_index(database)
    except SearchUnavailableError as error:
        return VectorSearchHealth(
            available=False,
            expected_dimensions=expected_dimensions,
            detail=type(error).__name__,
        )
    return VectorSearchHealth(
        available=True,
        index=index,
        expected_dimensions=expected_dimensions,
    )


async def check_context_text_health(
    database: AsyncDatabase[MongoDocument],
) -> TextSearchHealth:
    """Evaluate context lexical readiness without raising or leaking internals."""
    try:
        index = await describe_context_text_index(database)
    except SearchUnavailableError as error:
        return TextSearchHealth(available=False, detail=type(error).__name__)
    return TextSearchHealth(available=True, index=index)


__all__ = [
    "CONTEXT_COLLECTION",
    "CONTEXT_FILTER_PATHS",
    "CONTEXT_TEXT_INDEX",
    "CONTEXT_VECTOR_INDEX",
    "STANDARD_ANALYZER",
    "TITLE_BOOST",
    "ContextIndexAction",
    "ContextQueryFilters",
    "check_context_text_health",
    "check_context_vector_health",
    "context_filter_clauses",
    "context_text_definition",
    "context_text_query",
    "context_vector_definition",
    "context_vector_filter",
    "describe_context_text_index",
    "describe_context_vector_index",
    "ensure_context_text_index",
    "ensure_context_vector_index",
]
