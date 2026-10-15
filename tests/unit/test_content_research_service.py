"""V3 Phase 13 explicit V2 content/research evidence mapping tests."""

from collections.abc import Sequence

import pytest

from app.models.common import Page
from app.models.content import ContentItemDocument, ContentPlatform
from app.models.research import (
    CitationLocator,
    CitationRelation,
    ClaimState,
    ResearchAuthorization,
    ResearchBriefCitationReference,
    ResearchBriefClaimReference,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
    ResearchProjectType,
    ResearchSourceDocument,
    ResearchSourceType,
    SourceClass,
    SourceSnapshotDocument,
)
from app.research.errors import ResearchProjectNotFoundError
from app.services.content_research_service import (
    ContentResearchIntegrationService,
    FactReviewClassification,
)
from app.services.research_evidence_service import CitationEvidence

PROJECT = "payments-api"
RESEARCH_PROJECT = "payment-retries-research"
PROFILE = "personal-brand"


def project(*, project_ids: Sequence[str] = (PROJECT,)) -> ResearchProjectDocument:
    return ResearchProjectDocument(
        research_project_id=RESEARCH_PROJECT,
        name="Payment retry research",
        question="How should payment retries work?",
        type=ResearchProjectType.CONTENT_RESEARCH,
        related_project_ids=list(project_ids),
        authorization=ResearchAuthorization(project_ids=list(project_ids)),
    )


def claim(
    claim_id: str,
    statement: str,
    status: ClaimState,
    *,
    project_ids: Sequence[str] = (PROJECT,),
) -> ResearchClaimDocument:
    return ResearchClaimDocument(
        research_project_id=RESEARCH_PROJECT,
        claim_id=claim_id,
        statement=statement,
        status=status,
        authorization=ResearchAuthorization(project_ids=list(project_ids)),
    )


def citation(
    claim_id: str,
    citation_id: str,
    relation: CitationRelation,
    *,
    project_ids: Sequence[str] = (PROJECT,),
) -> ResearchCitationDocument:
    return ResearchCitationDocument(
        research_project_id=RESEARCH_PROJECT,
        claim_id=claim_id,
        citation_id=citation_id,
        source_id="payment-standard",
        snapshot_id="payment-standard-v1",
        chunk_id="payment-standard-chunk",
        source_class=SourceClass.STANDARD,
        locator=CitationLocator(url="https://example.test/payments#retries", page=4),
        evidence_excerpt="A payment retry must preserve the original idempotency key.",
        relation=relation,
        authorization=ResearchAuthorization(project_ids=list(project_ids)),
    )


def evidence(
    citation_document: ResearchCitationDocument, claim_document: ResearchClaimDocument
) -> CitationEvidence:
    source = ResearchSourceDocument(
        research_project_id=RESEARCH_PROJECT,
        source_id=citation_document.source_id,
        source_type=ResearchSourceType.MANUAL,
        uri="manual:payment-standard",
        title="Payment standard",
        source_class=SourceClass.STANDARD,
        authorization=ResearchAuthorization(project_ids=[PROJECT]),
    )
    snapshot = SourceSnapshotDocument(
        research_project_id=RESEARCH_PROJECT,
        source_id=source.source_id,
        snapshot_id=citation_document.snapshot_id,
        retrieved_at=source.created_at,
        content_hash="a" * 64,
        authorization=ResearchAuthorization(project_ids=[PROJECT]),
    )
    return CitationEvidence(
        citation=citation_document,
        claim=claim_document,
        source=source,
        snapshot=snapshot,
    )


def draft(body: str) -> ContentItemDocument:
    return ContentItemDocument(
        profile_id=PROFILE,
        content_id="medium-payment-retries",
        brief_id="payment-retries-brief",
        platform=ContentPlatform.MEDIUM,
        content_type="article",
        title="Payment retry strategies",
        body=body,
        topic_tags=[],
        project_ids=[PROJECT],
        source_refs=[],
    )


class Projects:
    def __init__(self, document: ResearchProjectDocument) -> None:
        self.document = document

    async def get(self, research_project_id: str) -> ResearchProjectDocument | None:
        return self.document if research_project_id == self.document.research_project_id else None


class Claims:
    def __init__(self, records: Sequence[ResearchClaimDocument]) -> None:
        self.records = {record.claim_id: record for record in records}

    async def get(self, claim_id: str) -> ResearchClaimDocument | None:
        return self.records.get(claim_id)

    async def list(
        self,
        *,
        status: ClaimState | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ResearchClaimDocument]:
        del cursor
        records = tuple(
            record for record in self.records.values() if status is None or record.status is status
        )
        return Page(items=records[:limit])


class Citations:
    def __init__(self, records: Sequence[ResearchCitationDocument]) -> None:
        self.records = {(record.claim_id, record.citation_id): record for record in records}

    async def get(self, claim_id: str, citation_id: str) -> ResearchCitationDocument | None:
        return self.records.get((claim_id, citation_id))


class Evidence:
    def __init__(self, records: Sequence[CitationEvidence]) -> None:
        self.records: dict[str, tuple[CitationEvidence, ...]] = {}
        for record in records:
            existing = self.records.get(record.claim.claim_id, ())
            self.records[record.claim.claim_id] = (*existing, record)

    async def get_claim_evidence(
        self,
        *,
        research_project_id: str,
        claim_id: str,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> tuple[CitationEvidence, ...]:
        assert research_project_id == RESEARCH_PROJECT
        return self.records.get(claim_id, ())


def service(
    claims: Sequence[ResearchClaimDocument], citations: Sequence[ResearchCitationDocument]
) -> ContentResearchIntegrationService:
    claims_store = Claims(claims)
    citation_store = Citations(citations)
    evidence_records = [
        evidence(citation_document, claims_store.records[citation_document.claim_id])
        for citation_document in citations
    ]
    return ContentResearchIntegrationService(
        Projects(project()),
        lambda _: claims_store,
        lambda _: citation_store,
        Evidence(evidence_records),
    )


@pytest.mark.asyncio
async def test_review_supported_claim_preserves_citation_locator_without_mutating_draft() -> None:
    supported = claim(
        "retry-key",
        "Payment retries preserve the original idempotency key.",
        ClaimState.SUPPORTED,
    )
    support = citation("retry-key", "retry-key-standard", CitationRelation.SUPPORTS)
    item = draft("Payment retries preserve the original idempotency key. This is the draft.")
    original = item.model_dump(mode="json")

    reviewed = await service([supported], [support]).review_content_facts(
        content=item,
        research_project_id=RESEARCH_PROJECT,
        claim_ids=(),
        claim_statements=(),
        allowed_project_ids=(PROJECT,),
    )

    assert reviewed.factual_review_status == "verified"
    assert reviewed.supported == 1
    assert reviewed.unsupported == reviewed.contested == reviewed.outdated == 0
    assert reviewed.findings[0].classification is FactReviewClassification.SUPPORTED
    assert reviewed.citation_refs[0].locator.url == "https://example.test/payments#retries"
    assert reviewed.citation_refs[0].locator.page == 4
    assert item.model_dump(mode="json") == original


@pytest.mark.asyncio
async def test_review_keeps_unsupported_contested_and_outdated_claims_visible() -> None:
    supported = claim(
        "supported", "Idempotency keys prevent duplicate charges.", ClaimState.SUPPORTED
    )
    unsupported = claim("unsupported", "Retries are always free.", ClaimState.UNSUPPORTED)
    contested = claim(
        "contested", "Every retry should use exponential backoff.", ClaimState.CONTESTED
    )
    outdated = claim("outdated", "The old gateway has no retry policy.", ClaimState.OUTDATED)
    citations = [
        citation("supported", "supported-citation", CitationRelation.SUPPORTS),
        citation("contested", "contested-support", CitationRelation.SUPPORTS),
        citation("contested", "contested-conflict", CitationRelation.CONTRADICTS),
    ]

    reviewed = await service(
        [supported, unsupported, contested, outdated], citations
    ).review_content_facts(
        content=draft("Draft text."),
        research_project_id=RESEARCH_PROJECT,
        claim_ids=("supported", "unsupported", "contested", "outdated"),
        claim_statements=(),
        allowed_project_ids=(PROJECT,),
    )

    classifications = {finding.claim_id: finding.classification for finding in reviewed.findings}
    assert reviewed.factual_review_status == "needs_review"
    assert (reviewed.supported, reviewed.unsupported, reviewed.contested, reviewed.outdated) == (
        1,
        1,
        1,
        1,
    )
    assert classifications == {
        "supported": FactReviewClassification.SUPPORTED,
        "unsupported": FactReviewClassification.UNSUPPORTED,
        "contested": FactReviewClassification.CONTESTED,
        "outdated": FactReviewClassification.OUTDATED,
    }
    assert {reference.relation for reference in reviewed.citation_refs} == {
        CitationRelation.SUPPORTS,
        CitationRelation.CONTRADICTS,
    }


@pytest.mark.asyncio
async def test_review_reports_explicit_unmatched_claim_as_missing_evidence() -> None:
    reviewed = await service([], []).review_content_facts(
        content=draft("Draft text."),
        research_project_id=RESEARCH_PROJECT,
        claim_ids=(),
        claim_statements=("This assertion has no stored research claim.",),
        allowed_project_ids=(PROJECT,),
    )

    assert reviewed.factual_review_status == "needs_review"
    assert reviewed.findings[0].classification is FactReviewClassification.MISSING_EVIDENCE
    assert reviewed.findings[0].citation_refs == ()
    assert reviewed.missing_evidence


@pytest.mark.asyncio
async def test_materialized_brief_references_are_canonical_and_retain_project_scope() -> None:
    assessed = claim(
        "retry-key", "Payment retries preserve the original idempotency key.", ClaimState.SUPPORTED
    )
    stored = citation("retry-key", "retry-key-standard", CitationRelation.SUPPORTS)
    material = await service([assessed], [stored]).materialize_brief_references(
        research_project_id=RESEARCH_PROJECT,
        claim_refs=[
            ResearchBriefClaimReference(
                claim_id="retry-key",
                statement="Caller supplied text is not retained.",
                status=ClaimState.UNSUPPORTED,
            )
        ],
        citation_refs=[
            ResearchBriefCitationReference(
                citation_id="retry-key-standard",
                claim_id="retry-key",
                source_id="invented-source",
                snapshot_id="invented-snapshot",
                source_class=SourceClass.GENERATED,
                locator=CitationLocator(url="https://example.test/invented"),
                evidence_excerpt="Invented excerpt.",
                relation=CitationRelation.CONTRADICTS,
            )
        ],
        allowed_project_ids=(PROJECT,),
    )

    assert material.claim_refs[0].statement == assessed.statement
    assert material.claim_refs[0].status is ClaimState.SUPPORTED
    assert material.citation_refs[0].source_id == stored.source_id
    assert material.citation_refs[0].relation is CitationRelation.SUPPORTS
    assert material.source_refs[0].source_type == "research_project"
    assert material.source_refs[0].project_id == PROJECT


@pytest.mark.asyncio
async def test_private_research_scope_is_not_materialized_for_another_project() -> None:
    assessed = claim("private", "Private retry statement.", ClaimState.SUPPORTED)
    private_service = ContentResearchIntegrationService(
        Projects(project(project_ids=(PROJECT,))),
        lambda _: Claims([assessed]),
        lambda _: Citations(()),
        Evidence(()),
    )

    with pytest.raises(ResearchProjectNotFoundError):
        await private_service.review_content_facts(
            content=draft("Private retry statement."),
            research_project_id=RESEARCH_PROJECT,
            claim_ids=(),
            claim_statements=(),
            allowed_project_ids=("other-project",),
        )


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
