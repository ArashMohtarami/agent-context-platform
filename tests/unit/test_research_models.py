"""V3 Phase 0 model, enum, provenance, and serialization checks."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.research import (
    CandidateState,
    CandidateType,
    CitationLocator,
    CitationRelation,
    ClaimState,
    DocumentContentStorage,
    IngestionJobDocument,
    IngestionJobState,
    KnowledgeCandidateDocument,
    PromotionEventDocument,
    ResearchBriefCitationReference,
    ResearchBriefClaimReference,
    ResearchBriefContradiction,
    ResearchBriefDocument,
    ResearchChunkDocument,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchDocumentDocument,
    ResearchNoteDocument,
    ResearchProjectDocument,
    ResearchProjectType,
    ResearchSourceDocument,
    SourceClass,
    SourceSnapshotDocument,
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


def documents() -> tuple[object, ...]:
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


def test_research_brief_rejects_inconsistent_structured_provenance() -> None:
    with pytest.raises(ValidationError, match="structured references"):
        ResearchBriefDocument(
            research_project_id=RESEARCH_PROJECT,
            brief_id="inconsistent-brief",
            title="Retry policy evidence",
            question="What retry guarantees are required?",
            scope="Official documentation only.",
            summary="Structured evidence only.",
            evidence_citation_ids=["provider-guide"],
            citation_refs=[
                ResearchBriefCitationReference(
                    citation_id="different-citation",
                    claim_id="idempotency-key-required",
                    source_id=SOURCE,
                    snapshot_id=SNAPSHOT,
                    source_class=SourceClass.OFFICIAL_DOCUMENTATION,
                    locator=CitationLocator(**locator()),
                    evidence_excerpt="Different excerpt.",
                    relation=CitationRelation.SUPPORTS,
                )
            ],
            authorization=authorization(),
        )

    document = ResearchBriefDocument(
        research_project_id=RESEARCH_PROJECT,
        brief_id="structured-brief",
        title="Retry policy evidence",
        question="What retry guarantees are required?",
        scope="Official documentation only.",
        summary="Structured evidence only.",
        evidence_citation_ids=["provider-guide"],
        source_ids=[SOURCE],
        claim_refs=[
            ResearchBriefClaimReference(
                claim_id="idempotency-key-required",
                statement="Retries require an idempotency key.",
                status=ClaimState.SUPPORTED,
                confidence=0.9,
            )
        ],
        citation_refs=[
            ResearchBriefCitationReference(
                citation_id="provider-guide",
                claim_id="idempotency-key-required",
                source_id=SOURCE,
                snapshot_id=SNAPSHOT,
                chunk_id="retry-requirement",
                source_class=SourceClass.OFFICIAL_DOCUMENTATION,
                locator=CitationLocator(**locator()),
                evidence_excerpt="Retry requests must carry an idempotency key.",
                relation=CitationRelation.SUPPORTS,
            )
        ],
        contradictions=[
            ResearchBriefContradiction(
                claim_id="idempotency-key-required",
                statement="Retries require an idempotency key.",
                supporting_citation_ids=["provider-guide"],
            )
        ],
        source_refs=[{"source_id": SOURCE, "snapshot_id": SNAPSHOT}],
        authorization=authorization(),
    )

    assert document.citation_refs[0].locator.anchor == "retry-policy"
    assert document.source_refs[0].snapshot_id == SNAPSHOT


def test_v3_models_round_trip_with_deterministic_ids_and_timestamps() -> None:
    for document in documents():
        document_type = type(document)
        encoded = document.model_dump(by_alias=True, mode="python")  # type: ignore[union-attr]
        decoded = document_type.model_validate(encoded)  # type: ignore[union-attr]
        assert decoded == document
        assert document.created_at.tzinfo is not None  # type: ignore[union-attr]
        assert document.updated_at.tzinfo is not None  # type: ignore[union-attr]


def test_candidate_review_lifecycle_requires_explicit_review_metadata_and_skill_target_shape() -> (
    None
):
    with pytest.raises(ValidationError, match="pending_review"):
        KnowledgeCandidateDocument(
            research_project_id=RESEARCH_PROJECT,
            candidate_id="pending-with-reviewer",
            candidate_type=CandidateType.KNOWLEDGE,
            title="Pending review metadata",
            content="A pending proposal has not been reviewed yet.",
            source_claim_ids=["idempotency-key-required"],
            reviewer="reviewer",
            reviewed_at=WHEN,
            authorization=authorization(),
        )
    with pytest.raises(ValidationError, match="reviewed candidates require"):
        KnowledgeCandidateDocument(
            research_project_id=RESEARCH_PROJECT,
            candidate_id="approved-without-reviewer",
            candidate_type=CandidateType.KNOWLEDGE,
            title="Missing reviewer",
            content="An approval needs a recorded reviewer and timestamp.",
            source_claim_ids=["idempotency-key-required"],
            status=CandidateState.APPROVED,
            authorization=authorization(),
        )
    with pytest.raises(ValidationError, match="skill target fields"):
        KnowledgeCandidateDocument(
            research_project_id=RESEARCH_PROJECT,
            candidate_id="non-skill-version",
            candidate_type=CandidateType.KNOWLEDGE,
            current_skill_version="1.0.0",
            title="Invalid skill version",
            content="Only a skill-update candidate can name a skill version.",
            source_claim_ids=["idempotency-key-required"],
            authorization=authorization(),
        )


def test_v3_required_vocabularies_are_complete_and_exact() -> None:
    assert {item.value for item in ResearchProjectType} == {
        "technical_research",
        "architecture_evaluation",
        "technology_comparison",
        "incident_investigation",
        "content_research",
        "learning",
        "documentation_research",
        "market_research",
        "repository_analysis",
    }
    assert {item.value for item in SourceClass} == {
        "primary",
        "official_documentation",
        "standard",
        "source_code",
        "academic",
        "secondary",
        "community",
        "user_provided",
        "generated",
        "unknown",
    }
    assert {item.value for item in ClaimState} == {
        "unreviewed",
        "supported",
        "partially_supported",
        "contested",
        "unsupported",
        "outdated",
    }
    assert {item.value for item in CandidateState} == {
        "pending_review",
        "approved",
        "rejected",
        "needs_changes",
        "promoted",
        "superseded",
    }
    assert {item.value for item in IngestionJobState} == {
        "pending",
        "running",
        "completed",
        "failed",
        "cancelled",
    }
    assert {item.value for item in CandidateType} == {
        "knowledge",
        "memory",
        "project_context",
        "architecture_decision",
        "skill_update",
        "content_fact",
    }


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (
            lambda: ResearchProjectDocument(
                research_project_id=RESEARCH_PROJECT,
                name="Wrong scope",
                question="Will this be authorized?",
                type="learning",
                related_project_ids=[PROJECT],
                authorization={"project_ids": []},
            ),
            "authorization project_ids",
        ),
        (
            lambda: ResearchDocumentDocument(
                research_project_id=RESEARCH_PROJECT,
                source_id=SOURCE,
                snapshot_id=SNAPSHOT,
                document_id=DOCUMENT,
                title="Metadata only but content kept",
                content_type="text/plain",
                content="This must be refused.",
                content_storage=DocumentContentStorage.METADATA_ONLY,
                content_hash=HASH,
                source_class="unknown",
                authorization=authorization(),
            ),
            "metadata_only",
        ),
        (
            lambda: ResearchClaimDocument(
                research_project_id=RESEARCH_PROJECT,
                claim_id="contradictory-link",
                statement="One citation cannot occupy opposing relations.",
                supporting_citation_ids=["citation"],
                contradicting_citation_ids=["citation"],
                authorization=authorization(),
            ),
            "support and contradict",
        ),
        (
            lambda: KnowledgeCandidateDocument(
                research_project_id=RESEARCH_PROJECT,
                candidate_id="unreviewed-skill",
                candidate_type="skill_update",
                title="Unsafe automatic skill change",
                content="This does not provide a target skill.",
                authorization=authorization(),
            ),
            "target_skill_id",
        ),
        (
            lambda: IngestionJobDocument(
                research_project_id=RESEARCH_PROJECT,
                job_id="invalid-completion",
                source_id=SOURCE,
                completed_at=WHEN,
                authorization=authorization(),
            ),
            "completed_at requires started_at",
        ),
    ],
)
def test_v3_models_reject_invalid_provenance_lifecycle_and_review_shapes(
    factory: object, match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        factory()  # type: ignore[operator]
