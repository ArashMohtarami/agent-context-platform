"""Search indexes for memories: definitions, queries, and health.

Memory is retrieved the same two ways skill chunks and project context are, but
its scope is not a single value: a project-scoped request may see that project's
memories *and* every global one, while a request naming no project sees only
global memories. That union is expressed as a pre-filter here, so no memory from
another project is ever a candidate in the first place.
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
from app.models.memory import MemoryScope
from app.models.skill import LifecycleStatus

MEMORY_COLLECTION: Final = "memories"
MEMORY_VECTOR_INDEX: Final = "memory_vector_index"
MEMORY_TEXT_INDEX: Final = "memory_text_index"
CONTENT_PATH: Final = "content"
SUMMARY_PATH: Final = "summary"
SCOPE_PATH: Final = "scope"
PROJECT_PATH: Final = "project_id"
CLIENT_PATH: Final = "client_id"
TYPE_PATH: Final = "type"
STATUS_PATH: Final = "status"
TAGS_PATH: Final = "tags"
IMPORTANCE_PATH: Final = "importance"
# Every field a memory query may restrict. `scope` and `project_id` are both
# indexed because the scope rule needs them together.
MEMORY_FILTER_PATHS: Final[tuple[str, ...]] = (
    SCOPE_PATH,
    PROJECT_PATH,
    CLIENT_PATH,
    TYPE_PATH,
    STATUS_PATH,
    TAGS_PATH,
    IMPORTANCE_PATH,
)
# A summary is the memory stated in one line, so it matches a question more
# directly than the body it condenses.
SUMMARY_BOOST: Final = 1.5

MemoryIndexAction = SearchIndexAction


@dataclass(frozen=True, slots=True)
class MemoryQueryFilters:
    """The scopes and metadata one memory query is restricted to."""

    scopes: tuple[MemoryScope, ...] = ("global",)
    project_id: str | None = None
    client_id: str | None = None
    types: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    status: LifecycleStatus | None = "active"
    minimum_importance: float = 0.0

    def __post_init__(self) -> None:
        """Reject a scope that could not be applied as written."""
        if not self.scopes:
            raise ValueError("a memory query must search at least one scope")
        if len(set(self.scopes)) != len(self.scopes):
            raise ValueError("memory scopes must be unique")
        project_scopes = {"project", "project_agent"}
        agent_scopes = {"agent", "project_agent"}
        if any(scope in project_scopes for scope in self.scopes) and not self.project_id:
            raise ValueError("searching project-scoped memory requires a project")
        if (
            not any(scope in project_scopes for scope in self.scopes)
            and self.project_id is not None
        ):
            raise ValueError("a project was named but project memory was not searched")
        if any(scope in agent_scopes for scope in self.scopes) and not self.client_id:
            raise ValueError("searching agent memory requires an authenticated client")
        if not any(scope in agent_scopes for scope in self.scopes) and self.client_id is not None:
            raise ValueError("a client was named but agent memory was not searched")
        if not 0.0 <= self.minimum_importance <= 1.0:
            raise ValueError("minimum_importance must be between 0 and 1")


def memory_vector_definition(dimensions: int) -> MongoDocument:
    """Build the memory vector index definition."""
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
            *({"type": "filter", "path": path} for path in MEMORY_FILTER_PATHS),
        ]
    }


def memory_text_definition() -> MongoDocument:
    """Build the memory Search index definition."""
    return {
        "analyzers": [identifier_analyzer_definition()],
        "mappings": {
            "dynamic": False,
            "fields": {
                CONTENT_PATH: searchable_text_field(exact=True),
                SUMMARY_PATH: searchable_text_field(),
                SCOPE_PATH: {"type": "token"},
                PROJECT_PATH: {"type": "token"},
                CLIENT_PATH: {"type": "token"},
                TYPE_PATH: {"type": "token"},
                STATUS_PATH: {"type": "token"},
                TAGS_PATH: {"type": "token"},
                IMPORTANCE_PATH: {"type": "number"},
            },
        },
    }


def memory_text_query(query: str, *, phrase: bool = False) -> MongoDocument:
    """Build the compound operator for one lexical memory query."""
    return compound_text_query(
        query,
        content_path=CONTENT_PATH,
        secondary=((SUMMARY_PATH, SUMMARY_BOOST),),
        phrase=phrase,
    )


def memory_vector_filter(filters: MemoryQueryFilters) -> MongoDocument:
    """Build the `$vectorSearch` pre-filter for one memory scope."""
    clauses: list[MongoDocument] = [_vector_scope_clause(filters)]
    if filters.types:
        clauses.append({TYPE_PATH: {"$in": list(filters.types)}})
    if filters.status is not None:
        clauses.append({STATUS_PATH: {"$eq": filters.status}})
    clauses.extend({TAGS_PATH: {"$eq": tag}} for tag in filters.tags)
    if filters.minimum_importance > 0.0:
        clauses.append({IMPORTANCE_PATH: {"$gte": filters.minimum_importance}})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def memory_filter_clauses(filters: MemoryQueryFilters) -> list[MongoDocument]:
    """Build non-scoring `$search` filter clauses for one memory scope."""
    clauses: list[MongoDocument] = [_lexical_scope_clause(filters)]
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


def _vector_scope_clause(filters: MemoryQueryFilters) -> MongoDocument:
    """Restrict a vector query to global memory, one project's, or both."""
    alternatives: list[MongoDocument] = []
    if "global" in filters.scopes:
        alternatives.append({SCOPE_PATH: {"$eq": "global"}})
    if "project" in filters.scopes:
        alternatives.append(
            {
                "$and": [
                    {SCOPE_PATH: {"$eq": "project"}},
                    {PROJECT_PATH: {"$eq": filters.project_id}},
                ]
            }
        )
    if "agent" in filters.scopes:
        alternatives.append(
            {
                "$and": [
                    {SCOPE_PATH: {"$eq": "agent"}},
                    {CLIENT_PATH: {"$eq": filters.client_id}},
                ]
            }
        )
    if "project_agent" in filters.scopes:
        alternatives.append(
            {
                "$and": [
                    {SCOPE_PATH: {"$eq": "project_agent"}},
                    {PROJECT_PATH: {"$eq": filters.project_id}},
                    {CLIENT_PATH: {"$eq": filters.client_id}},
                ]
            }
        )
    return alternatives[0] if len(alternatives) == 1 else {"$or": alternatives}


def _lexical_scope_clause(filters: MemoryQueryFilters) -> MongoDocument:
    """Restrict a lexical query to global memory, one project's, or both."""
    alternatives: list[MongoDocument] = []
    if "global" in filters.scopes:
        alternatives.append({"equals": {"path": SCOPE_PATH, "value": "global"}})
    if "project" in filters.scopes:
        alternatives.append(
            {
                "compound": {
                    "must": [
                        {"equals": {"path": SCOPE_PATH, "value": "project"}},
                        {"equals": {"path": PROJECT_PATH, "value": filters.project_id}},
                    ]
                }
            }
        )
    if "agent" in filters.scopes:
        alternatives.append(
            {
                "compound": {
                    "must": [
                        {"equals": {"path": SCOPE_PATH, "value": "agent"}},
                        {"equals": {"path": CLIENT_PATH, "value": filters.client_id}},
                    ]
                }
            }
        )
    if "project_agent" in filters.scopes:
        alternatives.append(
            {
                "compound": {
                    "must": [
                        {"equals": {"path": SCOPE_PATH, "value": "project_agent"}},
                        {"equals": {"path": PROJECT_PATH, "value": filters.project_id}},
                        {"equals": {"path": CLIENT_PATH, "value": filters.client_id}},
                    ]
                }
            }
        )
    if len(alternatives) == 1:
        return alternatives[0]
    return {"compound": {"should": alternatives, "minimumShouldMatch": 1}}


async def describe_memory_vector_index(
    database: AsyncDatabase[MongoDocument],
    name: str = MEMORY_VECTOR_INDEX,
) -> VectorIndexStatus:
    """Read the memory vector index's state through `mongod`."""
    state = await describe_search_index(database, MEMORY_COLLECTION, name)
    vector = vector_field(state.definition)
    return VectorIndexStatus(
        name=state.name,
        exists=state.exists,
        queryable=state.queryable,
        status=state.status,
        dimensions=positive_int(vector.get("numDimensions")) if vector else None,
        similarity=text_value(vector.get("similarity")) if vector else None,
    )


async def describe_memory_text_index(
    database: AsyncDatabase[MongoDocument],
    name: str = MEMORY_TEXT_INDEX,
) -> TextIndexStatus:
    """Read the memory Search index's state through `mongod`."""
    state = await describe_search_index(database, MEMORY_COLLECTION, name)
    return TextIndexStatus(
        name=state.name,
        exists=state.exists,
        queryable=state.queryable,
        status=state.status,
        analyzer=content_analyzer(state.definition),
    )


async def ensure_memory_vector_index(
    database: AsyncDatabase[MongoDocument],
    *,
    dimensions: int,
) -> MemoryIndexAction:
    """Create or reconcile the memory vector index."""
    definition = memory_vector_definition(dimensions)
    existing = await describe_search_index(database, MEMORY_COLLECTION, MEMORY_VECTOR_INDEX)
    return await reconcile_search_index(
        database,
        MEMORY_COLLECTION,
        name=MEMORY_VECTOR_INDEX,
        index_type=VECTOR_INDEX_TYPE,
        definition=definition,
        matches=vector_definition_matches(existing.definition, definition),
        existing=existing,
    )


async def ensure_memory_text_index(
    database: AsyncDatabase[MongoDocument],
) -> MemoryIndexAction:
    """Create or reconcile the memory Search index."""
    definition = memory_text_definition()
    existing = await describe_search_index(database, MEMORY_COLLECTION, MEMORY_TEXT_INDEX)
    return await reconcile_search_index(
        database,
        MEMORY_COLLECTION,
        name=MEMORY_TEXT_INDEX,
        index_type="search",
        definition=definition,
        matches=text_definition_matches(existing.definition, definition),
        existing=existing,
    )


async def check_memory_vector_health(
    database: AsyncDatabase[MongoDocument],
    expected_dimensions: int | None = None,
) -> VectorSearchHealth:
    """Evaluate memory vector readiness without raising or leaking internals."""
    try:
        index = await describe_memory_vector_index(database)
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


async def check_memory_text_health(
    database: AsyncDatabase[MongoDocument],
) -> TextSearchHealth:
    """Evaluate memory lexical readiness without raising or leaking internals."""
    try:
        index = await describe_memory_text_index(database)
    except SearchUnavailableError as error:
        return TextSearchHealth(available=False, detail=type(error).__name__)
    return TextSearchHealth(available=True, index=index)
