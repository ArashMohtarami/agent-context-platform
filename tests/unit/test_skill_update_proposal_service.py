"""V3 Phase 11 service tests: versioning, provenance, authorization, and no mutation."""

from collections.abc import Sequence

import pytest

from app.models.common import Page
from app.models.research import (
    CitationLocator,
    CitationRelation,
    ClaimState,
    ResearchAuthorization,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
    SkillUpdateProposalDocument,
)
from app.models.skill import SkillDocument
from app.research.errors import (
    SkillUpdateProposalProvenanceError,
    SkillUpdateProposalSkillNotFoundError,
    SkillUpdateProposalVersionMismatchError,
)
from app.services.skill_update_proposal_service import SkillUpdateProposalService

RESEARCH = "retry-evidence"
PROJECT = "payments-api"


def research_project() -> ResearchProjectDocument:
    return ResearchProjectDocument(
        research_project_id=RESEARCH,
        name="Retry evidence",
        question="What must backend skill guidance say about retries?",
        type="technical_research",
        related_project_ids=[PROJECT],
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="owner"),
    )


def claim(*, projects: Sequence[str] = (PROJECT,)) -> ResearchClaimDocument:
    return ResearchClaimDocument(
        research_project_id=RESEARCH,
        claim_id="retry-key",
        statement="Retryable provider writes require an idempotency key.",
        status=ClaimState.SUPPORTED,
        confidence=0.9,
        confidence_rationale="A human evaluated the retained citation.",
        authorization=ResearchAuthorization(project_ids=list(projects), owner_key_id="writer"),
    )


def citation(*, projects: Sequence[str] = (PROJECT,)) -> ResearchCitationDocument:
    return ResearchCitationDocument(
        research_project_id=RESEARCH,
        claim_id="retry-key",
        citation_id="provider-support",
        source_id="provider-docs",
        snapshot_id="provider-v2",
        source_class="official_documentation",
        locator=CitationLocator(url="https://docs.example.test/retries", line_start=12),
        evidence_excerpt="Supply an idempotency key for retryable writes.",
        relation=CitationRelation.SUPPORTS,
        authorization=ResearchAuthorization(project_ids=list(projects), owner_key_id="writer"),
    )


def skill() -> SkillDocument:
    return SkillDocument(
        skill_id="backend-guidance",
        name="Backend guidance",
        version="1.2.3",
        description="Canonical backend engineering guidance.",
        category="backend",
        trust_level="trusted",
        source_path="skills/backend-guidance.md",
        content_hash="a" * 64,
    )


class Projects:
    async def get(self, research_project_id: str) -> ResearchProjectDocument | None:
        return research_project() if research_project_id == RESEARCH else None


class Skills:
    def __init__(self, value: SkillDocument | None) -> None:
        self.value = value
        self.reads = 0

    async def get_active(self, skill_id: str) -> SkillDocument | None:
        self.reads += 1
        return self.value if self.value is not None and skill_id == self.value.skill_id else None


class Claims:
    def __init__(self, value: ResearchClaimDocument) -> None:
        self.value = value

    async def get(self, claim_id: str) -> ResearchClaimDocument | None:
        return self.value if claim_id == self.value.claim_id else None


class Citations:
    def __init__(self, value: ResearchCitationDocument) -> None:
        self.value = value

    async def get(self, claim_id: str, citation_id: str) -> ResearchCitationDocument | None:
        if claim_id == self.value.claim_id and citation_id == self.value.citation_id:
            return self.value
        return None


class Proposals:
    def __init__(self) -> None:
        self.values: dict[str, SkillUpdateProposalDocument] = {}

    async def create(self, document: SkillUpdateProposalDocument) -> SkillUpdateProposalDocument:
        if document.proposal_id in self.values:
            from app.repositories.errors import DuplicateDocumentError

            raise DuplicateDocumentError("duplicate")
        self.values[document.proposal_id] = document
        return document

    async def get(self, proposal_id: str) -> SkillUpdateProposalDocument | None:
        return self.values.get(proposal_id)

    async def list(self, **_: object) -> Page[SkillUpdateProposalDocument]:
        return Page(items=tuple(self.values.values()), next_cursor=None)


def service(
    *,
    current_skill: SkillDocument | None = None,
    evidence_claim: ResearchClaimDocument | None = None,
    evidence_citation: ResearchCitationDocument | None = None,
) -> tuple[SkillUpdateProposalService, Skills, Proposals]:
    skills = Skills(skill() if current_skill is None else current_skill)
    claims = Claims(claim() if evidence_claim is None else evidence_claim)
    citations = Citations(citation() if evidence_citation is None else evidence_citation)
    proposals = Proposals()
    return (
        SkillUpdateProposalService(
            Projects(),
            skills,
            lambda _: claims,
            lambda _: citations,
            lambda _: proposals,
        ),
        skills,
        proposals,
    )


@pytest.mark.asyncio
async def test_proposal_preserves_citations_and_generated_trust_without_mutating_skill() -> None:
    current = skill()
    before = current.model_dump(mode="json")
    target, skills, proposals = service(current_skill=current)

    proposal = await target.create_proposal(
        research_project_id=RESEARCH,
        proposal_id="idempotency-guidance",
        skill_id="backend-guidance",
        current_version="1.2.3",
        proposed_change="Clarify idempotency-key guidance.",
        proposed_patch="--- a/skills/backend-guidance.md\n+++ b/skills/backend-guidance.md",
        source_claim_ids=["retry-key"],
        source_citation_ids=["provider-support"],
        reason="Provider requirements changed.",
        created_by="writer",
        allowed_project_ids=(PROJECT,),
    )

    assert proposal.trust_level == "generated"
    assert proposal.status == "pending_review"
    assert proposal.source_claim_ids == ["retry-key"]
    assert proposal.source_citation_ids == ["provider-support"]
    assert proposal.authorization.project_ids == [PROJECT]
    assert skills.reads == 1
    assert current.model_dump(mode="json") == before
    assert proposals.values == {"idempotency-guidance": proposal}


@pytest.mark.asyncio
async def test_proposal_rejects_stale_or_unknown_canonical_skill_without_writing_one() -> None:
    target, skills, proposals = service()
    with pytest.raises(SkillUpdateProposalVersionMismatchError):
        await target.create_proposal(
            research_project_id=RESEARCH,
            proposal_id="stale",
            skill_id="backend-guidance",
            current_version="1.2.2",
            proposed_change="Clarify retries.",
            proposed_patch="--- a/skills/backend-guidance.md\n+++ b/skills/backend-guidance.md",
            source_claim_ids=["retry-key"],
            source_citation_ids=[],
            reason="Evidence changed.",
            created_by="writer",
            allowed_project_ids=(PROJECT,),
        )
    assert proposals.values == {}
    assert skills.reads == 1

    missing, _, missing_proposals = service(current_skill=skill())
    with pytest.raises(SkillUpdateProposalSkillNotFoundError):
        await missing.create_proposal(
            research_project_id=RESEARCH,
            proposal_id="unknown",
            skill_id="unknown-skill",
            current_version="1.2.3",
            proposed_change="Clarify retries.",
            proposed_patch="--- a/skills/unknown.md\n+++ b/skills/unknown.md",
            source_claim_ids=["retry-key"],
            source_citation_ids=[],
            reason="Evidence changed.",
            created_by="writer",
            allowed_project_ids=(PROJECT,),
        )
    assert missing_proposals.values == {}


@pytest.mark.asyncio
async def test_proposal_requires_authorized_project_local_claim_and_citation() -> None:
    target, _, proposals = service(evidence_claim=claim(projects=("private-ledger",)))
    with pytest.raises(SkillUpdateProposalProvenanceError, match="claim"):
        await target.create_proposal(
            research_project_id=RESEARCH,
            proposal_id="private-evidence",
            skill_id="backend-guidance",
            current_version="1.2.3",
            proposed_change="Clarify retries.",
            proposed_patch="--- a/skills/backend-guidance.md\n+++ b/skills/backend-guidance.md",
            source_claim_ids=["retry-key"],
            source_citation_ids=["provider-support"],
            reason="Evidence changed.",
            created_by="writer",
            allowed_project_ids=(PROJECT,),
        )
    assert proposals.values == {}
