"""V3 Phase 0 standard and mongot index-definition checks."""

from typing import Any, cast

import pytest

from app.db.migrations.v013_research_schema import V3_RESEARCH_COLLECTIONS
from app.db.migrations.v019_skill_update_proposals import SKILL_UPDATE_PROPOSAL_COLLECTION
from app.db.research_indexes import (
    RESEARCH_SEARCH_COLLECTIONS,
    RESEARCH_STANDARD_INDEX_DEFINITIONS,
    RESEARCH_TEXT_INDEXES,
    RESEARCH_VECTOR_INDEXES,
    research_text_definition,
    research_vector_definition,
)
from app.db.schema import COLLECTION_VALIDATORS
from app.db.vector_indexes import VECTOR_PATH


def test_v3_every_persistent_collection_has_a_validator_and_standard_indexes() -> None:
    # Phase 11 deliberately created its review-only collection in a later,
    # additive migration rather than rewriting the original Phase 0 migration.
    persistent_collections = {*V3_RESEARCH_COLLECTIONS, SKILL_UPDATE_PROPOSAL_COLLECTION}
    assert persistent_collections == set(RESEARCH_STANDARD_INDEX_DEFINITIONS)
    assert persistent_collections.issubset(COLLECTION_VALIDATORS)
    for collection, definitions in RESEARCH_STANDARD_INDEX_DEFINITIONS.items():
        assert definitions, collection
        names = [definition.document["name"] for definition in definitions]
        assert len(names) == len(set(names))


def test_v3_searchable_records_have_distinct_vector_and_text_indexes() -> None:
    assert set(RESEARCH_SEARCH_COLLECTIONS) == {"chunk", "note", "claim", "brief", "candidate"}
    assert len(set(RESEARCH_VECTOR_INDEXES.values())) == len(RESEARCH_SEARCH_COLLECTIONS)
    assert len(set(RESEARCH_TEXT_INDEXES.values())) == len(RESEARCH_SEARCH_COLLECTIONS)


def test_v3_search_indexes_include_authorization_and_metadata_prefilters() -> None:
    for kind in RESEARCH_SEARCH_COLLECTIONS:
        vector = research_vector_definition(kind, 384)
        fields = cast(list[dict[str, Any]], vector["fields"])
        assert fields[0] == {
            "type": "vector",
            "path": VECTOR_PATH,
            "numDimensions": 384,
            "similarity": "cosine",
        }
        filter_paths = {field["path"] for field in fields[1:]}
        assert {"research_project_id", "authorization.project_ids", "status", "tags"}.issubset(
            filter_paths
        )

        text = research_text_definition(kind)
        mappings = cast(dict[str, Any], text["mappings"])
        text_fields = cast(dict[str, Any], mappings["fields"])
        assert mappings["dynamic"] is False
        assert text_fields["authorization.project_ids"] == {"type": "token"}
        assert "embedding" not in text_fields

    chunk_fields = cast(dict[str, Any], research_text_definition("chunk")["mappings"]["fields"])
    assert chunk_fields["source_class"] == {"type": "token"}


@pytest.mark.parametrize("dimensions", [0, -1, 4_097, True])
def test_v3_vector_definitions_reject_invalid_dimensions(dimensions: object) -> None:
    with pytest.raises(ValueError, match="dimensions"):
        research_vector_definition("chunk", dimensions)  # type: ignore[arg-type]
