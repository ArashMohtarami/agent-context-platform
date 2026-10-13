"""Skill metadata queries that scope chunk-level retrieval."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.models.common import deterministic_object_id
from app.repositories.skill_repository import MAX_VERSION_KEYS, MongoSkillRepository
from app.skills.types import SkillVersionKey


def build_repository(
    documents: list[MongoDocument] | None = None,
) -> tuple[MongoSkillRepository, MagicMock]:
    """Build a skill repository whose find calls are observable."""
    collection = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=list(documents or []))
    collection.find = MagicMock(return_value=cursor)
    database = MagicMock()
    database.__getitem__ = MagicMock(return_value=collection)
    repository = MongoSkillRepository(cast(AsyncDatabase[MongoDocument], database))
    return repository, collection


def find_call(collection: MagicMock) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return the filter and projection of the submitted query."""
    call = collection.find.call_args
    projection = call.args[1] if len(call.args) > 1 else None
    return cast(dict[str, Any], call.args[0]), cast("dict[str, Any] | None", projection)


@pytest.mark.asyncio
async def test_version_keys_default_to_active_versions_only() -> None:
    repository, collection = build_repository(
        [{"skill_id": "fastapi-production", "version": "1.0.0"}]
    )

    keys = await repository.list_version_keys()

    filters, projection = find_call(collection)
    assert filters == {"status": "active"}
    assert projection == {"skill_id": 1, "version": 1, "_id": 0}
    assert keys == (SkillVersionKey("fastapi-production", "1.0.0"),)


@pytest.mark.asyncio
async def test_every_metadata_filter_becomes_one_bounded_query() -> None:
    repository, collection = build_repository([])

    await repository.list_version_keys(
        status=None,
        skill_id="fastapi-production",
        version="1.0.0",
        category="backend",
        tags=["python", "fastapi"],
        trust_level="trusted",
        limit=25,
    )

    filters, _ = find_call(collection)
    assert filters == {
        "skill_id": "fastapi-production",
        "version": "1.0.0",
        "category": "backend",
        "tags": {"$all": ["python", "fastapi"]},
        "trust_level": "trusted",
    }
    collection.find.return_value.limit.assert_called_once_with(25)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, MAX_VERSION_KEYS + 1, True])
async def test_unbounded_metadata_queries_are_rejected(limit: int) -> None:
    repository, collection = build_repository([])

    with pytest.raises(ValueError, match="limit"):
        await repository.list_version_keys(limit=limit)

    collection.find.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_stored_keys_are_refused() -> None:
    repository, _ = build_repository([{"skill_id": "fastapi-production", "version": 1}])

    with pytest.raises(ValueError, match="invalid skill version key"):
        await repository.list_version_keys()


@pytest.mark.asyncio
async def test_selected_versions_are_loaded_by_deterministic_identity() -> None:
    repository, collection = build_repository([])
    keys = (
        SkillVersionKey("fastapi-production", "1.0.0"),
        SkillVersionKey("strict-python", "2.0.0"),
        SkillVersionKey("fastapi-production", "1.0.0"),
    )

    await repository.get_many(keys)

    filters, _ = find_call(collection)
    assert filters == {
        "_id": {
            "$in": [
                deterministic_object_id("skill", "fastapi-production", "1.0.0"),
                deterministic_object_id("skill", "strict-python", "2.0.0"),
            ]
        }
    }


@pytest.mark.asyncio
async def test_loading_no_versions_skips_the_query() -> None:
    repository, collection = build_repository([])

    assert await repository.get_many(()) == ()
    collection.find.assert_not_called()


@pytest.mark.asyncio
async def test_loading_an_unbounded_version_set_is_refused() -> None:
    repository, _ = build_repository([])
    keys = [SkillVersionKey(f"skill-{index}", "1.0.0") for index in range(MAX_VERSION_KEYS + 1)]

    with pytest.raises(ValueError, match="at most"):
        await repository.get_many(keys)
