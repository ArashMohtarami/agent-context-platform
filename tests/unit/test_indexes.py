"""Standard-index tests covering every application collection."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.indexes import INDEX_DEFINITIONS, ensure_indexes


def test_index_definitions_cover_every_collection_without_vector_indexes() -> None:
    assert set(INDEX_DEFINITIONS) == {
        "skills",
        "skill_chunks",
        "projects",
        "project_context",
        "memories",
        "conversations",
        "retrieval_logs",
        # Phase 15 adds the credentials and the record of what they did.
        "api_keys",
        "audit_logs",
        "creator_profiles",
        "content_pillars",
        "content_ideas",
        "content_briefs",
        "content_items",
        "content_versions",
        "content_links",
        "content_metrics",
        "content_campaigns",
        "research_projects",
        "research_sources",
        "source_snapshots",
        "research_documents",
        "research_chunks",
        "research_notes",
        "research_claims",
        "research_citations",
        "research_briefs",
        "knowledge_candidates",
        "skill_update_proposals",
        "promotion_events",
        "ingestion_jobs",
        # V4 Phase 0 agent identity metadata remains operational data and must
        # receive the same index reconciliation as knowledge records.
        "agent_clients",
        "agent_sessions",
        "project_bindings",
        "agent_activity",
        "client_profiles",
        # V4 Phase 10 retry records are operational data and are also
        # reconciled by normal index initialization.
        "gateway_idempotency",
    }
    documents = [
        definition.document
        for definitions in INDEX_DEFINITIONS.values()
        for definition in definitions
    ]
    indexed_paths = {path for document in documents for path in document["key"]}
    # Vectors are indexed by mongot in a later phase, never as a standard index.
    assert "embedding" not in indexed_paths
    # Model metadata is indexed so a model migration can find chunks to re-embed.
    assert {"embedding_model", "embedding_dimensions"} <= indexed_paths


def test_discovery_recency_indexes_keep_phase_five_reads_scoped_and_ordered() -> None:
    """Recent-source queries stay bounded by their existing isolation keys."""
    names = {
        collection: {definition.document["name"] for definition in definitions}
        for collection, definitions in INDEX_DEFINITIONS.items()
    }

    assert {
        "skill_status_updated",
        "context_project_status_updated",
        "memory_scope_project_status_updated",
        "content_idea_profile_updated",
        "content_item_profile_status_updated",
    } <= set().union(*names.values())


@pytest.mark.asyncio
async def test_index_creation_is_repeatable_for_every_collection() -> None:
    database = MagicMock()
    collections: dict[str, MagicMock] = {}

    def collection_for(name: str) -> MagicMock:
        if name not in collections:
            collection = MagicMock()
            collection.create_indexes = AsyncMock()
            collections[name] = collection
        return collections[name]

    database.__getitem__.side_effect = collection_for
    typed_database = cast(AsyncDatabase[MongoDocument], database)

    await ensure_indexes(typed_database)
    await ensure_indexes(typed_database)

    for collection_name, definitions in INDEX_DEFINITIONS.items():
        calls = collections[collection_name].create_indexes.await_args_list
        assert calls == [call(list(definitions)), call(list(definitions))]
