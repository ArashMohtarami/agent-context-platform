"""Explicit pre-V6 fixture: every domain family and actual reference chains."""

from datetime import UTC, datetime, timedelta

from app.db.tenant_inventory import MODEL_BY_COLLECTION
from app.models.common import MongoDocumentModel
from app.models.content import (
    ContentBriefDocument,
    ContentCampaignDocument,
    ContentIdeaDocument,
    ContentItemDocument,
    ContentLinkDocument,
    ContentMetricDocument,
    ContentPillarDocument,
    ContentVersionDocument,
    CreatorProfileDocument,
)
from app.models.research import (
    CandidateState,
    CandidateType,
    CitationRelation,
    ClaimState,
    IngestionJobDocument,
    IngestionJobState,
    KnowledgeCandidateDocument,
    PromotionEventDocument,
    ResearchBriefDocument,
    ResearchChunkDocument,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchDocumentDocument,
    ResearchNoteDocument,
    ResearchProjectDocument,
    ResearchProjectType,
    ResearchSourceDocument,
    SkillUpdateProposalDocument,
    SourceClass,
    SourceSnapshotDocument,
)


def content_documents() -> tuple[object, ...]:
    """Build one valid record for every V2 collection."""
    return (
        CreatorProfileDocument(
            profile_id="personal-brand",
            name="Technical Personal Brand",
            audiences=["backend engineers"],
            domains=["distributed systems"],
            voice={"tone": ["direct"], "avoid": ["generic hype"]},
        ),
        ContentPillarDocument(
            profile_id="personal-brand",
            pillar_id="distributed-systems",
            name="Distributed Systems",
            description="Practical distributed-systems lessons.",
            priority=0.9,
        ),
        ContentIdeaDocument(
            profile_id="personal-brand",
            idea_id="payment-idempotency",
            title="Why payment APIs need idempotency",
            description="A project-grounded explanation of safe retries.",
            pillar_id="distributed-systems",
            platforms=["linkedin", "medium"],
            origin={
                "type": "project_context",
                "project_id": "payment-service",
                "source_ids": ["payment-idempotency-context"],
            },
            priority=0.8,
        ),
        ContentBriefDocument(
            profile_id="personal-brand",
            brief_id="payment-idempotency-linkedin",
            idea_id="payment-idempotency",
            platform="linkedin",
            objective="Explain safe payment retries through real implementation experience.",
            primary_message="Idempotency turns uncertain retries into a controlled API contract.",
            source_refs=[
                {
                    "source_type": "project_context",
                    "source_id": "payment-idempotency-context",
                    "project_id": "payment-service",
                }
            ],
        ),
        ContentItemDocument(
            profile_id="personal-brand",
            content_id="payment-idempotency-linkedin-post",
            brief_id="payment-idempotency-linkedin",
            platform="linkedin",
            content_type="post",
            title="Why payment APIs need idempotency",
            body="A current draft grounded in project context.",
            source_refs=[
                {
                    "source_type": "project_context",
                    "source_id": "payment-idempotency-context",
                    "project_id": "payment-service",
                }
            ],
            project_ids=["payment-service"],
        ),
        ContentVersionDocument(
            profile_id="personal-brand",
            content_id="payment-idempotency-linkedin-post",
            version=1,
            title="Why payment APIs need idempotency",
            body="The first preserved draft.",
            status="drafting",
        ),
        ContentLinkDocument(
            profile_id="personal-brand",
            link_id="idempotency-context-derived",
            content_id="payment-idempotency-linkedin-post",
            source_type="project_context",
            source_id="payment-idempotency-context",
            project_id="payment-service",
            relation="derived_from",
        ),
        ContentMetricDocument(
            profile_id="personal-brand",
            metric_id="payment-idempotency-linkedin-20260920",
            content_id="payment-idempotency-linkedin-post",
            platform="linkedin",
            captured_at=datetime(2026, 9, 20, tzinfo=UTC),
            metrics={"views": 100.0, "likes": 5.0},
        ),
        ContentCampaignDocument(
            profile_id="personal-brand",
            campaign_id="payment-reliability-series",
            name="Payment Reliability Series",
            description="A coordinated technical-content series about payment reliability.",
            content_ids=["payment-idempotency-linkedin-post"],
            platforms=["linkedin", "medium"],
        ),
    )


PROJECT = "payments-api"

RESEARCH_PROJECT = "payment-retry-research"

SOURCE = "provider-docs"

SNAPSHOT = "provider-docs-20260928"

DOCUMENT = "retry-guide"

HASH = "a" * 64

WHEN = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def authorization() -> dict[str, object]:
    return {"project_ids": [PROJECT], "owner_key_id": "research-key"}


def locator() -> dict[str, object]:
    return {"url": "https://docs.example.test/retries", "anchor": "retry-policy"}


def research_documents() -> tuple[object, ...]:
    """Build one valid strict document for every V3 collection."""
    return (
        ResearchProjectDocument(
            research_project_id=RESEARCH_PROJECT,
            name="Payment retry evidence",
            question="What do the provider's current retry guarantees require?",
            type=ResearchProjectType.TECHNICAL_RESEARCH,
            related_project_ids=[PROJECT],
            authorization=authorization(),
        ),
        ResearchSourceDocument(
            research_project_id=RESEARCH_PROJECT,
            source_id=SOURCE,
            source_type="url",
            uri="https://docs.example.test/retries",
            canonical_uri="https://docs.example.test/retries",
            title="Provider retry guide",
            source_class=SourceClass.OFFICIAL_DOCUMENTATION,
            authorization=authorization(),
            retrieved_at=WHEN,
        ),
        SourceSnapshotDocument(
            research_project_id=RESEARCH_PROJECT,
            source_id=SOURCE,
            snapshot_id=SNAPSHOT,
            retrieved_at=WHEN,
            content_hash=HASH,
            version_identifier="etag:abc123",
            authorization=authorization(),
        ),
        ResearchDocumentDocument(
            research_project_id=RESEARCH_PROJECT,
            source_id=SOURCE,
            snapshot_id=SNAPSHOT,
            document_id=DOCUMENT,
            title="Provider retry guide",
            content_type="text/markdown",
            content="Retry requests must carry an idempotency key.",
            content_hash=HASH,
            source_class=SourceClass.OFFICIAL_DOCUMENTATION,
            authorization=authorization(),
        ),
        ResearchChunkDocument(
            research_project_id=RESEARCH_PROJECT,
            source_id=SOURCE,
            snapshot_id=SNAPSHOT,
            document_id=DOCUMENT,
            chunk_id="retry-requirement",
            chunk_index=0,
            content="Retry requests must carry an idempotency key.",
            source_class=SourceClass.OFFICIAL_DOCUMENTATION,
            locator=locator(),
            authorization=authorization(),
        ),
        ResearchNoteDocument(
            research_project_id=RESEARCH_PROJECT,
            note_id="provider-requirement",
            title="Provider retry requirement",
            body="The guide requires idempotency keys for retries.",
            source_refs=[
                {"source_id": SOURCE, "snapshot_id": SNAPSHOT, "chunk_id": "retry-requirement"}
            ],
            authorization=authorization(),
        ),
        ResearchClaimDocument(
            research_project_id=RESEARCH_PROJECT,
            claim_id="idempotency-key-required",
            statement="Retries require an idempotency key.",
            status=ClaimState.UNREVIEWED,
            authorization=authorization(),
        ),
        ResearchCitationDocument(
            research_project_id=RESEARCH_PROJECT,
            claim_id="idempotency-key-required",
            citation_id="provider-guide",
            source_id=SOURCE,
            snapshot_id=SNAPSHOT,
            chunk_id="retry-requirement",
            source_class=SourceClass.OFFICIAL_DOCUMENTATION,
            locator=locator(),
            evidence_excerpt="Retry requests must carry an idempotency key.",
            relation=CitationRelation.SUPPORTS,
            authorization=authorization(),
        ),
        ResearchBriefDocument(
            research_project_id=RESEARCH_PROJECT,
            brief_id="retry-policy-summary",
            title="Retry policy evidence",
            question="What retry guarantees are required?",
            scope="Official provider documentation only.",
            summary="The current guide requires idempotency keys for retries.",
            evidence_citation_ids=["provider-guide"],
            source_ids=[SOURCE],
            authorization=authorization(),
        ),
        KnowledgeCandidateDocument(
            research_project_id=RESEARCH_PROJECT,
            candidate_id="retry-context-update",
            candidate_type=CandidateType.PROJECT_CONTEXT,
            target_project_id=PROJECT,
            title="Provider retry requirement",
            content="Use an idempotency key for provider retries.",
            source_claim_ids=["idempotency-key-required"],
            source_citation_ids=["provider-guide"],
            authorization=authorization(),
        ),
        PromotionEventDocument(
            research_project_id=RESEARCH_PROJECT,
            candidate_id="retry-context-update",
            event_id="approved-20260928",
            target_type=CandidateType.PROJECT_CONTEXT,
            reviewer="reviewer",
            decision=CandidateState.APPROVED,
            decided_at=WHEN,
            authorization=authorization(),
        ),
        IngestionJobDocument(
            research_project_id=RESEARCH_PROJECT,
            job_id="provider-docs-ingest",
            source_id=SOURCE,
            status=IngestionJobState.PENDING,
            authorization=authorization(),
        ),
    )


def legacy_fixture() -> dict[str, list[dict[str, object]]]:
    """No V6 metadata: revisions and historical actors are intentionally nondefault."""
    values = {
        "skills": dict(
            skill_id="retry-skill",
            name="Retry",
            version="1.0.0",
            description="Retries",
            category="backend",
            trust_level="trusted",
            source_path="retry.md",
            content_hash=HASH,
        ),
        "skill_chunks": dict(
            skill_id="retry-skill",
            skill_version="1.0.0",
            chunk_id="retry-chunk",
            content="Retry carefully",
            chunk_index=0,
            tokens=3,
        ),
        "projects": dict(
            project_id="payment-service",
            name="Payments",
            description="Payments",
            repository="https://example.test/payments",
            environment="test",
        ),
        "project_context": dict(
            project_id="payment-service",
            external_id="payment-idempotency-context",
            type="architecture",
            title="Retries",
            content="Preserved text",
            importance=0.8,
            source="human",
        ),
        "memories": dict(
            scope="project",
            project_id="payment-service",
            external_id="retry-memory",
            type="decision",
            content="Remember retries",
            importance=0.8,
            confidence=0.9,
            source="human",
        ),
        "conversations": dict(
            project_id="payment-service",
            session_id="external-session",
            role="user",
            content="Preserved conversation",
        ),
        "retrieval_logs": dict(
            query="Refused query history",
            requested_sources=["skills"],
            result_count=0,
            latency_ms=1.0,
            retrieval_method="lexical",
        ),
        "api_keys": dict(
            key_id="a" * 16,
            key_hash=HASH,
            name="Legacy",
            scopes=["search:read"],
            projects=[],
            profiles=[],
        ),
        "audit_logs": dict(action="authorize", outcome="denied", key_id="b" * 16),
        "client_profiles": dict(profile_id="template", name="Template", scopes=["search:read"]),
        "gateway_idempotency": dict(
            client_id="agent-one",
            operation="memory.create",
            key_hash=HASH,
            payload_hash=HASH,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        ),
        "agent_clients": dict(
            client_id="agent-one",
            client_type="codex",
            name="Agent",
            owner_id="historical-owner",
            project_ids=[],
            profile_id="template",
        ),
        "agent_sessions": dict(
            session_id="session-one",
            client_id="agent-one",
            client_type="codex",
            started_at=WHEN,
            last_seen_at=WHEN,
            project_id="payment-service",
        ),
        "project_bindings": dict(
            binding_id="binding-one",
            project_id="payment-service",
            binding_type="repository_url",
            value="https://example.test/payments",
        ),
        "agent_activity": dict(
            event_id="event-one",
            client_id="retired-agent",
            client_type="codex",
            operation="search",
            status="succeeded",
        ),
    }
    documents: list[MongoDocumentModel] = [
        MODEL_BY_COLLECTION[name](**value) for name, value in values.items()
    ]
    documents.extend(content_documents())
    documents.extend(research_documents())
    documents.append(
        MODEL_BY_COLLECTION["projects"](**{**values["projects"], "project_id": PROJECT})
    )
    documents.append(
        SkillUpdateProposalDocument(
            research_project_id=RESEARCH_PROJECT,
            proposal_id="proposal-one",
            skill_id="retry-skill",
            current_version="1.0.0",
            proposed_change="Explain retries",
            proposed_patch="Untrusted text",
            source_claim_ids=["idempotency-key-required"],
            reason="Preserved evidence",
            authorization=authorization(),
        )
    )
    result = {name: [] for name in MODEL_BY_COLLECTION}
    reverse = {model: name for name, model in MODEL_BY_COLLECTION.items()}
    for document in documents:
        document.revision = 4
        if "actor" in type(document).model_fields:
            document.actor = {"actor_type": "human"}
        result[reverse[type(document)]].append(document.model_dump(by_alias=True, mode="python"))
    return result
