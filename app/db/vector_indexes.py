"""MongoDB Vector Search index definition, reconciliation, and health."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.errors import SearchUnavailableError
from app.db.search_indexes import (
    SKILL_CHUNK_COLLECTION,
    SearchIndexAction,
    describe_search_index,
    positive_int,
    reconcile_search_index,
    text_value,
)
from app.embeddings.types import MAX_EMBEDDING_DIMENSIONS

SKILL_CHUNK_VECTOR_INDEX: Final = "skill_chunk_vector_index"
VECTOR_INDEX_TYPE: Final = "vectorSearch"
VECTOR_PATH: Final = "embedding"
# Cosine matches the normalized unit vectors the embedding provider produces.
VECTOR_SIMILARITY: Final = "cosine"
# Only chunk-level fields can pre-filter a vector query. Skill metadata filters
# are resolved to these keys by the retrieval service.
VECTOR_FILTER_PATHS: Final[tuple[str, ...]] = ("org_id", "_id", "skill_id", "skill_version")

VectorIndexAction = SearchIndexAction


def skill_chunk_vector_definition(dimensions: int) -> MongoDocument:
    """Build the skill chunk vector index definition for one dimension count.

    Dimensions are supplied by the loaded embedding model, so the index and the
    stored vectors can never disagree about their shape.
    """
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
            *({"type": "filter", "path": path} for path in VECTOR_FILTER_PATHS),
        ]
    }


@dataclass(frozen=True, slots=True)
class VectorIndexStatus:
    """Credential-free state of the vector index, safe to return to clients."""

    name: str
    exists: bool
    queryable: bool
    status: str | None = None
    dimensions: int | None = None
    similarity: str | None = None

    @property
    def ready(self) -> bool:
        """Report whether the index can currently serve a vector query."""
        return self.exists and self.queryable


@dataclass(frozen=True, slots=True)
class VectorSearchHealth:
    """Vector search readiness including agreement with the active model."""

    available: bool
    index: VectorIndexStatus | None = None
    expected_dimensions: int | None = None
    detail: str | None = None

    @property
    def dimensions_match(self) -> bool:
        """Report whether the built index matches the active embedding model."""
        if self.index is None or self.expected_dimensions is None:
            return False
        return self.index.dimensions == self.expected_dimensions

    @property
    def ready(self) -> bool:
        """Report whether vector search can serve the configured model."""
        return self.available and self.index is not None and self.index.ready


async def describe_vector_index(
    database: AsyncDatabase[MongoDocument],
    name: str = SKILL_CHUNK_VECTOR_INDEX,
) -> VectorIndexStatus:
    """Read the vector index's state through `mongod`."""
    state = await describe_search_index(database, SKILL_CHUNK_COLLECTION, name)
    vector = vector_field(state.definition)
    return VectorIndexStatus(
        name=state.name,
        exists=state.exists,
        queryable=state.queryable,
        status=state.status,
        dimensions=positive_int(vector.get("numDimensions")) if vector else None,
        similarity=text_value(vector.get("similarity")) if vector else None,
    )


async def ensure_vector_indexes(
    database: AsyncDatabase[MongoDocument],
    *,
    dimensions: int,
) -> VectorIndexAction:
    """Create or reconcile the skill chunk vector index.

    Safe to repeat on every startup: an identical index is left untouched, and a
    definition that no longer matches the active model is updated in place.
    """
    definition = skill_chunk_vector_definition(dimensions)
    existing = await describe_search_index(
        database,
        SKILL_CHUNK_COLLECTION,
        SKILL_CHUNK_VECTOR_INDEX,
    )
    return await reconcile_search_index(
        database,
        SKILL_CHUNK_COLLECTION,
        name=SKILL_CHUNK_VECTOR_INDEX,
        index_type=VECTOR_INDEX_TYPE,
        definition=definition,
        matches=vector_definition_matches(existing.definition, definition),
        existing=existing,
    )


async def check_vector_search_health(
    database: AsyncDatabase[MongoDocument],
    expected_dimensions: int | None = None,
) -> VectorSearchHealth:
    """Evaluate vector search readiness without raising or leaking internals."""
    try:
        index = await describe_vector_index(database)
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


def vector_field(definition: Mapping[str, object] | None) -> Mapping[str, object] | None:
    """Return the vector field of an index definition, if it has one."""
    if definition is None:
        return None
    fields = definition.get("fields")
    if not isinstance(fields, list):
        return None
    for field in fields:
        if isinstance(field, Mapping) and field.get("type") == "vector":
            return field
    return None


def vector_definition_matches(
    current: Mapping[str, object] | None,
    expected: MongoDocument,
) -> bool:
    """Compare only the parts of a definition this application controls."""
    if current is None:
        return False
    return _comparable(current) == _comparable(expected)


def _comparable(definition: Mapping[str, object]) -> tuple[object, ...]:
    vector = vector_field(definition) or {}
    fields = definition.get("fields")
    filters = tuple(
        sorted(
            str(field.get("path"))
            for field in (fields if isinstance(fields, list) else [])
            if isinstance(field, Mapping) and field.get("type") == "filter"
        )
    )
    return (
        text_value(vector.get("path")),
        positive_int(vector.get("numDimensions")),
        text_value(vector.get("similarity")),
        filters,
    )
