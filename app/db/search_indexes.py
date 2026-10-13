"""Shared MongoDB Search index primitives.

Every index operation is issued to `mongod`, which coordinates with the
self-managed `mongot`. The application never connects to `mongot` directly.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.operations import SearchIndexModel

from app.db.client_types import MongoDocument
from app.db.errors import SearchUnavailableError

SKILL_CHUNK_COLLECTION: Final = "skill_chunks"
_INDEX_ALREADY_EXISTS: Final = 68

SearchIndexAction = Literal["created", "updated", "unchanged"]


@dataclass(frozen=True, slots=True)
class SearchIndexState:
    """Credential-free state of one search index, safe to return to clients."""

    name: str
    exists: bool
    queryable: bool
    status: str | None = None
    definition: Mapping[str, object] | None = None

    @property
    def ready(self) -> bool:
        """Report whether the index can currently serve a query."""
        return self.exists and self.queryable


async def describe_search_index(
    database: AsyncDatabase[MongoDocument],
    collection_name: str,
    name: str,
) -> SearchIndexState:
    """Read one search index's state through `mongod`."""
    raw = await _find_index(database[collection_name], name)
    if raw is None:
        return SearchIndexState(name=name, exists=False, queryable=False)
    definition = raw.get("latestDefinition")
    status = raw.get("status")
    return SearchIndexState(
        name=name,
        exists=True,
        queryable=bool(raw.get("queryable", False)),
        status=status if isinstance(status, str) else None,
        definition=definition if isinstance(definition, Mapping) else None,
    )


async def reconcile_search_index(
    database: AsyncDatabase[MongoDocument],
    collection_name: str,
    *,
    name: str,
    index_type: str,
    definition: MongoDocument,
    matches: bool,
    existing: SearchIndexState,
) -> SearchIndexAction:
    """Create an absent index, or update one that no longer matches.

    `matches` is decided by the caller because each index type compares only the
    parts of its definition that this application controls.
    """
    collection = database[collection_name]
    if not existing.exists:
        try:
            await collection.create_search_index(
                SearchIndexModel(definition=definition, name=name, type=index_type)
            )
        except OperationFailure as error:
            if error.code == _INDEX_ALREADY_EXISTS:
                # Another instance won the idempotent create race.
                return "unchanged"
            raise SearchUnavailableError(f"the {name} search index could not be created") from error
        except PyMongoError as error:
            raise SearchUnavailableError(f"the {name} search index could not be created") from error
        return "created"

    if matches:
        return "unchanged"
    try:
        await collection.update_search_index(name, definition)
    except PyMongoError as error:
        raise SearchUnavailableError(f"the {name} search index could not be updated") from error
    return "updated"


async def _find_index(
    collection: AsyncCollection[MongoDocument],
    name: str,
) -> Mapping[str, object] | None:
    try:
        cursor = await collection.list_search_indexes(name)
        documents = await cursor.to_list(length=1)
    except PyMongoError as error:
        raise SearchUnavailableError("MongoDB Search indexes could not be listed") from error
    for document in documents:
        if document.get("name") == name:
            return document
    return None


def positive_int(value: object) -> int | None:
    """Return a usable positive integer from an index definition."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def text_value(value: object) -> str | None:
    """Return a usable string from an index definition."""
    return value if isinstance(value, str) else None
