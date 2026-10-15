"""V3 Phase 0 MongoDB and mongot index definitions.

This module creates only derived indexes.  It deliberately contains no search
queries, scoring, ingestion, or retrieval policy; those are later V3 phases.
"""

from typing import Final, Literal

from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.asynchronous.database import AsyncDatabase

from app.db.client_types import MongoDocument
from app.db.search_indexes import SearchIndexAction, describe_search_index, reconcile_search_index
from app.db.text_indexes import (
    identifier_analyzer_definition,
    searchable_text_field,
    text_definition_matches,
)
from app.db.vector_indexes import (
    VECTOR_INDEX_TYPE,
    VECTOR_PATH,
    VECTOR_SIMILARITY,
    vector_definition_matches,
)
from app.embeddings.types import MAX_EMBEDDING_DIMENSIONS

ResearchSearchCollectionKind = Literal["chunk", "note", "claim", "brief", "candidate"]

RESEARCH_STANDARD_INDEX_DEFINITIONS: Final[dict[str, tuple[IndexModel, ...]]] = {
    "research_projects": (
        IndexModel(
            [("research_project_id", ASCENDING)], unique=True, name="research_project_id_uq"
        ),
        IndexModel(
            [
                ("authorization.project_ids", ASCENDING),
                ("status", ASCENDING),
                ("updated_at", DESCENDING),
            ],
            name="research_project_auth_status_updated",
        ),
        IndexModel(
            [("type", ASCENDING), ("status", ASCENDING)], name="research_project_type_status"
        ),
        IndexModel([("tags", ASCENDING)], name="research_project_tags"),
        IndexModel(
            [("created_at", ASCENDING), ("_id", ASCENDING)], name="research_project_created_page"
        ),
    ),
    "research_sources": (
        IndexModel([("source_id", ASCENDING)], unique=True, name="research_source_id_uq"),
        IndexModel(
            [("research_project_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
            name="research_source_project_status_updated",
        ),
        IndexModel(
            [("research_project_id", ASCENDING), ("canonical_uri", ASCENDING)],
            unique=True,
            partialFilterExpression={"canonical_uri": {"$type": "string"}},
            name="research_source_project_canonical_uri_uq",
        ),
        IndexModel(
            [
                ("research_project_id", ASCENDING),
                ("source_class", ASCENDING),
                ("published_at", DESCENDING),
            ],
            name="research_source_project_class_published",
        ),
        IndexModel(
            [
                ("research_project_id", ASCENDING),
                ("source_type", ASCENDING),
                ("title", ASCENDING),
                ("publisher", ASCENDING),
                ("author", ASCENDING),
                ("published_at", ASCENDING),
            ],
            name="research_source_project_metadata",
        ),
        IndexModel(
            [
                ("authorization.project_ids", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="research_source_auth_created_page",
        ),
    ),
    "source_snapshots": (
        IndexModel(
            [("source_id", ASCENDING), ("snapshot_id", ASCENDING)],
            unique=True,
            name="source_snapshot_source_id_uq",
        ),
        IndexModel(
            [
                ("research_project_id", ASCENDING),
                ("source_id", ASCENDING),
                ("retrieved_at", DESCENDING),
            ],
            name="source_snapshot_project_source_retrieved",
        ),
        IndexModel(
            [("source_id", ASCENDING), ("content_hash", ASCENDING)],
            name="source_snapshot_source_hash",
        ),
        IndexModel(
            [("research_project_id", ASCENDING), ("content_hash", ASCENDING)],
            name="source_snapshot_project_hash",
        ),
        IndexModel(
            [
                ("authorization.project_ids", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="source_snapshot_auth_created_page",
        ),
    ),
    "research_documents": (
        IndexModel(
            [("source_id", ASCENDING), ("snapshot_id", ASCENDING), ("document_id", ASCENDING)],
            unique=True,
            name="research_document_snapshot_id_uq",
        ),
        IndexModel(
            [("research_project_id", ASCENDING), ("source_id", ASCENDING), ("status", ASCENDING)],
            name="research_document_project_source_status",
        ),
        IndexModel(
            [("source_id", ASCENDING), ("content_hash", ASCENDING)],
            name="research_document_source_hash",
        ),
        IndexModel(
            [
                ("authorization.project_ids", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="research_document_auth_created_page",
        ),
    ),
    "research_chunks": (
        IndexModel(
            [("document_id", ASCENDING), ("chunk_id", ASCENDING)],
            unique=True,
            name="research_chunk_document_id_uq",
        ),
        IndexModel(
            [("document_id", ASCENDING), ("chunk_index", ASCENDING)],
            unique=True,
            name="research_chunk_document_order_uq",
        ),
        IndexModel(
            [
                ("research_project_id", ASCENDING),
                ("source_class", ASCENDING),
                ("status", ASCENDING),
                ("updated_at", DESCENDING),
            ],
            name="research_chunk_project_class_status_updated",
        ),
        IndexModel(
            [
                ("authorization.project_ids", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="research_chunk_auth_created_page",
        ),
        IndexModel(
            [("embedding_model", ASCENDING), ("embedding_dimensions", ASCENDING)],
            partialFilterExpression={"embedding_model": {"$type": "string"}},
            name="research_chunk_embedding_model",
        ),
    ),
    "research_notes": (
        IndexModel(
            [("research_project_id", ASCENDING), ("note_id", ASCENDING)],
            unique=True,
            name="research_note_project_id_uq",
        ),
        IndexModel(
            [("research_project_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
            name="research_note_project_status_updated",
        ),
        IndexModel(
            [
                ("authorization.project_ids", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="research_note_auth_created_page",
        ),
        IndexModel(
            [("embedding_model", ASCENDING), ("embedding_dimensions", ASCENDING)],
            partialFilterExpression={"embedding_model": {"$type": "string"}},
            name="research_note_embedding_model",
        ),
    ),
    "research_claims": (
        IndexModel(
            [("research_project_id", ASCENDING), ("claim_id", ASCENDING)],
            unique=True,
            name="research_claim_project_id_uq",
        ),
        IndexModel(
            [("research_project_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
            name="research_claim_project_status_updated",
        ),
        IndexModel(
            [
                ("authorization.project_ids", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="research_claim_auth_created_page",
        ),
        IndexModel(
            [("embedding_model", ASCENDING), ("embedding_dimensions", ASCENDING)],
            partialFilterExpression={"embedding_model": {"$type": "string"}},
            name="research_claim_embedding_model",
        ),
    ),
    "research_citations": (
        IndexModel(
            [("claim_id", ASCENDING), ("citation_id", ASCENDING)],
            unique=True,
            name="research_citation_claim_id_uq",
        ),
        IndexModel(
            [
                ("research_project_id", ASCENDING),
                ("source_id", ASCENDING),
                ("snapshot_id", ASCENDING),
            ],
            name="research_citation_project_source_snapshot",
        ),
        IndexModel(
            [("claim_id", ASCENDING), ("relation", ASCENDING)],
            name="research_citation_claim_relation",
        ),
        IndexModel(
            [
                ("authorization.project_ids", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="research_citation_auth_created_page",
        ),
    ),
    "research_briefs": (
        IndexModel(
            [("research_project_id", ASCENDING), ("brief_id", ASCENDING)],
            unique=True,
            name="research_brief_project_id_uq",
        ),
        IndexModel(
            [("research_project_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
            name="research_brief_project_status_updated",
        ),
        IndexModel(
            [
                ("authorization.project_ids", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="research_brief_auth_created_page",
        ),
        IndexModel(
            [("embedding_model", ASCENDING), ("embedding_dimensions", ASCENDING)],
            partialFilterExpression={"embedding_model": {"$type": "string"}},
            name="research_brief_embedding_model",
        ),
    ),
    "knowledge_candidates": (
        IndexModel(
            [("research_project_id", ASCENDING), ("candidate_id", ASCENDING)],
            unique=True,
            name="knowledge_candidate_project_id_uq",
        ),
        IndexModel(
            [
                ("research_project_id", ASCENDING),
                ("status", ASCENDING),
                ("candidate_type", ASCENDING),
                ("updated_at", DESCENDING),
            ],
            name="knowledge_candidate_project_status_type_updated",
        ),
        IndexModel(
            [("target_project_id", ASCENDING), ("status", ASCENDING)],
            partialFilterExpression={"target_project_id": {"$type": "string"}},
            name="knowledge_candidate_target_project_status",
        ),
        IndexModel(
            [
                ("authorization.project_ids", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="knowledge_candidate_auth_created_page",
        ),
        IndexModel(
            [("embedding_model", ASCENDING), ("embedding_dimensions", ASCENDING)],
            partialFilterExpression={"embedding_model": {"$type": "string"}},
            name="knowledge_candidate_embedding_model",
        ),
    ),
    "skill_update_proposals": (
        IndexModel(
            [("research_project_id", ASCENDING), ("proposal_id", ASCENDING)],
            unique=True,
            name="skill_update_proposal_project_id_uq",
        ),
        IndexModel(
            [
                ("research_project_id", ASCENDING),
                ("status", ASCENDING),
                ("updated_at", DESCENDING),
            ],
            name="skill_update_proposal_project_status_updated",
        ),
        IndexModel(
            [("skill_id", ASCENDING), ("current_version", ASCENDING), ("updated_at", DESCENDING)],
            name="skill_update_proposal_skill_version_updated",
        ),
        IndexModel(
            [
                ("authorization.project_ids", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="skill_update_proposal_auth_created_page",
        ),
    ),
    "promotion_events": (
        IndexModel(
            [("candidate_id", ASCENDING), ("event_id", ASCENDING)],
            unique=True,
            name="promotion_event_candidate_id_uq",
        ),
        IndexModel(
            [
                ("research_project_id", ASCENDING),
                ("candidate_id", ASCENDING),
                ("decided_at", DESCENDING),
            ],
            name="promotion_event_project_candidate_decided",
        ),
        IndexModel(
            [
                ("authorization.project_ids", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="promotion_event_auth_created_page",
        ),
    ),
    "ingestion_jobs": (
        IndexModel([("job_id", ASCENDING)], unique=True, name="ingestion_job_id_uq"),
        IndexModel(
            [("research_project_id", ASCENDING), ("source_id", ASCENDING), ("status", ASCENDING)],
            name="ingestion_job_project_source_status",
        ),
        IndexModel(
            [("status", ASCENDING), ("created_at", ASCENDING)], name="ingestion_job_status_created"
        ),
        IndexModel(
            [
                ("authorization.project_ids", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="ingestion_job_auth_created_page",
        ),
    ),
}

RESEARCH_SEARCH_COLLECTIONS: Final[dict[ResearchSearchCollectionKind, str]] = {
    "chunk": "research_chunks",
    "note": "research_notes",
    "claim": "research_claims",
    "brief": "research_briefs",
    "candidate": "knowledge_candidates",
}
RESEARCH_VECTOR_INDEXES: Final[dict[ResearchSearchCollectionKind, str]] = {
    kind: f"{collection.removesuffix('s')}_vector_index"
    for kind, collection in RESEARCH_SEARCH_COLLECTIONS.items()
}
RESEARCH_TEXT_INDEXES: Final[dict[ResearchSearchCollectionKind, str]] = {
    kind: f"{collection.removesuffix('s')}_text_index"
    for kind, collection in RESEARCH_SEARCH_COLLECTIONS.items()
}
_PRIMARY_TEXT_PATHS: Final[dict[ResearchSearchCollectionKind, str]] = {
    "chunk": "content",
    "note": "body",
    "claim": "statement",
    "brief": "summary",
    "candidate": "content",
}
_SECONDARY_TEXT_PATHS: Final[dict[ResearchSearchCollectionKind, tuple[tuple[str, float], ...]]] = {
    "chunk": (("section", 0.5),),
    "note": (("title", 1.5),),
    "claim": (),
    "brief": (("title", 1.5), ("question", 1.25)),
    "candidate": (("title", 1.5),),
}
_FILTER_PATHS: Final[dict[ResearchSearchCollectionKind, tuple[str, ...]]] = {
    "chunk": ("research_project_id", "authorization.project_ids", "source_class", "status", "tags"),
    "note": ("research_project_id", "authorization.project_ids", "status", "tags"),
    "claim": ("research_project_id", "authorization.project_ids", "status", "tags"),
    "brief": ("research_project_id", "authorization.project_ids", "status", "tags"),
    "candidate": ("research_project_id", "authorization.project_ids", "status", "tags"),
}


def research_vector_definition(
    kind: ResearchSearchCollectionKind, dimensions: int
) -> MongoDocument:
    """Build a V3 vector index definition; all filters live on the same record."""
    _require_kind(kind)
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
            *({"type": "filter", "path": path} for path in ("org_id", "_id", *_FILTER_PATHS[kind])),
        ]
    }


def research_text_definition(kind: ResearchSearchCollectionKind) -> MongoDocument:
    """Build a V3 lexical index with only citable/searchable text and filters."""
    _require_kind(kind)
    fields: MongoDocument = {
        "org_id": {"type": "token"},
        "_id": {"type": "objectId"},
        _PRIMARY_TEXT_PATHS[kind]: searchable_text_field(exact=True),
        "research_project_id": {"type": "token"},
        "authorization.project_ids": {"type": "token"},
        "status": {"type": "token"},
        "tags": {"type": "token"},
    }
    if kind == "chunk":
        fields["source_class"] = {"type": "token"}
    for path, _ in _SECONDARY_TEXT_PATHS[kind]:
        fields[path] = searchable_text_field()
    return {
        "analyzers": [identifier_analyzer_definition()],
        "mappings": {"dynamic": False, "fields": fields},
    }


async def ensure_research_vector_indexes(
    database: AsyncDatabase[MongoDocument], *, dimensions: int
) -> tuple[SearchIndexAction, ...]:
    """Idempotently reconcile V3 vector indexes through mongod/mongot."""
    actions: list[SearchIndexAction] = []
    for kind, collection in RESEARCH_SEARCH_COLLECTIONS.items():
        name = RESEARCH_VECTOR_INDEXES[kind]
        definition = research_vector_definition(kind, dimensions)
        existing = await describe_search_index(database, collection, name)
        actions.append(
            await reconcile_search_index(
                database,
                collection,
                name=name,
                index_type=VECTOR_INDEX_TYPE,
                definition=definition,
                matches=vector_definition_matches(existing.definition, definition),
                existing=existing,
            )
        )
    return tuple(actions)


async def ensure_research_text_indexes(
    database: AsyncDatabase[MongoDocument],
) -> tuple[SearchIndexAction, ...]:
    """Idempotently reconcile V3 lexical indexes through mongod/mongot."""
    actions: list[SearchIndexAction] = []
    for kind, collection in RESEARCH_SEARCH_COLLECTIONS.items():
        name = RESEARCH_TEXT_INDEXES[kind]
        definition = research_text_definition(kind)
        existing = await describe_search_index(database, collection, name)
        actions.append(
            await reconcile_search_index(
                database,
                collection,
                name=name,
                index_type="search",
                definition=definition,
                matches=text_definition_matches(existing.definition, definition),
                existing=existing,
            )
        )
    return tuple(actions)


def _require_kind(kind: ResearchSearchCollectionKind) -> None:
    if kind not in RESEARCH_SEARCH_COLLECTIONS:
        raise ValueError("unknown research collection kind")


__all__ = [
    "RESEARCH_SEARCH_COLLECTIONS",
    "RESEARCH_STANDARD_INDEX_DEFINITIONS",
    "RESEARCH_TEXT_INDEXES",
    "RESEARCH_VECTOR_INDEXES",
    "ResearchSearchCollectionKind",
    "ensure_research_text_indexes",
    "ensure_research_vector_indexes",
    "research_text_definition",
    "research_vector_definition",
]
