"""Strict MongoDB validators introduced by V3 Phase 0 only.

Kept separate from the V1/V2 mapping so the additive V3 schema is easy to
audit.  ``app.db.schema`` imports this mapping only after its shared validator
primitives have been defined.
"""

from typing import Final

from app.db.client_types import MongoDocument
from app.db.provenance_schema import ACTOR_PROVENANCE
from app.embeddings.types import MAX_EMBEDDING_DIMENSIONS, MAX_EMBEDDING_MODEL_NAME_LENGTH
from app.models.freshness import FreshnessClass, RevalidationReason
from app.models.research import (
    CandidateState,
    CandidateType,
    CitationRelation,
    ClaimState,
    DocumentContentStorage,
    IngestionJobState,
    ResearchBriefStatus,
    ResearchNoteStatus,
    ResearchProjectStatus,
    ResearchProjectType,
    ResearchRecordStatus,
    ResearchSourceType,
    SkillUpdateProposalStatus,
    SourceClass,
)

IDENTIFIER: Final[MongoDocument] = {
    "bsonType": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$",
}
DATE: Final[MongoDocument] = {"bsonType": "date"}
TAGS: Final[MongoDocument] = {
    "bsonType": "array",
    "maxItems": 50,
    "uniqueItems": True,
    "items": {"bsonType": "string", "minLength": 1, "maxLength": 64},
}
METADATA: Final[MongoDocument] = {"bsonType": "object"}
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
RESEARCH_PROJECT_TYPES: Final[list[str]] = [value.value for value in ResearchProjectType]
SOURCE_CLASSES: Final[list[str]] = [value.value for value in SourceClass]
RESEARCH_SOURCE_TYPES: Final[list[str]] = [value.value for value in ResearchSourceType]
RESEARCH_PROJECT_STATUSES: Final[list[str]] = [value.value for value in ResearchProjectStatus]
RESEARCH_RECORD_STATUSES: Final[list[str]] = [value.value for value in ResearchRecordStatus]
RESEARCH_NOTE_STATUSES: Final[list[str]] = [value.value for value in ResearchNoteStatus]
CLAIM_STATES: Final[list[str]] = [value.value for value in ClaimState]
CITATION_RELATIONS: Final[list[str]] = [value.value for value in CitationRelation]
RESEARCH_BRIEF_STATUSES: Final[list[str]] = [value.value for value in ResearchBriefStatus]
CANDIDATE_STATES: Final[list[str]] = [value.value for value in CandidateState]
CANDIDATE_TYPES: Final[list[str]] = [value.value for value in CandidateType]
FRESHNESS_CLASSES: Final[list[str]] = [value.value for value in FreshnessClass]
REVALIDATION_REASONS: Final[list[str]] = [value.value for value in RevalidationReason]
SKILL_UPDATE_PROPOSAL_STATUSES: Final[list[str]] = [
    value.value for value in SkillUpdateProposalStatus
]
INGESTION_JOB_STATES: Final[list[str]] = [value.value for value in IngestionJobState]
DOCUMENT_CONTENT_STORAGE: Final[list[str]] = [value.value for value in DocumentContentStorage]
SEMANTIC_VERSION_PATTERN: Final[str] = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
RESEARCH_AUTHORIZATION: Final[MongoDocument] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["project_ids", "owner_key_id"],
    "properties": {
        "project_ids": {
            "bsonType": "array",
            "maxItems": 100,
            "uniqueItems": True,
            "items": IDENTIFIER,
        },
        "owner_key_id": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 128},
    },
}
RESEARCH_SOURCE_REFERENCE: Final[MongoDocument] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["source_id", "snapshot_id", "document_id", "chunk_id"],
    "properties": {
        "source_id": IDENTIFIER,
        "snapshot_id": {"bsonType": ["string", "null"]},
        "document_id": {"bsonType": ["string", "null"]},
        "chunk_id": {"bsonType": ["string", "null"]},
    },
}
RESEARCH_SOURCE_REFERENCES: Final[MongoDocument] = {
    "bsonType": "array",
    "maxItems": 100,
    "items": RESEARCH_SOURCE_REFERENCE,
}
CITATION_LOCATOR: Final[MongoDocument] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["url", "anchor", "path", "page", "line_start", "line_end", "commit"],
    "properties": {
        "url": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 2_048},
        "anchor": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 1_024},
        "path": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 2_048},
        "page": {"bsonType": ["int", "long", "null"], "minimum": 1},
        "line_start": {"bsonType": ["int", "long", "null"], "minimum": 1},
        "line_end": {"bsonType": ["int", "long", "null"], "minimum": 1},
        "commit": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 256},
    },
}
RESEARCH_BRIEF_CLAIM_REFERENCE: Final[MongoDocument] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": ["claim_id", "statement", "status", "confidence"],
    "properties": {
        "claim_id": IDENTIFIER,
        "statement": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
        "status": {"enum": CLAIM_STATES},
        "confidence": {"bsonType": ["double", "null"], "minimum": 0.0, "maximum": 1.0},
    },
}
RESEARCH_BRIEF_CITATION_REFERENCE: Final[MongoDocument] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": [
        "citation_id",
        "claim_id",
        "source_id",
        "snapshot_id",
        "chunk_id",
        "source_class",
        "locator",
        "evidence_excerpt",
        "relation",
    ],
    "properties": {
        "citation_id": IDENTIFIER,
        "claim_id": IDENTIFIER,
        "source_id": IDENTIFIER,
        "snapshot_id": IDENTIFIER,
        "chunk_id": {"bsonType": ["string", "null"]},
        "source_class": {"enum": SOURCE_CLASSES},
        "locator": CITATION_LOCATOR,
        "evidence_excerpt": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
        "relation": {"enum": CITATION_RELATIONS},
    },
}
RESEARCH_BRIEF_CONTRADICTION: Final[MongoDocument] = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": [
        "claim_id",
        "statement",
        "supporting_citation_ids",
        "contradicting_citation_ids",
    ],
    "properties": {
        "claim_id": IDENTIFIER,
        "statement": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
        "supporting_citation_ids": {
            "bsonType": "array",
            "maxItems": 200,
            "uniqueItems": True,
            "items": IDENTIFIER,
        },
        "contradicting_citation_ids": {
            "bsonType": "array",
            "maxItems": 200,
            "uniqueItems": True,
            "items": IDENTIFIER,
        },
    },
}
RESEARCH_BRIEF_SOURCE_REFERENCES: Final[MongoDocument] = {
    "bsonType": "array",
    "maxItems": 200,
    "items": RESEARCH_SOURCE_REFERENCE,
}

RESEARCH_TEXT_LIST: Final[MongoDocument] = {
    "bsonType": "array",
    "maxItems": 100,
    "uniqueItems": True,
    "items": {"bsonType": "string", "minLength": 1, "maxLength": 200_000},
}


def document_schema(required_fields: list[str], properties: MongoDocument) -> MongoDocument:
    return {
        "$jsonSchema": {
            "bsonType": "object",
            "additionalProperties": False,
            "required": ["_id", "created_at", "updated_at", "revision", "actor", *required_fields],
            "properties": {
                "_id": {"bsonType": "objectId"},
                "created_at": DATE,
                "updated_at": DATE,
                "revision": {"bsonType": ["int", "long"], "minimum": 1},
                "actor": ACTOR_PROVENANCE,
                **properties,
            },
        }
    }


def _identifier_list(max_items: int) -> MongoDocument:
    return {
        "bsonType": "array",
        "maxItems": max_items,
        "uniqueItems": True,
        "items": IDENTIFIER,
    }


def _scoped(properties: MongoDocument) -> MongoDocument:
    return {
        "research_project_id": IDENTIFIER,
        "authorization": RESEARCH_AUTHORIZATION,
        **properties,
    }


_RECORD_STATUS: Final[MongoDocument] = {"enum": RESEARCH_RECORD_STATUSES}
_SOURCE_CLASS: Final[MongoDocument] = {"enum": SOURCE_CLASSES}
_CONTENT: Final[MongoDocument] = {"bsonType": "string", "minLength": 1, "maxLength": 200_000}
_SHORT_TEXT: Final[MongoDocument] = {"bsonType": "string", "minLength": 1, "maxLength": 500}
_OPTIONAL_IDENTIFIER: Final[MongoDocument] = {"bsonType": ["string", "null"]}
_OPTIONAL_DATE: Final[MongoDocument] = {"bsonType": ["date", "null"]}


RESEARCH_COLLECTION_VALIDATORS: Final[dict[str, MongoDocument]] = {
    "research_projects": document_schema(
        [
            "research_project_id",
            "name",
            "question",
            "type",
            "related_project_ids",
            "authorization",
            "tags",
            "status",
        ],
        _scoped(
            {
                "name": _SHORT_TEXT,
                "question": _CONTENT,
                "type": {"enum": RESEARCH_PROJECT_TYPES},
                "related_project_ids": _identifier_list(100),
                "tags": TAGS,
                "status": {"enum": RESEARCH_PROJECT_STATUSES},
            }
        ),
    ),
    "research_sources": document_schema(
        [
            "research_project_id",
            "source_id",
            "source_type",
            "uri",
            "canonical_uri",
            "title",
            "publisher",
            "author",
            "published_at",
            "retrieved_at",
            "valid_from",
            "valid_until",
            "source_class",
            "trust_metadata",
            "authorization",
            "tags",
            "status",
        ],
        _scoped(
            {
                "source_id": IDENTIFIER,
                "source_type": {"enum": RESEARCH_SOURCE_TYPES},
                "uri": {"bsonType": "string", "minLength": 1, "maxLength": 2_048},
                "canonical_uri": {
                    "bsonType": ["string", "null"],
                    "minLength": 1,
                    "maxLength": 2_048,
                },
                "title": _SHORT_TEXT,
                "publisher": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 500},
                "author": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 500},
                "published_at": _OPTIONAL_DATE,
                "retrieved_at": _OPTIONAL_DATE,
                "valid_from": _OPTIONAL_DATE,
                "valid_until": _OPTIONAL_DATE,
                "source_class": _SOURCE_CLASS,
                "trust_metadata": METADATA,
                "tags": TAGS,
                "status": _RECORD_STATUS,
            }
        ),
    ),
    "source_snapshots": document_schema(
        [
            "research_project_id",
            "source_id",
            "snapshot_id",
            "retrieved_at",
            "content_hash",
            "version_identifier",
            "metadata",
            "authorization",
            "status",
        ],
        _scoped(
            {
                "source_id": IDENTIFIER,
                "snapshot_id": IDENTIFIER,
                "retrieved_at": DATE,
                "content_hash": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
                "version_identifier": {
                    "bsonType": ["string", "null"],
                    "minLength": 1,
                    "maxLength": 2_048,
                },
                "metadata": METADATA,
                "status": _RECORD_STATUS,
            }
        ),
    ),
    "research_documents": document_schema(
        [
            "research_project_id",
            "source_id",
            "snapshot_id",
            "document_id",
            "title",
            "content_type",
            "language",
            "content",
            "content_storage",
            "content_hash",
            "source_class",
            "authorization",
            "tags",
            "status",
        ],
        _scoped(
            {
                "source_id": IDENTIFIER,
                "snapshot_id": IDENTIFIER,
                "document_id": IDENTIFIER,
                "title": _SHORT_TEXT,
                "content_type": {"bsonType": "string", "minLength": 1, "maxLength": 255},
                "language": {"bsonType": "string", "minLength": 1, "maxLength": 64},
                "content": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 200_000},
                "content_storage": {"enum": DOCUMENT_CONTENT_STORAGE},
                "content_hash": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
                "source_class": _SOURCE_CLASS,
                "tags": TAGS,
                "status": _RECORD_STATUS,
            }
        ),
    ),
    "research_chunks": document_schema(
        [
            "research_project_id",
            "source_id",
            "snapshot_id",
            "document_id",
            "chunk_id",
            "chunk_index",
            "section",
            "content",
            "source_class",
            "locator",
            "metadata",
            "authorization",
            "tags",
            "status",
            *EMBEDDING_REQUIRED,
        ],
        _scoped(
            {
                "source_id": IDENTIFIER,
                "snapshot_id": IDENTIFIER,
                "document_id": IDENTIFIER,
                "chunk_id": IDENTIFIER,
                "chunk_index": {"bsonType": ["int", "long"], "minimum": 0},
                "section": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 500},
                "content": _CONTENT,
                "source_class": _SOURCE_CLASS,
                "locator": CITATION_LOCATOR,
                "metadata": METADATA,
                "tags": TAGS,
                "status": _RECORD_STATUS,
                **EMBEDDING_PROPERTIES,
            }
        ),
    ),
    "research_notes": document_schema(
        [
            "research_project_id",
            "note_id",
            "title",
            "body",
            "source_refs",
            "created_by",
            "authorization",
            "tags",
            "status",
            *EMBEDDING_REQUIRED,
        ],
        _scoped(
            {
                "note_id": IDENTIFIER,
                "title": _SHORT_TEXT,
                "body": _CONTENT,
                "source_refs": RESEARCH_SOURCE_REFERENCES,
                "created_by": _OPTIONAL_IDENTIFIER,
                "tags": TAGS,
                "status": {"enum": RESEARCH_NOTE_STATUSES},
                **EMBEDDING_PROPERTIES,
            }
        ),
    ),
    "research_claims": document_schema(
        [
            "research_project_id",
            "claim_id",
            "statement",
            "status",
            "confidence",
            "confidence_rationale",
            "supporting_citation_ids",
            "contradicting_citation_ids",
            "authorization",
            "tags",
            *EMBEDDING_REQUIRED,
        ],
        _scoped(
            {
                "claim_id": IDENTIFIER,
                "statement": _CONTENT,
                "status": {"enum": CLAIM_STATES},
                "confidence": {"bsonType": ["double", "null"], "minimum": 0.0, "maximum": 1.0},
                "confidence_rationale": {
                    "bsonType": ["string", "null"],
                    "minLength": 1,
                    "maxLength": 4_000,
                },
                "supporting_citation_ids": _identifier_list(100),
                "contradicting_citation_ids": _identifier_list(100),
                "tags": TAGS,
                **EMBEDDING_PROPERTIES,
            }
        ),
    ),
    "research_citations": document_schema(
        [
            "research_project_id",
            "claim_id",
            "citation_id",
            "source_id",
            "snapshot_id",
            "chunk_id",
            "source_class",
            "locator",
            "evidence_excerpt",
            "relation",
            "authorization",
            "status",
        ],
        _scoped(
            {
                "claim_id": IDENTIFIER,
                "citation_id": IDENTIFIER,
                "source_id": IDENTIFIER,
                "snapshot_id": IDENTIFIER,
                "chunk_id": _OPTIONAL_IDENTIFIER,
                "source_class": _SOURCE_CLASS,
                "locator": CITATION_LOCATOR,
                "evidence_excerpt": _CONTENT,
                "relation": {"enum": CITATION_RELATIONS},
                "status": _RECORD_STATUS,
            }
        ),
    ),
    "research_briefs": document_schema(
        [
            "research_project_id",
            "brief_id",
            "title",
            "question",
            "scope",
            "summary",
            "key_findings",
            "evidence_citation_ids",
            "contradicting_citation_ids",
            "source_ids",
            "claim_refs",
            "citation_refs",
            "contradictions",
            "source_refs",
            "unknowns",
            "next_questions",
            "authorization",
            "tags",
            "status",
            *EMBEDDING_REQUIRED,
        ],
        _scoped(
            {
                "brief_id": IDENTIFIER,
                "title": _SHORT_TEXT,
                "question": _CONTENT,
                "scope": _CONTENT,
                "summary": _CONTENT,
                "key_findings": RESEARCH_TEXT_LIST,
                "evidence_citation_ids": _identifier_list(200),
                "contradicting_citation_ids": _identifier_list(200),
                "source_ids": _identifier_list(200),
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
                "contradictions": {
                    "bsonType": "array",
                    "maxItems": 200,
                    "items": RESEARCH_BRIEF_CONTRADICTION,
                },
                "source_refs": RESEARCH_BRIEF_SOURCE_REFERENCES,
                "unknowns": RESEARCH_TEXT_LIST,
                "next_questions": RESEARCH_TEXT_LIST,
                "tags": TAGS,
                "status": {"enum": RESEARCH_BRIEF_STATUSES},
                **EMBEDDING_PROPERTIES,
            }
        ),
    ),
    "knowledge_candidates": document_schema(
        [
            "research_project_id",
            "candidate_id",
            "candidate_type",
            "target_project_id",
            "target_skill_id",
            "current_skill_version",
            "title",
            "content",
            "source_claim_ids",
            "source_citation_ids",
            "confidence",
            "confidence_rationale",
            "freshness_class",
            "review_after",
            "revalidation_of_candidate_id",
            "revalidation_reasons",
            "status",
            "reviewer",
            "reviewed_at",
            "review_notes",
            "authorization",
            "tags",
            *EMBEDDING_REQUIRED,
        ],
        _scoped(
            {
                "candidate_id": IDENTIFIER,
                "candidate_type": {"enum": CANDIDATE_TYPES},
                "target_project_id": _OPTIONAL_IDENTIFIER,
                "target_skill_id": _OPTIONAL_IDENTIFIER,
                "current_skill_version": {
                    "bsonType": ["string", "null"],
                    "minLength": 1,
                    "maxLength": 64,
                },
                "title": _SHORT_TEXT,
                "content": _CONTENT,
                "source_claim_ids": _identifier_list(100),
                "source_citation_ids": _identifier_list(200),
                "confidence": {"bsonType": ["double", "null"], "minimum": 0.0, "maximum": 1.0},
                "confidence_rationale": {
                    "bsonType": ["string", "null"],
                    "minLength": 1,
                    "maxLength": 4_000,
                },
                "freshness_class": {"enum": FRESHNESS_CLASSES},
                "review_after": _OPTIONAL_DATE,
                "revalidation_of_candidate_id": _OPTIONAL_IDENTIFIER,
                "revalidation_reasons": {
                    "bsonType": "array",
                    "maxItems": 20,
                    "uniqueItems": True,
                    "items": {"enum": REVALIDATION_REASONS},
                },
                "status": {"enum": CANDIDATE_STATES},
                "reviewer": _OPTIONAL_IDENTIFIER,
                "reviewed_at": _OPTIONAL_DATE,
                "review_notes": {
                    "bsonType": ["string", "null"],
                    "minLength": 1,
                    "maxLength": 4_000,
                },
                "tags": TAGS,
                **EMBEDDING_PROPERTIES,
            }
        ),
    ),
    "skill_update_proposals": document_schema(
        [
            "research_project_id",
            "proposal_id",
            "skill_id",
            "current_version",
            "proposed_change",
            "proposed_patch",
            "source_claim_ids",
            "source_citation_ids",
            "reason",
            "status",
            "trust_level",
            "authorization",
        ],
        _scoped(
            {
                "proposal_id": IDENTIFIER,
                "skill_id": IDENTIFIER,
                "current_version": {
                    "bsonType": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "pattern": SEMANTIC_VERSION_PATTERN,
                },
                "proposed_change": _CONTENT,
                "proposed_patch": _CONTENT,
                "source_claim_ids": _identifier_list(100),
                "source_citation_ids": _identifier_list(200),
                "reason": _CONTENT,
                "status": {"enum": SKILL_UPDATE_PROPOSAL_STATUSES},
                "trust_level": {"enum": ["generated"]},
            }
        ),
    ),
    "promotion_events": document_schema(
        [
            "research_project_id",
            "candidate_id",
            "event_id",
            "target_type",
            "target_id",
            "reviewer",
            "decision",
            "decided_at",
            "reason",
            "authorization",
        ],
        _scoped(
            {
                "candidate_id": IDENTIFIER,
                "event_id": IDENTIFIER,
                "target_type": {"enum": CANDIDATE_TYPES},
                "target_id": _OPTIONAL_IDENTIFIER,
                "reviewer": IDENTIFIER,
                "decision": {"enum": CANDIDATE_STATES},
                "decided_at": DATE,
                "reason": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 4_000},
            }
        ),
    ),
    "ingestion_jobs": document_schema(
        [
            "research_project_id",
            "job_id",
            "source_id",
            "snapshot_id",
            "status",
            "started_at",
            "completed_at",
            "error",
            "documents_created",
            "chunks_created",
            "authorization",
        ],
        _scoped(
            {
                "job_id": IDENTIFIER,
                "source_id": IDENTIFIER,
                "snapshot_id": _OPTIONAL_IDENTIFIER,
                "status": {"enum": INGESTION_JOB_STATES},
                "started_at": _OPTIONAL_DATE,
                "completed_at": _OPTIONAL_DATE,
                "error": {"bsonType": ["string", "null"], "minLength": 1, "maxLength": 4_000},
                "documents_created": {"bsonType": ["int", "long"], "minimum": 0},
                "chunks_created": {"bsonType": ["int", "long"], "minimum": 0},
            }
        ),
    ),
}
