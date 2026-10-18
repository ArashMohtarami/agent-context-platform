"""Bounded browser inputs and content-only projections, without vectors or job authority."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.identity.models import Input
from app.models.common import Identifier
from app.models.research import CitationLocator, ClaimState, SkillUpdateProposalStatus
from app.schemas.research import ResearchClaimEvidenceResponse, ResearchIngestionRequest

MAX_BROWSER_INGESTION_BYTES = 8192


class BrowserIngestion(ResearchIngestionRequest):
    @model_validator(mode="after")
    def bounded_browser_submission(self) -> "BrowserIngestion":
        if self.fetch_repository:
            raise ValueError("repository acquisition is not available in the console")
        if len(self.submitted_bytes()) > MAX_BROWSER_INGESTION_BYTES:
            raise ValueError("browser submission exceeds 8 KiB")
        return self


class Decision(Input):
    expected_revision: int = Field(ge=1)
    decision: Literal["approved", "rejected", "needs_changes"]
    reason: str | None = Field(default=None, min_length=1, max_length=4000)


class ProposalDecision(Input):
    expected_revision: int = Field(ge=1)
    decision: SkillUpdateProposalStatus

    @model_validator(mode="after")
    def final_decision(self) -> "ProposalDecision":
        if self.decision not in {
            SkillUpdateProposalStatus.APPROVED,
            SkillUpdateProposalStatus.REJECTED,
        }:
            raise ValueError("select approved or rejected")
        return self


class ClaimDecision(Input):
    expected_revision: int = Field(ge=1)
    status: ClaimState
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_rationale: Annotated[str, Field(min_length=1, max_length=4000)]
    supporting_citations: list[Identifier] = Field(default_factory=list, max_length=100)
    contradicting_citations: list[Identifier] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def assessed(self) -> "ClaimDecision":
        if self.status == ClaimState.UNREVIEWED:
            raise ValueError("a review requires an explicit assessment")
        return self


class RetainedDocument(Input):
    source_id: str
    snapshot_id: str
    document_id: str
    title: str
    content: str | None
    content_type: str
    content_storage: str
    language: str
    content_hash: str
    source_class: str
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime


class RetainedChunk(Input):
    source_id: str
    snapshot_id: str
    document_id: str
    chunk_id: str
    content: str
    locator: CitationLocator
    revision: int


class Coverage(Input):
    completeness: Literal["bounded"] = "bounded"
    citation_limit: Literal[50] = 50


class ConsoleEvidence(ResearchClaimEvidenceResponse):
    coverage: Coverage = Field(default_factory=Coverage)
