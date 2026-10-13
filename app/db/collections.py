"""Idempotent creation of validated application collections."""

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import CollectionInvalid

from app.db.client_types import MongoDocument
from app.db.health import MONGOT_PROBE_COLLECTION
from app.db.schema import COLLECTION_VALIDATORS


async def ensure_collections(
    database: AsyncDatabase[MongoDocument],
    *,
    collection_names: tuple[str, ...] | None = None,
) -> None:
    """Create missing validated collections idempotently.

    The coordinator probe's collection is created alongside them, empty and
    without a validator. It holds no documents and never will: it exists so
    that listing its search indexes has to reach `mongot`, which listing the
    indexes of a collection MongoDB does not have does not.
    """
    names = collection_names if collection_names is not None else tuple(COLLECTION_VALIDATORS)
    unknown = set(names) - set(COLLECTION_VALIDATORS)
    if unknown:
        raise ValueError(f"unknown collection validators: {sorted(unknown)!r}")

    existing = set(await database.list_collection_names())
    if MONGOT_PROBE_COLLECTION not in existing:
        try:
            await database.create_collection(MONGOT_PROBE_COLLECTION)
        except CollectionInvalid:
            pass
    for collection_name in names:
        validator = COLLECTION_VALIDATORS[collection_name]
        if collection_name in existing:
            continue
        try:
            await database.create_collection(
                collection_name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )
        except CollectionInvalid:
            # Another application instance may have won the idempotent create race.
            continue
