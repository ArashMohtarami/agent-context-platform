"""Idempotent standard MongoDB index definitions for Phase 3."""

from typing import Final

from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.asynchronous.database import AsyncDatabase

from app.db.agent_indexes import AGENT_STANDARD_INDEX_DEFINITIONS
from app.db.client_profile_indexes import CLIENT_PROFILE_STANDARD_INDEX_DEFINITIONS
from app.db.client_types import MongoDocument
from app.db.idempotency_indexes import IDEMPOTENCY_STANDARD_INDEX_DEFINITIONS
from app.db.research_indexes import RESEARCH_STANDARD_INDEX_DEFINITIONS

INDEX_DEFINITIONS: Final[dict[str, tuple[IndexModel, ...]]] = {
    **CLIENT_PROFILE_STANDARD_INDEX_DEFINITIONS,
    "skills": (
        IndexModel(
            [("skill_id", ASCENDING), ("version", ASCENDING)],
            unique=True,
            name="skill_version_uq",
        ),
        IndexModel(
            [("skill_id", ASCENDING), ("status", ASCENDING)],
            unique=True,
            partialFilterExpression={"status": "active"},
            name="one_active_skill_version_uq",
        ),
        IndexModel(
            [("category", ASCENDING), ("status", ASCENDING)],
            name="skill_category_status",
        ),
        IndexModel([("tags", ASCENDING)], name="skill_tags"),
        IndexModel(
            [("created_at", ASCENDING), ("_id", ASCENDING)],
            name="skill_created_page",
        ),
        IndexModel([("updated_at", DESCENDING)], name="skill_updated_at"),
        # Phase 5 discovery reads active skills newest-first.
        IndexModel(
            [("status", ASCENDING), ("updated_at", DESCENDING)],
            name="skill_status_updated",
        ),
    ),
    "skill_chunks": (
        IndexModel(
            [("skill_id", ASCENDING), ("skill_version", ASCENDING), ("chunk_id", ASCENDING)],
            unique=True,
            name="skill_chunk_uq",
        ),
        IndexModel(
            [
                ("skill_id", ASCENDING),
                ("skill_version", ASCENDING),
                ("chunk_index", ASCENDING),
            ],
            unique=True,
            name="skill_chunk_order_uq",
        ),
        IndexModel(
            [
                ("skill_id", ASCENDING),
                ("skill_version", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="skill_chunk_created_page",
        ),
        IndexModel([("updated_at", DESCENDING)], name="skill_chunk_updated_at"),
        # Supports finding chunks to re-embed when the active model changes.
        IndexModel(
            [("embedding_model", ASCENDING), ("embedding_dimensions", ASCENDING)],
            partialFilterExpression={"embedding_model": {"$type": "string"}},
            name="skill_chunk_embedding_model",
        ),
    ),
    "projects": (
        IndexModel([("project_id", ASCENDING)], unique=True, name="project_id_uq"),
        IndexModel([("tags", ASCENDING)], name="project_tags"),
        IndexModel(
            [("created_at", ASCENDING), ("_id", ASCENDING)],
            name="project_created_page",
        ),
        IndexModel([("updated_at", DESCENDING)], name="project_updated_at"),
    ),
    "project_context": (
        IndexModel(
            [
                ("project_id", ASCENDING),
                ("type", ASCENDING),
                ("status", ASCENDING),
                ("created_at", DESCENDING),
            ],
            name="context_project_type_status_created",
        ),
        IndexModel(
            [("project_id", ASCENDING), ("tags", ASCENDING)],
            name="context_project_tags",
        ),
        IndexModel(
            [("project_id", ASCENDING), ("updated_at", DESCENDING)],
            name="context_project_updated",
        ),
        # Phase 5 discovery reads only active context in a project by recency.
        IndexModel(
            [("project_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
            name="context_project_status_updated",
        ),
        IndexModel(
            [("project_id", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)],
            name="context_project_created_page",
        ),
        IndexModel(
            [("project_id", ASCENDING), ("external_id", ASCENDING)],
            unique=True,
            partialFilterExpression={"external_id": {"$type": "string"}},
            name="context_external_id_uq",
        ),
        # Supports finding context to re-embed when the active model changes.
        IndexModel(
            [("embedding_model", ASCENDING), ("embedding_dimensions", ASCENDING)],
            partialFilterExpression={"embedding_model": {"$type": "string"}},
            name="context_embedding_model",
        ),
    ),
    "memories": (
        IndexModel(
            [
                ("scope", ASCENDING),
                ("project_id", ASCENDING),
                ("status", ASCENDING),
                ("created_at", DESCENDING),
            ],
            name="memory_scope_project_status_created",
        ),
        IndexModel(
            [("project_id", ASCENDING), ("tags", ASCENDING)],
            name="memory_project_tags",
        ),
        IndexModel(
            [
                ("scope", ASCENDING),
                ("project_id", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="memory_scope_project_created_page",
        ),
        IndexModel([("updated_at", DESCENDING)], name="memory_updated_at"),
        # Phase 5 discovery keeps global and one project-memory scope separate.
        IndexModel(
            [
                ("scope", ASCENDING),
                ("project_id", ASCENDING),
                ("status", ASCENDING),
                ("updated_at", DESCENDING),
            ],
            name="memory_scope_project_status_updated",
        ),
        # V4 agent-scoped memories can share an idempotency key with another
        # client without becoming visible to it. The legacy index is replaced
        # by v027 after all old documents are backfilled with ``client_id``.
        IndexModel(
            [
                ("scope", ASCENDING),
                ("project_id", ASCENDING),
                ("client_id", ASCENDING),
                ("external_id", ASCENDING),
            ],
            unique=True,
            partialFilterExpression={"external_id": {"$type": "string"}},
            name="memory_scope_project_client_external_id_uq",
        ),
        IndexModel(
            [
                ("scope", ASCENDING),
                ("project_id", ASCENDING),
                ("client_id", ASCENDING),
                ("status", ASCENDING),
                ("updated_at", DESCENDING),
            ],
            name="memory_scope_project_client_status_updated",
        ),
        # Supports finding memories to re-embed when the active model changes.
        IndexModel(
            [("embedding_model", ASCENDING), ("embedding_dimensions", ASCENDING)],
            partialFilterExpression={"embedding_model": {"$type": "string"}},
            name="memory_embedding_model",
        ),
        # Supports walking a supersession chain from either end.
        IndexModel(
            [("supersedes", ASCENDING)],
            partialFilterExpression={"supersedes": {"$type": "objectId"}},
            name="memory_supersedes",
        ),
    ),
    "conversations": (
        IndexModel(
            [
                ("project_id", ASCENDING),
                ("session_id", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name="conversation_project_session_created",
        ),
        IndexModel(
            [("project_id", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)],
            name="conversation_project_created_page",
        ),
        IndexModel([("updated_at", DESCENDING)], name="conversation_updated_at"),
    ),
    "retrieval_logs": (
        IndexModel(
            [("project_id", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)],
            name="retrieval_project_created_page",
        ),
        IndexModel(
            [("created_at", ASCENDING), ("_id", ASCENDING)],
            name="retrieval_created_page",
        ),
        IndexModel(
            [("retrieval_method", ASCENDING), ("created_at", DESCENDING)],
            name="retrieval_method_created",
        ),
        IndexModel([("updated_at", DESCENDING)], name="retrieval_updated_at"),
        IndexModel(
            [("client_id", ASCENDING), ("created_at", DESCENDING)],
            name="retrieval_client_created",
        ),
        IndexModel(
            [("session_id", ASCENDING), ("created_at", DESCENDING)],
            name="retrieval_session_created",
        ),
    ),
    "api_keys": (
        # Authentication is one indexed read by identifier, never a scan: a
        # lookup that walked every key would slow down as a deployment issued
        # more of them, and would make the check's cost depend on the data.
        IndexModel([("key_id", ASCENDING)], name="api_key_id", unique=True),
        IndexModel([("status", ASCENDING), ("expires_at", ASCENDING)], name="api_key_usable"),
        IndexModel(
            [("client_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)],
            partialFilterExpression={"client_id": {"$type": "string"}},
            name="api_key_client_status_created",
        ),
        IndexModel(
            [("created_at", ASCENDING), ("_id", ASCENDING)],
            name="api_key_created_page",
        ),
    ),
    "audit_logs": (
        IndexModel(
            [("created_at", ASCENDING), ("_id", ASCENDING)],
            name="audit_created_page",
        ),
        IndexModel([("key_id", ASCENDING), ("created_at", DESCENDING)], name="audit_key_created"),
        IndexModel([("outcome", ASCENDING), ("created_at", DESCENDING)], name="audit_outcome"),
        IndexModel(
            [("project_id", ASCENDING), ("created_at", DESCENDING)],
            name="audit_project_created",
        ),
    ),
    "creator_profiles": (
        IndexModel([("profile_id", ASCENDING)], unique=True, name="creator_profile_id_uq"),
        IndexModel(
            [("status", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)],
            name="creator_profile_status_created_page",
        ),
        IndexModel([("tags", ASCENDING)], name="creator_profile_tags"),
        IndexModel([("updated_at", DESCENDING)], name="creator_profile_updated_at"),
    ),
    "content_pillars": (
        IndexModel(
            [("profile_id", ASCENDING), ("pillar_id", ASCENDING)],
            unique=True,
            name="content_pillar_profile_id_uq",
        ),
        IndexModel(
            [
                ("profile_id", ASCENDING),
                ("status", ASCENDING),
                ("priority", DESCENDING),
                ("created_at", ASCENDING),
            ],
            name="content_pillar_profile_status_priority",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)],
            name="content_pillar_profile_created_page",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("tags", ASCENDING)],
            name="content_pillar_profile_tags",
        ),
    ),
    "content_ideas": (
        IndexModel(
            [("profile_id", ASCENDING), ("idea_id", ASCENDING)],
            unique=True,
            name="content_idea_profile_id_uq",
        ),
        IndexModel(
            [
                ("profile_id", ASCENDING),
                ("status", ASCENDING),
                ("priority", DESCENDING),
                ("created_at", ASCENDING),
            ],
            name="content_idea_profile_status_priority",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("pillar_id", ASCENDING), ("status", ASCENDING)],
            name="content_idea_profile_pillar_status",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("platforms", ASCENDING), ("status", ASCENDING)],
            name="content_idea_profile_platform_status",
        ),
        IndexModel(
            [("origin.project_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)],
            name="content_idea_origin_project_status",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)],
            name="content_idea_profile_created_page",
        ),
        # Phase 5 reads all current ideas to suppress exact unpublished duplicates.
        IndexModel(
            [("profile_id", ASCENDING), ("updated_at", DESCENDING)],
            name="content_idea_profile_updated",
        ),
        IndexModel(
            [("embedding_model", ASCENDING), ("embedding_dimensions", ASCENDING)],
            partialFilterExpression={"embedding_model": {"$type": "string"}},
            name="content_idea_embedding_model",
        ),
    ),
    "content_briefs": (
        IndexModel(
            [("profile_id", ASCENDING), ("brief_id", ASCENDING)],
            unique=True,
            name="content_brief_profile_id_uq",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("idea_id", ASCENDING), ("platform", ASCENDING)],
            name="content_brief_profile_idea_platform",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)],
            name="content_brief_profile_status_created",
        ),
        IndexModel(
            [("source_refs.project_id", ASCENDING), ("created_at", DESCENDING)],
            name="content_brief_source_project_created",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)],
            name="content_brief_profile_created_page",
        ),
        IndexModel(
            [("embedding_model", ASCENDING), ("embedding_dimensions", ASCENDING)],
            partialFilterExpression={"embedding_model": {"$type": "string"}},
            name="content_brief_embedding_model",
        ),
    ),
    "content_items": (
        IndexModel(
            [("profile_id", ASCENDING), ("content_id", ASCENDING)],
            unique=True,
            name="content_item_profile_id_uq",
        ),
        IndexModel(
            [
                ("profile_id", ASCENDING),
                ("platform", ASCENDING),
                ("status", ASCENDING),
                ("published_at", DESCENDING),
            ],
            name="content_item_profile_platform_status",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("pillar_id", ASCENDING), ("status", ASCENDING)],
            name="content_item_profile_pillar_status",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("project_ids", ASCENDING), ("created_at", DESCENDING)],
            name="content_item_profile_project_created",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)],
            name="content_item_profile_created_page",
        ),
        # Phase 5 reads published current copies for explainable history hints.
        IndexModel(
            [("profile_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
            name="content_item_profile_status_updated",
        ),
        IndexModel(
            [("embedding_model", ASCENDING), ("embedding_dimensions", ASCENDING)],
            partialFilterExpression={"embedding_model": {"$type": "string"}},
            name="content_item_embedding_model",
        ),
    ),
    "content_versions": (
        IndexModel(
            [("profile_id", ASCENDING), ("content_id", ASCENDING), ("version", ASCENDING)],
            unique=True,
            name="content_version_profile_content_version_uq",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("content_id", ASCENDING), ("created_at", DESCENDING)],
            name="content_version_profile_content_created",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)],
            name="content_version_profile_created_page",
        ),
    ),
    "content_links": (
        IndexModel(
            [("profile_id", ASCENDING), ("link_id", ASCENDING)],
            unique=True,
            name="content_link_profile_id_uq",
        ),
        IndexModel(
            [
                ("profile_id", ASCENDING),
                ("content_id", ASCENDING),
                ("source_type", ASCENDING),
                ("source_id", ASCENDING),
                ("relation", ASCENDING),
            ],
            unique=True,
            name="content_link_provenance_uq",
        ),
        IndexModel(
            [("project_id", ASCENDING), ("source_type", ASCENDING), ("source_id", ASCENDING)],
            name="content_link_project_source",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("content_id", ASCENDING), ("status", ASCENDING)],
            name="content_link_profile_content_status",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)],
            name="content_link_profile_created_page",
        ),
    ),
    "content_metrics": (
        IndexModel(
            [("profile_id", ASCENDING), ("metric_id", ASCENDING)],
            unique=True,
            name="content_metric_profile_id_uq",
        ),
        IndexModel(
            [
                ("profile_id", ASCENDING),
                ("content_id", ASCENDING),
                ("platform", ASCENDING),
                ("captured_at", DESCENDING),
            ],
            name="content_metric_profile_content_captured",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("captured_at", DESCENDING)],
            name="content_metric_profile_captured",
        ),
    ),
    "content_campaigns": (
        IndexModel(
            [("profile_id", ASCENDING), ("campaign_id", ASCENDING)],
            unique=True,
            name="content_campaign_profile_id_uq",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)],
            name="content_campaign_profile_status_created",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("content_ids", ASCENDING)],
            name="content_campaign_profile_content",
        ),
        IndexModel(
            [("profile_id", ASCENDING), ("created_at", ASCENDING), ("_id", ASCENDING)],
            name="content_campaign_profile_created_page",
        ),
    ),
}

INDEX_DEFINITIONS.update(IDEMPOTENCY_STANDARD_INDEX_DEFINITIONS)


INDEX_DEFINITIONS.update(RESEARCH_STANDARD_INDEX_DEFINITIONS)
INDEX_DEFINITIONS.update(AGENT_STANDARD_INDEX_DEFINITIONS)


async def ensure_indexes(database: AsyncDatabase[MongoDocument]) -> None:
    """Create every Phase 3 standard index safely on repeated startup."""
    for collection_name, definitions in INDEX_DEFINITIONS.items():
        await database[collection_name].create_indexes(list(definitions))
