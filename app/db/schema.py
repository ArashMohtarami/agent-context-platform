"""MongoDB JSON Schema validators for the application collections."""

from typing import Final

from app.db.agent_schema import AGENT_COLLECTION_VALIDATORS
from app.db.client_profile_schema import CLIENT_PROFILE_COLLECTION_VALIDATORS
from app.db.client_types import MongoDocument
from app.db.idempotency_schema import IDEMPOTENCY_COLLECTION_VALIDATORS
from app.db.provenance_schema import ACTOR_PROVENANCE
from app.db.research_schema import (
    RESEARCH_BRIEF_CITATION_REFERENCE,
    RESEARCH_BRIEF_CLAIM_REFERENCE,
    RESEARCH_COLLECTION_VALIDATORS,
)
from app.embeddings.types import MAX_EMBEDDING_DIMENSIONS, MAX_EMBEDDING_MODEL_NAME_LENGTH
from app.models.agent import AgentClientType
from app.models.content import (
    ContentBriefStatus,
    ContentCampaignStatus,
    ContentFactualReviewStatus,
    ContentLinkStatus,
    ContentMetricStatus,
    ContentPillarStatus,
    ContentPlatform,
    ContentPublicationProvider,
    ContentRelationType,
    ContentStatus,
    CreatorProfileStatus,
)
from app.models.freshness import FreshnessClass
from app.security.scopes import ALL_SCOPES as SCOPE_NAMES

STRING: Final[MongoDocument] = {"bsonType": "string"}
IDENTIFIER: Final[MongoDocument] = {
    "bsonType": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$",
}
DATE: Final[MongoDocument] = {"bsonType": "date"}
REVISION: Final[MongoDocument] = {"bsonType": ["int", "long"], "minimum": 1}
TAGS: Final[MongoDocument] = {
    "bsonType": "array",
    "maxItems": 50,
    "uniqueItems": True,
    "items": {"bsonType": "string", "minLength": 1, "maxLength": 64},
}
METADATA: Final[MongoDocument] = {"bsonType": "object"}
LIFECYCLE_STATUSES: Final[list[str]] = ["active", "archived", "superseded"]
CONTENT_STATUSES: Final[list[str]] = [status.value for status in ContentStatus]
CONTENT_PLATFORMS: Final[list[str]] = [platform.value for platform in ContentPlatform]
CONTENT_PUBLICATION_PROVIDERS: Final[list[str]] = [
    provider.value for provider in ContentPublicationProvider
]
CONTENT_RELATIONS: Final[list[str]] = [relation.value for relation in ContentRelationType]
CREATOR_PROFILE_STATUSES: Final[list[str]] = [status.value for status in CreatorProfileStatus]
CONTENT_PILLAR_STATUSES: Final[list[str]] = [status.value for status in ContentPillarStatus]
CONTENT_BRIEF_STATUSES: Final[list[str]] = [status.value for status in ContentBriefStatus]
CONTENT_FACTUAL_REVIEW_STATUSES: Final[list[str]] = [
    status.value for status in ContentFactualReviewStatus
]
CONTENT_LINK_STATUSES: Final[list[str]] = [status.value for status in ContentLinkStatus]
CONTENT_METRIC_STATUSES: Final[list[str]] = [status.value for status in ContentMetricStatus]
CONTENT_CAMPAIGN_STATUSES: Final[list[str]] = [status.value for status in ContentCampaignStatus]
PREFERENCE_LIST: Final[MongoDocument] = {
    "bsonType": "array",
    "maxItems": 50,
    "uniqueItems": True,
    "items": {"bsonType": "string", "minLength": 1, "maxLength": 500},
}
IDENTIFIER_LIST: Final[MongoDocument] = {
    "bsonType": "array",
    "maxItems": 50,
    "uniqueItems": True,
    "items": IDENTIFIER,
}
PLATFORM_LIST: Final[MongoDocument] = {
    "bsonType": "array",
    "minItems": 1,
    "maxItems": 16,
    "uniqueItems": True,
    "items": {"enum": CONTENT_PLATFORMS},
}
OPTIONAL_PLATFORM_LIST: Final[MongoDocument] = {
    "bsonType": "array",
    "maxItems": 16,
    "uniqueItems": True,
    "items": {"enum": CONTENT_PLATFORMS},
}
CONTENT_SOURCE_REFERENCE: Final[MongoDocument] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["source_type", "source_id", "project_id"],
    "properties": {
        "source_type": IDENTIFIER,
        "source_id": IDENTIFIER,
        "project_id": {"bsonType": ["string", "null"]},
    },
}
CONTENT_SOURCE_REFERENCES: Final[MongoDocument] = {
    "bsonType": "array",
    "maxItems": 100,
    "items": CONTENT_SOURCE_REFERENCE,
}
PROMOTION_PROVENANCE: Final[MongoDocument] = {
    "bsonType": ["object", "null"],
    "additionalProperties": False,
    "required": [
        "candidate_id",
        "research_project_id",
        "claim_ids",
        "citation_ids",
        "source_ids",
        "source_snapshot_ids",
        "verified_at",
        "review_after",
        "freshness_class",
    ],
    "properties": {
        "candidate_id": IDENTIFIER,
        "research_project_id": IDENTIFIER,
        "claim_ids": {
            "bsonType": "array",
            "maxItems": 100,
            "uniqueItems": True,
            "items": IDENTIFIER,
        },
        "citation_ids": {
            "bsonType": "array",
            "maxItems": 200,
            "uniqueItems": True,
            "items": IDENTIFIER,
        },
        "source_ids": {
            "bsonType": "array",
            "maxItems": 200,
            "uniqueItems": True,
            "items": IDENTIFIER,
        },
        "source_snapshot_ids": {
            "bsonType": "array",
            "maxItems": 200,
            "uniqueItems": True,
            "items": IDENTIFIER,
        },
        "verified_at": DATE,
        "review_after": {"bsonType": ["date", "null"]},
        "freshness_class": {"enum": [value.value for value in FreshnessClass]},
    },
}

CONTENT_ORIGIN: Final[MongoDocument] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["type", "project_id", "source_ids"],
    "properties": {
        "type": IDENTIFIER,
        "project_id": {"bsonType": ["string", "null"]},
        "source_ids": IDENTIFIER_LIST,
    },
}

EMBEDDING_PROPERTIES: Final[MongoDocument] = {
    "embedding": {
        "bsonType": ["array", "null"],
        "minItems": 1,
        "maxItems": MAX_EMBEDDING_DIMENSIONS,
        "items": {"bsonType": "double"},
    },
    "embedding_model": {
        "bsonType": ["string", "null"],
        "minLength": 1,
        "maxLength": MAX_EMBEDDING_MODEL_NAME_LENGTH,
    },
    "embedding_dimensions": {
        "bsonType": ["int", "long", "null"],
        "minimum": 1,
        "maximum": MAX_EMBEDDING_DIMENSIONS,
    },
    "embedding_generated_at": {"bsonType": ["date", "null"]},
}
EMBEDDING_REQUIRED: Final[list[str]] = [
    "embedding",
    "embedding_model",
    "embedding_dimensions",
    "embedding_generated_at",
]


def document_schema(required_fields: list[str], properties: MongoDocument) -> MongoDocument:
    """Add common strict document requirements."""
    common: MongoDocument = {
        "_id": {"bsonType": "objectId"},
        "created_at": DATE,
        "updated_at": DATE,
        "revision": REVISION,
    }
    return {
        "$jsonSchema": {
            "bsonType": "object",
            "additionalProperties": False,
            "required": ["_id", "created_at", "updated_at", "revision", *required_fields],
            "properties": {**common, **properties},
        }
    }


def content_document_schema(required_fields: list[str], properties: MongoDocument) -> MongoDocument:
    """Build a V2 validator with actor provenance required on every record."""
    return document_schema(
        [*required_fields, "actor"],
        {"actor": ACTOR_PROVENANCE, **properties},
    )


COLLECTION_VALIDATORS: Final[dict[str, MongoDocument]] = {
    **CLIENT_PROFILE_COLLECTION_VALIDATORS,
    "skills": document_schema(
        [
            "skill_id",
            "name",
            "version",
            "description",
            "category",
            "tags",
            "trust_level",
            "status",
            "source_path",
            "content_hash",
        ],
        {
            "skill_id": IDENTIFIER,
            "name": {"bsonType": "string", "minLength": 1, "maxLength": 500},
            "version": {"bsonType": "string", "minLength": 5, "maxLength": 64},
            "description": {"bsonType": "string", "minLength": 1, "maxLength": 4_000},
            "category": IDENTIFIER,
            "tags": TAGS,
            "trust_level": {"enum": ["trusted", "user", "external", "generated"]},
            "status": {"enum": LIFECYCLE_STATUSES},
            "source_path": {"bsonType": "string", "minLength": 1, "maxLength": 2_048},
            "content_hash": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
        },
    ),
    "skill_chunks": document_schema(
        [
            "skill_id",
            "skill_version",
            "chunk_id",
            "content",
            "section",
            "chunk_index",
            "tokens",
            "metadata",
            *EMBEDDING_REQUIRED,
        ],
        {
            "skill_id": IDENTIFIER,
            "skill_version": {"bsonType": "string", "minLength": 5, "maxLength": 64},
            "chunk_id": IDENTIFIER,
            "content": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
            "section": {"bsonType": ["string", "null"]},
            "chunk_index": {"bsonType": ["int", "long"], "minimum": 0},
            "tokens": {"bsonType": ["int", "long"], "minimum": 0, "maximum": 100_000},
            "metadata": METADATA,
            **EMBEDDING_PROPERTIES,
        },
    ),
    "projects": document_schema(
        ["project_id", "name", "description", "repository", "environment", "tags"],
        {
            "project_id": IDENTIFIER,
            "name": {"bsonType": "string", "minLength": 1, "maxLength": 500},
            "description": {"bsonType": "string", "minLength": 1, "maxLength": 4_000},
            "repository": {"bsonType": "string", "minLength": 1, "maxLength": 2_048},
            "environment": IDENTIFIER,
            "tags": TAGS,
        },
    ),
    "project_context": document_schema(
        [
            "project_id",
            "type",
            "title",
            "content",
            "importance",
            "tags",
            "source",
            "status",
            "external_id",
            "actor",
            *EMBEDDING_REQUIRED,
        ],
        {
            "project_id": IDENTIFIER,
            "type": {
                "enum": [
                    "architecture",
                    "architecture_decision",
                    "business_rule",
                    "api",
                    "database",
                    "infrastructure",
                    "deployment",
                    "security",
                    "integration",
                    "convention",
                    "troubleshooting",
                    "known_issue",
                    "implementation",
                    "constraint",
                ]
            },
            "title": {"bsonType": "string", "minLength": 1, "maxLength": 500},
            "content": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
            "importance": {"bsonType": "double", "minimum": 0.0, "maximum": 1.0},
            "tags": TAGS,
            "source": IDENTIFIER,
            "status": {"enum": LIFECYCLE_STATUSES},
            "external_id": {"bsonType": ["string", "null"]},
            "promotion_provenance": PROMOTION_PROVENANCE,
            "actor": ACTOR_PROVENANCE,
            **EMBEDDING_PROPERTIES,
        },
    ),
    "memories": {
        "$and": [
            document_schema(
                [
                    "scope",
                    "project_id",
                    "client_id",
                    "type",
                    "content",
                    "summary",
                    "importance",
                    "confidence",
                    "tags",
                    "source",
                    "status",
                    "external_id",
                    "last_used_at",
                    "supersedes",
                    "superseded_by",
                    "actor",
                    *EMBEDDING_REQUIRED,
                ],
                {
                    "scope": {"enum": ["global", "project", "agent", "project_agent"]},
                    "project_id": {"bsonType": ["string", "null"]},
                    "client_id": {"bsonType": ["string", "null"]},
                    "type": IDENTIFIER,
                    "content": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
                    "summary": {"bsonType": ["string", "null"], "maxLength": 500},
                    "importance": {"bsonType": "double", "minimum": 0.0, "maximum": 1.0},
                    "confidence": {"bsonType": "double", "minimum": 0.0, "maximum": 1.0},
                    "tags": TAGS,
                    "source": IDENTIFIER,
                    "status": {"enum": LIFECYCLE_STATUSES},
                    "external_id": {"bsonType": ["string", "null"]},
                    "last_used_at": {"bsonType": ["date", "null"]},
                    "supersedes": {"bsonType": ["objectId", "null"]},
                    "superseded_by": {"bsonType": ["objectId", "null"]},
                    "promotion_provenance": PROMOTION_PROVENANCE,
                    "actor": ACTOR_PROVENANCE,
                    **EMBEDDING_PROPERTIES,
                },
            ),
            {
                "$or": [
                    {"scope": "global", "project_id": None, "client_id": None},
                    {"scope": "project", "project_id": {"$type": "string"}, "client_id": None},
                    {"scope": "agent", "project_id": None, "client_id": {"$type": "string"}},
                    {
                        "scope": "project_agent",
                        "project_id": {"$type": "string"},
                        "client_id": {"$type": "string"},
                    },
                ]
            },
        ]
    },
    "conversations": document_schema(
        ["project_id", "session_id", "role", "content", "metadata"],
        {
            "project_id": IDENTIFIER,
            "session_id": IDENTIFIER,
            "role": {"enum": ["system", "user", "assistant", "tool"]},
            "content": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
            "metadata": METADATA,
        },
    ),
    "retrieval_logs": document_schema(
        [
            "query",
            "project_id",
            "requested_sources",
            "result_count",
            "selected_result_ids",
            "latency_ms",
            "retrieval_method",
            "client_id",
            "client_type",
            "session_id",
        ],
        {
            "query": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
            "project_id": {"bsonType": ["string", "null"]},
            "requested_sources": {
                "bsonType": "array",
                "minItems": 1,
                "maxItems": 10,
                "uniqueItems": True,
                "items": IDENTIFIER,
            },
            "result_count": {"bsonType": ["int", "long"], "minimum": 0},
            "selected_result_ids": {
                "bsonType": "array",
                "maxItems": 50,
                "uniqueItems": True,
                "items": STRING,
            },
            "latency_ms": {"bsonType": "double", "minimum": 0.0},
            "retrieval_method": {"enum": ["lexical", "vector", "hybrid"]},
            "client_id": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 128},
            "client_type": {
                "enum": [None, *[client_type.value for client_type in AgentClientType]]
            },
            "session_id": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 128},
        },
    ),
    "api_keys": document_schema(
        ["key_id", "client_id", "key_hash", "name", "scopes", "status", "revoked_at"],
        {
            "key_id": {"bsonType": "string", "pattern": "^[0-9a-f]{16}$"},
            "client_id": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 128},
            # A hash, never a key. The pattern is what a SHA-256 digest looks
            # like, so a plaintext secret cannot be stored here by mistake.
            "key_hash": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
            "name": {"bsonType": "string", "minLength": 1, "maxLength": 500},
            "scopes": {
                "bsonType": "array",
                "minItems": 1,
                "maxItems": 32,
                "uniqueItems": True,
                "items": {"enum": list(SCOPE_NAMES)},
            },
            "projects": {
                "bsonType": "array",
                "maxItems": 100,
                "uniqueItems": True,
                "items": IDENTIFIER,
            },
            "profiles": {
                "bsonType": "array",
                "maxItems": 100,
                "uniqueItems": True,
                "items": IDENTIFIER,
            },
            "status": {"enum": ["active", "revoked"]},
            "expires_at": {"bsonType": ["date", "null"]},
            "last_used_at": {"bsonType": ["date", "null"]},
            "revoked_at": {"bsonType": ["date", "null"]},
        },
    ),
    "audit_logs": document_schema(
        ["action", "outcome", "key_id", "actor"],
        {
            "action": {
                "enum": [
                    "authenticate",
                    "authorize",
                    "key.create",
                    "key.revoke",
                    "context.write",
                    "memory.write",
                    "creator.write",
                    "content.write",
                    "content.approve",
                    "content.publish",
                    "research.write",
                ]
            },
            "outcome": {"enum": ["allowed", "denied", "failed"]},
            "key_id": {"bsonType": "string", "minLength": 1, "maxLength": 64},
            "key_name": {"bsonType": ["string", "null"], "maxLength": 500},
            "project_id": {"bsonType": ["string", "null"]},
            "profile_id": {"bsonType": ["string", "null"]},
            "research_project_id": {"bsonType": ["string", "null"]},
            "scope": {"bsonType": ["string", "null"], "maxLength": 64},
            "method": {"bsonType": ["string", "null"], "maxLength": 8},
            "path": {"bsonType": ["string", "null"], "maxLength": 200},
            "request_id": {"bsonType": ["string", "null"], "maxLength": 64},
            "reason": {"bsonType": ["string", "null"], "maxLength": 200},
            "actor": ACTOR_PROVENANCE,
        },
    ),
    "creator_profiles": content_document_schema(
        [
            "profile_id",
            "name",
            "status",
            "tags",
            "audiences",
            "domains",
            "voice",
            "writing_preferences",
            "formatting_preferences",
            "terminology_preferences",
            "language_preferences",
            "tone_preferences",
            "topics_of_interest",
            "topics_to_avoid",
            "content_objectives",
        ],
        {
            "profile_id": IDENTIFIER,
            "name": {"bsonType": "string", "minLength": 1, "maxLength": 500},
            "status": {"enum": CREATOR_PROFILE_STATUSES},
            "tags": TAGS,
            "audiences": PREFERENCE_LIST,
            "domains": PREFERENCE_LIST,
            "voice": {
                "bsonType": "object",
                "additionalProperties": False,
                "required": ["tone", "avoid"],
                "properties": {"tone": PREFERENCE_LIST, "avoid": PREFERENCE_LIST},
            },
            "writing_preferences": PREFERENCE_LIST,
            "formatting_preferences": PREFERENCE_LIST,
            "terminology_preferences": PREFERENCE_LIST,
            "language_preferences": PREFERENCE_LIST,
            "tone_preferences": PREFERENCE_LIST,
            "topics_of_interest": PREFERENCE_LIST,
            "topics_to_avoid": PREFERENCE_LIST,
            "content_objectives": PREFERENCE_LIST,
        },
    ),
    "content_pillars": content_document_schema(
        ["profile_id", "pillar_id", "name", "description", "tags", "priority", "status"],
        {
            "profile_id": IDENTIFIER,
            "pillar_id": IDENTIFIER,
            "name": {"bsonType": "string", "minLength": 1, "maxLength": 500},
            "description": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
            "tags": TAGS,
            "priority": {"bsonType": "double", "minimum": 0.0, "maximum": 1.0},
            "status": {"enum": CONTENT_PILLAR_STATUSES},
        },
    ),
    "content_ideas": content_document_schema(
        [
            "profile_id",
            "idea_id",
            "title",
            "description",
            "pillar_id",
            "platforms",
            "origin",
            "tags",
            "priority",
            "status",
            *EMBEDDING_REQUIRED,
        ],
        {
            "profile_id": IDENTIFIER,
            "idea_id": IDENTIFIER,
            "title": {"bsonType": "string", "minLength": 1, "maxLength": 500},
            "description": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
            "pillar_id": {"bsonType": ["string", "null"]},
            "platforms": PLATFORM_LIST,
            "origin": CONTENT_ORIGIN,
            "tags": TAGS,
            "priority": {"bsonType": "double", "minimum": 0.0, "maximum": 1.0},
            "status": {"enum": CONTENT_STATUSES},
            **EMBEDDING_PROPERTIES,
        },
    ),
    "content_briefs": content_document_schema(
        [
            "profile_id",
            "brief_id",
            "idea_id",
            "platform",
            "objective",
            "audience",
            "primary_message",
            "supporting_points",
            "source_refs",
            "research_project_id",
            "claim_refs",
            "citation_refs",
            "factual_review_status",
            "skills",
            "avoid_repetition_with",
            "tags",
            "status",
            *EMBEDDING_REQUIRED,
        ],
        {
            "profile_id": IDENTIFIER,
            "brief_id": IDENTIFIER,
            "idea_id": IDENTIFIER,
            "platform": {"enum": CONTENT_PLATFORMS},
            "objective": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
            "audience": PREFERENCE_LIST,
            "primary_message": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
            "supporting_points": {
                "bsonType": "array",
                "maxItems": 50,
                "uniqueItems": True,
                "items": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
            },
            "source_refs": CONTENT_SOURCE_REFERENCES,
            "research_project_id": {"bsonType": ["string", "null"]},
            "claim_refs": {
                "bsonType": "array",
                "maxItems": 200,
                "items": RESEARCH_BRIEF_CLAIM_REFERENCE,
            },
            "citation_refs": {
                "bsonType": "array",
                "maxItems": 200,
                "items": RESEARCH_BRIEF_CITATION_REFERENCE,
            },
            "factual_review_status": {"enum": CONTENT_FACTUAL_REVIEW_STATUSES},
            "skills": IDENTIFIER_LIST,
            "avoid_repetition_with": IDENTIFIER_LIST,
            "tags": TAGS,
            "status": {"enum": CONTENT_BRIEF_STATUSES},
            **EMBEDDING_PROPERTIES,
        },
    ),
    "content_items": content_document_schema(
        [
            "profile_id",
            "content_id",
            "brief_id",
            "platform",
            "content_type",
            "title",
            "body",
            "status",
            "tags",
            "topic_tags",
            "pillar_id",
            "project_ids",
            "source_refs",
            "current_version",
            "published_at",
            "external_url",
            "publication_provider",
            "external_id",
            "publication_idempotency_key",
            *EMBEDDING_REQUIRED,
        ],
        {
            "profile_id": IDENTIFIER,
            "content_id": IDENTIFIER,
            "brief_id": {"bsonType": ["string", "null"]},
            "platform": {"enum": CONTENT_PLATFORMS},
            "content_type": IDENTIFIER,
            "title": {"bsonType": "string", "minLength": 1, "maxLength": 500},
            "body": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
            "status": {"enum": CONTENT_STATUSES},
            "tags": TAGS,
            "topic_tags": IDENTIFIER_LIST,
            "pillar_id": {"bsonType": ["string", "null"]},
            "project_ids": IDENTIFIER_LIST,
            "source_refs": CONTENT_SOURCE_REFERENCES,
            "current_version": {"bsonType": ["int", "long"], "minimum": 1},
            "published_at": {"bsonType": ["date", "null"]},
            "external_url": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 2_048},
            "publication_provider": {"enum": [*CONTENT_PUBLICATION_PROVIDERS, None]},
            "external_id": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 1_024},
            "publication_idempotency_key": {
                "bsonType": ["string", "null"],
                "minLength": 1,
                "maxLength": 128,
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$",
            },
            **EMBEDDING_PROPERTIES,
        },
    ),
    "content_versions": content_document_schema(
        [
            "profile_id",
            "content_id",
            "version",
            "title",
            "body",
            "status",
            "change_reason",
            "created_by",
            "tags",
        ],
        {
            "profile_id": IDENTIFIER,
            "content_id": IDENTIFIER,
            "version": {"bsonType": ["int", "long"], "minimum": 1},
            "title": {"bsonType": "string", "minLength": 1, "maxLength": 500},
            "body": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
            "status": {"enum": CONTENT_STATUSES},
            "change_reason": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 500},
            "created_by": {"bsonType": ["string", "null"]},
            "tags": TAGS,
        },
    ),
    "content_links": content_document_schema(
        [
            "profile_id",
            "link_id",
            "content_id",
            "source_type",
            "source_id",
            "project_id",
            "relation",
            "tags",
            "status",
        ],
        {
            "profile_id": IDENTIFIER,
            "link_id": IDENTIFIER,
            "content_id": IDENTIFIER,
            "source_type": IDENTIFIER,
            "source_id": IDENTIFIER,
            "project_id": {"bsonType": ["string", "null"]},
            "relation": {"enum": CONTENT_RELATIONS},
            "tags": TAGS,
            "status": {"enum": CONTENT_LINK_STATUSES},
        },
    ),
    "content_metrics": content_document_schema(
        [
            "profile_id",
            "metric_id",
            "content_id",
            "platform",
            "captured_at",
            "metrics",
            "source",
            "provider_metadata",
            "tags",
            "status",
        ],
        {
            "profile_id": IDENTIFIER,
            "metric_id": IDENTIFIER,
            "content_id": IDENTIFIER,
            "platform": {"enum": CONTENT_PLATFORMS},
            "captured_at": DATE,
            "metrics": {
                "bsonType": "object",
                "minProperties": 1,
                "additionalProperties": {"bsonType": ["double", "int", "long"], "minimum": 0},
            },
            "source": IDENTIFIER,
            "provider_metadata": METADATA,
            "tags": TAGS,
            "status": {"enum": CONTENT_METRIC_STATUSES},
        },
    ),
    "content_campaigns": content_document_schema(
        [
            "profile_id",
            "campaign_id",
            "name",
            "description",
            "content_ids",
            "platforms",
            "tags",
            "status",
        ],
        {
            "profile_id": IDENTIFIER,
            "campaign_id": IDENTIFIER,
            "name": {"bsonType": "string", "minLength": 1, "maxLength": 500},
            "description": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
            "content_ids": IDENTIFIER_LIST,
            "platforms": OPTIONAL_PLATFORM_LIST,
            "tags": TAGS,
            "status": {"enum": CONTENT_CAMPAIGN_STATUSES},
        },
    ),
}

COLLECTION_VALIDATORS.update(RESEARCH_COLLECTION_VALIDATORS)
COLLECTION_VALIDATORS.update(AGENT_COLLECTION_VALIDATORS)
COLLECTION_VALIDATORS.update(IDEMPOTENCY_COLLECTION_VALIDATORS)
