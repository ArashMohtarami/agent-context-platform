"""V2 Phase 4 Mongo Search/vector index definitions and pre-filter tests."""

from typing import Any, cast

from app.db.content_indexes import (
    CONTENT_COLLECTIONS,
    CONTENT_TEXT_INDEXES,
    CONTENT_VECTOR_INDEXES,
    ContentQueryFilters,
    content_filter_clauses,
    content_text_definition,
    content_vector_definition,
    content_vector_filter,
)

PROFILE = "personal-brand"
PROJECT = "payments-api"


def test_every_content_collection_has_a_distinct_text_and_vector_index() -> None:
    assert set(CONTENT_COLLECTIONS) == {"idea", "brief", "item"}
    assert len(set(CONTENT_TEXT_INDEXES.values())) == 3
    assert len(set(CONTENT_VECTOR_INDEXES.values())) == 3


def test_content_vectors_pre_filter_profile_and_supported_metadata() -> None:
    filters = ContentQueryFilters(
        profile_id=PROFILE,
        platform="linkedin",
        project_id=PROJECT,
        status="published",
        pillar_id="payments",
        tags=("payments", "outbox"),
    )
    item = content_vector_filter("item", filters)
    clauses = cast(list[dict[str, Any]], item["$and"])

    assert {"profile_id": {"$eq": PROFILE}} in clauses
    assert {"platform": {"$eq": "linkedin"}} in clauses
    assert {"project_ids": {"$eq": PROJECT}} in clauses
    assert {"pillar_id": {"$eq": "payments"}} in clauses
    assert {"status": {"$eq": "published"}} in clauses
    assert {"tags": {"$eq": "payments"}} in clauses
    assert {"tags": {"$eq": "outbox"}} in clauses

    # Briefs have no pillar, so a broad common filter cannot accidentally
    # query a nonexistent path.
    brief = content_vector_filter("brief", filters)
    assert all("pillar_id" not in clause for clause in brief["$and"])


def test_content_lexical_indexes_map_prose_and_every_filter_field() -> None:
    for kind in CONTENT_COLLECTIONS:
        definition = content_text_definition(kind)
        mappings = cast(dict[str, Any], definition["mappings"])
        fields = cast(dict[str, Any], mappings["fields"])

        assert mappings["dynamic"] is False
        assert "embedding" not in fields
        assert {"profile_id", "status", "tags"}.issubset(fields)
        assert fields["profile_id"] == {"type": "token"}


def test_content_lexical_filters_match_vector_filter_scope() -> None:
    filters = ContentQueryFilters(
        profile_id=PROFILE,
        platform="linkedin",
        project_id=PROJECT,
        status="archived",
        tags=("payments",),
    )
    clauses = content_filter_clauses("idea", filters)

    assert {"equals": {"path": "profile_id", "value": PROFILE}} in clauses
    assert {"equals": {"path": "platforms", "value": "linkedin"}} in clauses
    assert {"equals": {"path": "origin.project_id", "value": PROJECT}} in clauses
    assert {"equals": {"path": "status", "value": "archived"}} in clauses
    assert {"equals": {"path": "tags", "value": "payments"}} in clauses


def test_vector_definition_rejects_invalid_dimensions() -> None:
    for dimensions in (0, -1, 4_097, True):
        try:
            content_vector_definition("item", dimensions)
        except ValueError as error:
            assert "dimensions" in str(error)
        else:  # pragma: no cover - documents the expected safety failure
            raise AssertionError("invalid vector dimensions must fail")
