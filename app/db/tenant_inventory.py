"""Reviewed ownership, natural identities and structural reference closure."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Final

from bson import ObjectId

from app.db.client_types import MongoDocument
from app.models import agent, content, research
from app.models.api_key import ApiKeyDocument
from app.models.audit_log import AuditLogDocument
from app.models.client_profile import ClientProfileDocument
from app.models.common import MongoDocumentModel
from app.models.conversation import ConversationDocument
from app.models.idempotency import IdempotencyRecordDocument
from app.models.memory import MemoryDocument
from app.models.project import ProjectDocument
from app.models.project_context import ProjectContextDocument
from app.models.retrieval_log import RetrievalLogDocument
from app.models.skill import SkillDocument
from app.models.skill_chunk import SkillChunkDocument

MODEL_BY_COLLECTION: Final[dict[str, type[MongoDocumentModel]]] = {
    "skills": SkillDocument,
    "skill_chunks": SkillChunkDocument,
    "projects": ProjectDocument,
    "project_context": ProjectContextDocument,
    "memories": MemoryDocument,
    "conversations": ConversationDocument,
    "retrieval_logs": RetrievalLogDocument,
    "api_keys": ApiKeyDocument,
    "audit_logs": AuditLogDocument,
    "client_profiles": ClientProfileDocument,
    "gateway_idempotency": IdempotencyRecordDocument,
    **{
        name: getattr(content, model)
        for name, model in (
            ("creator_profiles", "CreatorProfileDocument"),
            ("content_pillars", "ContentPillarDocument"),
            ("content_ideas", "ContentIdeaDocument"),
            ("content_briefs", "ContentBriefDocument"),
            ("content_items", "ContentItemDocument"),
            ("content_versions", "ContentVersionDocument"),
            ("content_links", "ContentLinkDocument"),
            ("content_metrics", "ContentMetricDocument"),
            ("content_campaigns", "ContentCampaignDocument"),
        )
    },
    **{
        name: getattr(research, model)
        for name, model in (
            ("research_projects", "ResearchProjectDocument"),
            ("research_sources", "ResearchSourceDocument"),
            ("source_snapshots", "SourceSnapshotDocument"),
            ("research_documents", "ResearchDocumentDocument"),
            ("research_chunks", "ResearchChunkDocument"),
            ("research_notes", "ResearchNoteDocument"),
            ("research_claims", "ResearchClaimDocument"),
            ("research_citations", "ResearchCitationDocument"),
            ("research_briefs", "ResearchBriefDocument"),
            ("knowledge_candidates", "KnowledgeCandidateDocument"),
            ("skill_update_proposals", "SkillUpdateProposalDocument"),
            ("promotion_events", "PromotionEventDocument"),
            ("ingestion_jobs", "IngestionJobDocument"),
        )
    },
    **{
        name: getattr(agent, model)
        for name, model in (
            ("agent_clients", "AgentClientDocument"),
            ("agent_sessions", "AgentSessionDocument"),
            ("project_bindings", "ProjectBindingDocument"),
            ("agent_activity", "AgentActivityDocument"),
        )
    },
}

IDENTITY_FIELDS: Final = {
    "project": ("project_id",),
    "skill": ("skill_id", "version"),
    "skill_chunk": ("skill_id", "skill_version", "chunk_id"),
    "project_context": ("project_id", "external_id"),
    "agent-client": ("client_id",),
    "project-binding": ("binding_id",),
    "client-profile": ("profile_id",),
    "creator_profile": ("profile_id",),
    **{
        f"content_{kind}": ("profile_id", f"{kind if kind != 'item' else 'content'}_id")
        for kind in ("pillar", "idea", "brief", "item", "link", "metric", "campaign")
    },
    "content_version": ("profile_id", "content_id", "version"),
    "research_project": ("research_project_id",),
    "research_source": ("source_id",),
    "source_snapshot": ("source_id", "snapshot_id"),
    "research_document": ("source_id", "snapshot_id", "document_id"),
    "research_chunk": ("document_id", "chunk_id"),
    "research_note": ("research_project_id", "note_id"),
    "research_claim": ("research_project_id", "claim_id"),
    "research_citation": ("claim_id", "citation_id"),
    "research_brief": ("research_project_id", "brief_id"),
    "knowledge_candidate": ("research_project_id", "candidate_id"),
    "skill_update_proposal": ("research_project_id", "proposal_id"),
    "promotion_event": ("candidate_id", "event_id"),
    "ingestion_job": ("job_id",),
}


@dataclass(frozen=True)
class Reference:
    target: str
    query: MongoDocument
    path: str


# Descriptive/diagnostic identifiers are explicitly NOT live foreign keys. They
# may name refused attempts, retired actors or external correlation labels.
HISTORICAL_COLLECTIONS: Final = {"audit_logs", "retrieval_logs", "agent_activity"}
DESCRIPTIVE_IDS: Final = {
    "owner_id",
    "owner_key_id",
    "created_by",
    "reviewer",
    "request_id",
    "query_id",
    "correlation_id",
    "selected_result_ids",
    "key_id",
    "event_id",
    "job_id",
    "binding_id",
}


def references(collection: str, document: Mapping[str, object]) -> Iterator[Reference]:
    """Resolve declared links without guessing identity from text or actor labels."""
    if collection in HISTORICAL_COLLECTIONS:
        return  # Immutable event metadata can legitimately refer to missing/refused targets.

    def walk(
        value: object, path: str = "", root_project: object = None, root_profile: object = None
    ) -> Iterator[Reference]:
        if isinstance(value, list):
            for position, child in enumerate(value):
                yield from walk(child, f"{path}[{position}]", root_project, root_profile)
            return
        if not isinstance(value, Mapping):
            return
        root_project = value.get("research_project_id", root_project)
        root_profile = value.get("profile_id", root_profile)
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            field = f"{path}.{key}" if path else key
            if item is None or key in DESCRIPTIVE_IDS or key in {"org_id", "legacy_adoption_id"}:
                continue
            if key in {"supersedes", "superseded_by"} and isinstance(item, ObjectId):
                yield Reference(
                    "memories",
                    {
                        "_id": item,
                        "scope": document.get("scope"),
                        "project_id": document.get("project_id"),
                        "client_id": document.get("client_id"),
                    },
                    field,
                )
                continue
            if isinstance(item, Mapping | list):
                # Arbitrary metadata/replay bodies are inert payloads, not declared links.
                if key not in {"metadata", "provider_metadata", "result_data", "metrics", "actor"}:
                    yield from walk(item, field, root_project, root_profile)
                # Lists of declared resource IDs are handled below too.
            singular = key.removesuffix("s") if key.endswith("_ids") else key
            if collection == "api_keys":
                singular = {"projects": "project_id", "profiles": "profile_id"}.get(key, singular)
            if collection.startswith("content") and singular == "source_id" and value.get("type"):
                # ContentOrigin uses `type`; ContentSourceReference uses `source_type`.
                value = {**value, "source_type": value["type"]}
            if singular in {
                "evidence_citation_id",
                "supporting_citation_id",
                "contradicting_citation_id",
            }:
                singular = "source_citation_id"
            ids = item if isinstance(item, list) else [item]
            for identifier in ids:
                if not isinstance(identifier, str):
                    continue
                query: MongoDocument = {}
                target: str | None = None
                if singular in {"project_id", "related_project_id", "target_project_id"}:
                    if collection != "projects" or path:
                        target, query = "projects", {"project_id": identifier}
                elif singular == "profile_id":
                    if collection not in {"creator_profiles", "client_profiles"} or path:
                        target = (
                            "client_profiles"
                            if collection == "agent_clients"
                            else "creator_profiles"
                        )
                        query = {"profile_id": identifier}
                elif singular == "client_id":
                    if collection != "agent_clients" or path:
                        target, query = "agent_clients", {"client_id": identifier}
                elif singular == "research_project_id":
                    if collection != "research_projects" or path:
                        target, query = "research_projects", {"research_project_id": identifier}
                elif singular in {"skill_id", "target_skill_id"}:
                    if collection != "skills" or path:
                        target, query = "skills", {"skill_id": identifier}
                        version = value.get(
                            "skill_version",
                            value.get("current_version", value.get("current_skill_version")),
                        )
                        query["version" if version else "status"] = version or "active"
                elif singular in {"content_id", "idea_id", "pillar_id", "brief_id"}:
                    kind = singular.removesuffix("_id")
                    target = {
                        "content": "content_items",
                        "idea": "content_ideas",
                        "pillar": "content_pillars",
                        "brief": "content_briefs",
                    }[kind]
                    if collection == target and not path:
                        target = None
                    elif collection.startswith("research"):
                        target = None  # A research brief's primary ID is a different namespace.
                    else:
                        query = {singular: identifier, "profile_id": root_profile}
                elif singular in {"claim_id", "source_claim_id"}:
                    if collection != "research_claims" or path:
                        target, query = "research_claims", {"claim_id": identifier}
                elif singular in {
                    "citation_id",
                    "source_citation_id",
                    "supporting_citation_id",
                    "contradicting_citation_id",
                }:
                    if collection != "research_citations" or path:
                        target, query = "research_citations", {"citation_id": identifier}
                        if value.get("claim_id"):
                            query["claim_id"] = value["claim_id"]
                        elif document.get("source_claim_ids"):
                            query["claim_id"] = {"$in": document["source_claim_ids"]}
                elif singular in {"candidate_id", "revalidation_of_candidate_id"}:
                    if (
                        collection != "knowledge_candidates"
                        or path
                        or singular.startswith("revalidation")
                    ):
                        target, query = "knowledge_candidates", {"candidate_id": identifier}
                elif singular in {
                    "source_id",
                    "snapshot_id",
                    "source_snapshot_id",
                    "document_id",
                    "chunk_id",
                }:
                    own = {
                        "skill_chunks": "chunk_id",
                        "research_sources": "source_id",
                        "source_snapshots": "snapshot_id",
                        "research_documents": "document_id",
                        "research_chunks": "chunk_id",
                    }
                    if own.get(collection) == singular and not path:
                        continue
                    if singular == "source_id" and value.get("source_type"):
                        source_type = value["source_type"]
                        dynamic = {
                            "project_context": "project_context",
                            "memory": "memories",
                            "agent_memory": "memories",
                            "project_memory": "memories",
                            "global_memory": "memories",
                            "skill": "skills",
                            "active_skill": "skills",
                            "content_item": "content_items",
                            "research_project": "research_projects",
                        }
                        if source_type in dynamic:
                            target = dynamic[source_type]
                            if target == "skills":
                                query = {"skill_id": identifier, "status": "active"}
                            elif target == "research_projects":
                                query = {"research_project_id": identifier}
                            elif target == "content_items":
                                query = {"content_id": identifier, "profile_id": root_profile}
                            elif target == "project_context" and not ObjectId.is_valid(identifier):
                                query = {
                                    "external_id": identifier,
                                    "project_id": value.get("project_id"),
                                }
                            else:
                                query = {
                                    "_id": ObjectId(identifier)
                                    if ObjectId.is_valid(identifier)
                                    else identifier
                                }
                        elif collection.startswith("content"):
                            # An undeclared source vocabulary is unresolved, never
                            # guessed to be a research source by its spelling.
                            target, query = "_unclassified_source", {"_id": identifier}
                    if target is None:
                        target = {
                            "source_id": "research_sources",
                            "snapshot_id": "source_snapshots",
                            "source_snapshot_id": "source_snapshots",
                            "document_id": "research_documents",
                            "chunk_id": "research_chunks",
                        }[singular]
                        query = {
                            "snapshot_id"
                            if singular == "source_snapshot_id"
                            else singular: identifier
                        }
                        dimensions = {
                            "source_snapshots": ("source_id",),
                            "research_documents": ("source_id", "snapshot_id"),
                            "research_chunks": ("source_id", "snapshot_id", "document_id"),
                        }.get(target, ())
                        for dimension in dimensions:
                            if (
                                dimension != singular
                                and value.get(dimension)
                                and target != "research_sources"
                            ):
                                query[dimension] = value[dimension]
                elif singular == "session_id" and collection == "agent_sessions":
                    continue  # Own primary identity; conversation session IDs are external labels.
                elif singular == "target_id" and collection == "promotion_events":
                    target = str(document.get("target_type"))
                    target = (
                        "memories"
                        if target == "memory"
                        else "project_context"
                        if target in {"project_context", "architecture_decision"}
                        else None
                    )
                    query = {
                        "_id": ObjectId(identifier) if ObjectId.is_valid(identifier) else identifier
                    }
                if target is not None:
                    if (
                        target
                        in {
                            "research_sources",
                            "source_snapshots",
                            "research_documents",
                            "research_chunks",
                            "research_claims",
                            "research_citations",
                            "knowledge_candidates",
                        }
                        and root_project
                    ):
                        query.setdefault("research_project_id", root_project)
                    yield Reference(target, query, field)

    yield from walk(document)
