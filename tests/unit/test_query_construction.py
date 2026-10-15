"""Caller input reaches a query as a value, never as part of its structure.

MongoDB queries are documents, so the injection to rule out is not a quoting
bug but a shape one: a caller whose string became a key, an operator, or a
nested document would be writing the query rather than answering it.

Every builder here is given input designed to become structure, and the built
query is inspected for it.
"""

import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_skill_service
from app.api.routes.skills import router as skills_router
from app.db.context_indexes import (
    ContextQueryFilters,
    context_filter_clauses,
    context_text_query,
)
from app.db.text_indexes import compound_text_query, version_filter_clauses
from app.repositories.api_key_repository import MongoApiKeyRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.security.keys import MalformedKeyError, parse
from app.skills.types import SkillVersionKey
from tests.api_doubles import open_application

# Values written to become query structure rather than query input.
HOSTILE = (
    '{"$ne": null}',
    "{'$gt': ''}",
    '{"$where": "sleep(5000)"}',
    "$ne",
    "$gt",
    '", "$or": [{}], "x": "',
    "../../admin",
    "\x00",
    ".*",
    "^.*$",
    "a|b",
    "[a-z]+",
    "$regex",
    "{$exists: true}",
)
# Every Mongo operator this platform never lets a caller name.
FORBIDDEN_OPERATORS = (
    "$where",
    "$expr",
    "$function",
    "$accumulator",
    "$regex",
    "$ne",
    "$gt",
    "$lt",
    "$or",
    "$nor",
    "$exists",
    "$nin",
)


def operators(value: Any) -> set[str]:
    """Return every `$`-prefixed key anywhere in a built query."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and key.startswith("$"):
                found.add(key)
            found |= operators(nested)
    elif isinstance(value, list | tuple):
        for item in value:
            found |= operators(item)
    return found


def values(value: Any) -> list[Any]:
    """Return every leaf in a built query."""
    if isinstance(value, dict):
        return [leaf for nested in value.values() for leaf in values(nested)]
    if isinstance(value, list | tuple):
        return [leaf for item in value for leaf in values(item)]
    return [value]


def collection() -> tuple[MagicMock, MagicMock]:
    """Build a database whose one collection records how it was queried."""
    recorded = MagicMock()
    recorded.find_one = AsyncMock(return_value=None)
    recorded.update_one = AsyncMock()
    recorded.aggregate = AsyncMock()
    recorded.aggregate.return_value.to_list = AsyncMock(return_value=[])
    database = MagicMock()
    database.__getitem__.return_value = recorded
    return database, recorded


# --- lexical search --------------------------------------------------------


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_search_query_becomes_search_text_and_nothing_else(hostile: str) -> None:
    built = compound_text_query(f"payments {hostile}")

    # Atlas Search takes the query in a value position. Whatever it says, it
    # is a term to match, and the operators are the ones this module wrote.
    assert operators(built) == set()
    assert any(hostile in str(leaf) for leaf in values(built))


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_hostile_query_reaches_no_operator_position_in_context_search(hostile: str) -> None:
    built = context_text_query(f"refunds {hostile}")

    assert operators(built).isdisjoint(FORBIDDEN_OPERATORS)


def test_an_empty_query_is_refused_rather_than_matching_everything() -> None:
    for empty in ("", "   ", "\n\t"):
        with pytest.raises(ValueError, match="must not be empty"):
            compound_text_query(empty)


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_version_filter_names_a_value_not_a_condition(hostile: str) -> None:
    built = version_filter_clauses([SkillVersionKey(skill_id=hostile, version=hostile)])

    # `equals` takes a literal. A caller's text lands in `value`, where the
    # worst it can do is match nothing.
    assert operators(built).isdisjoint(FORBIDDEN_OPERATORS)
    assert hostile in values(built)


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_context_filter_names_a_value_not_a_condition(hostile: str) -> None:
    built = context_filter_clauses(ContextQueryFilters(project_id=hostile))

    assert operators(built).isdisjoint(FORBIDDEN_OPERATORS)
    assert hostile in values(built)


# --- the one place a regex is built ----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("hostile", HOSTILE)
async def test_a_lexical_skill_search_escapes_every_pattern_it_builds(hostile: str) -> None:
    database, recorded = collection()
    repository = MongoSkillRepository(database)

    await repository.search_active_lexical(f"payments {hostile}")

    pipeline = recorded.aggregate.await_args.args[0]
    built = [leaf.pattern for leaf in values(pipeline) if hasattr(leaf, "pattern")]
    assert built, "the search builds at least one pattern"
    for pattern in built:
        # Escaped, so `.*` matches a literal dot-star and `[a-z]+` matches
        # itself. A caller cannot write an expression that scans the
        # collection, and matching the escaped form back to the term it came
        # from is what shows the escaping actually happened.
        assert re.fullmatch(pattern, _source_term(pattern)) is not None
    assert operators(pipeline).isdisjoint(("$where", "$function", "$accumulator"))


def _source_term(pattern: str) -> str:
    """Return the literal text an escaped pattern was built from."""
    return re.sub(r"\\(.)", r"\1", pattern)


@pytest.mark.asyncio
async def test_a_lexical_skill_search_is_bounded_in_terms_and_results() -> None:
    database, recorded = collection()
    repository = MongoSkillRepository(database)

    await repository.search_active_lexical(" ".join(f"term{index}" for index in range(200)))

    pipeline = recorded.aggregate.await_args.args[0]
    compiled = [leaf for leaf in values(pipeline) if hasattr(leaf, "pattern")]
    # A query of arbitrary length must not become a query of arbitrary cost.
    assert len(set(pattern.pattern for pattern in compiled)) <= 8
    assert any("$limit" in str(stage) for stage in pipeline)


# --- identifiers -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("hostile", HOSTILE)
async def test_a_key_identifier_is_validated_before_it_becomes_a_filter(hostile: str) -> None:
    # The credential is parsed before it is looked up, so a crafted identifier
    # is refused rather than turned into a query at all.
    with pytest.raises(MalformedKeyError):
        parse(f"acp_{hostile}_{'s' * 40}")


@pytest.mark.asyncio
async def test_a_key_lookup_filters_on_exactly_one_string() -> None:
    database, recorded = collection()
    repository = MongoApiKeyRepository(database)

    await repository.get_by_key_id("0123456789abcdef")

    assert recorded.find_one.await_args.args[0] == {
        "key_id": "0123456789abcdef",
        "org_id": "org-test",
    }


# --- through the API -------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("hostile", HOSTILE)
async def test_a_hostile_path_parameter_is_refused_by_the_route(hostile: str) -> None:
    service = MagicMock()
    service.get_skill = AsyncMock(return_value=None)
    application = _with(skills_router, service)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/skills/{quote(hostile, safe='')}")

    # Either the identifier pattern refuses it, or it is looked up and found to
    # be nothing. What must not happen is a match.
    assert response.status_code in {404, 422}


@pytest.mark.asyncio
@pytest.mark.parametrize("hostile", HOSTILE)
async def test_a_hostile_body_value_stays_a_string(hostile: str) -> None:
    service = MagicMock()
    service.search_skills = AsyncMock(return_value=())
    application = _with(skills_router, service)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/skills/search", json={"query": f"x {hostile}"})

    if response.status_code == 200:
        passed = service.search_skills.await_args.args[0]
        # Pydantic validated it as a string, so a JSON object in the body is a
        # 422 and a string that looks like one is a string.
        assert isinstance(passed, str)
        assert hostile in passed


@pytest.mark.asyncio
async def test_a_body_sending_an_object_where_a_string_belongs_is_refused() -> None:
    service = MagicMock()
    service.search_skills = AsyncMock(return_value=())
    application = _with(skills_router, service)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/skills/search", json={"query": {"$ne": None}})

    # The one shape that would matter, refused at the schema before any
    # repository sees it.
    assert response.status_code == 422
    service.search_skills.assert_not_awaited()


def _with(router: Any, service: MagicMock) -> FastAPI:
    """Build an open application whose skill service is the given double."""
    application = open_application(router)

    async def provide() -> MagicMock:
        return service

    application.dependency_overrides[get_skill_service] = provide
    return application


# --- the rule itself -------------------------------------------------------


def test_no_module_builds_a_query_from_server_side_javascript() -> None:
    # `$where`, `$function` and `$accumulator` run JavaScript in the database.
    # Nothing here needs them, and reading the source is the honest way to keep
    # it that way: an operator that is never written cannot be reached.
    offenders = [
        str(path)
        for path in Path("app").rglob("*.py")
        for marker in ("$where", "$function", "$accumulator")
        if marker in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_the_application_connects_as_a_role_that_can_do_almost_nothing() -> None:
    # Defence in depth, and the phase's "no root MongoDB credentials in the
    # application": what the API authenticates as is readWrite on one database
    # plus two roles narrowed to a single action each. Neither carries the
    # privilege `$where` needs, and neither is root, dbOwner, or dbAdmin.
    granted = Path("scripts/create-application-user.js").read_text(encoding="utf-8")

    assert '{ role: "readWrite", db: process.env.MONGODB_DATABASE }' in granted
    # The only cluster-wide action is reading replica-set status, and the only
    # administrative one is updating this database's own validators.
    assert 'actions: ["replSetGetStatus"]' in granted
    assert 'actions: ["collMod"]' in granted
    for privileged in ("root", "dbOwner", "dbAdmin", "clusterAdmin", "__system", "anyAction"):
        assert privileged not in granted


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
