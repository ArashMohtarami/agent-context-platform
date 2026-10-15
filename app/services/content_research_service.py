"""Read-only V3 evidence integration for V2 creator-content workflows.

This service maps explicit research claims to a saved V2 draft and freezes
verified research references into V2 briefs. It never invokes retrieval to
infer a factual verdict, generates or rewrites prose, changes a content
lifecycle, publishes content, or promotes research into V1 knowledge.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.models.common import Page
from app.models.content import (
    ContentFactualReviewStatus,
    ContentItemDocument,
    ContentSourceReference,
)
from app.models.research import (
    CitationRelation,
    ClaimState,
    ResearchAuthorization,
    ResearchBriefCitationReference,
    ResearchBriefClaimReference,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
)
from app.research.errors import ResearchClaimNotFoundError, ResearchProjectNotFoundError
from app.security.tenant_service import tenant_service
from app.services.research_evidence_service import CitationEvidence

_MAX_FACT_CLAIMS = 100
_FACT_REVIEW_PAGE_SIZE = 50
_NORMALIZE = re.compile(r"[^a-z0-9]+", re.IGNORECASE)


class ResearchProjectLookup(Protocol):
    """Read the V3 scope that controls all cited material."""

    async def get(self, research_project_id: str) -> ResearchProjectDocument | None: ...


class ResearchClaimStore(Protocol):
    """Read explicit user-assessed claims without deriving a new assessment."""

    async def get(self, claim_id: str) -> ResearchClaimDocument | None: ...

    async def list(
        self,
        *,
        status: ClaimState | None = ...,
        allowed_project_ids: tuple[str, ...] | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[ResearchClaimDocument]: ...


class ResearchCitationStore(Protocol):
    """Read authored citation records within one research project."""

    async def get(self, claim_id: str, citation_id: str) -> ResearchCitationDocument | None: ...


class ResearchEvidenceReader(Protocol):
    """Materialize existing citation provenance without evaluating claims."""

    async def get_claim_evidence(
        self,
        *,
        research_project_id: str,
        claim_id: str,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> tuple[CitationEvidence, ...]: ...


ResearchClaimRepositoryFactory = Callable[[str], ResearchClaimStore]
ResearchCitationRepositoryFactory = Callable[[str], ResearchCitationStore]


@dataclass(frozen=True, slots=True)
class ContentBriefResearchMaterial:
    """Canonical V3 references and V1 scope links retained by a V2 brief."""

    research_project_id: str
    claim_refs: tuple[ResearchBriefClaimReference, ...]
    citation_refs: tuple[ResearchBriefCitationReference, ...]
    source_refs: tuple[ContentSourceReference, ...]


class FactReviewClassification(StrEnum):
    """A visible rendering of an explicit research-claim state."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTESTED = "contested"
    UNSUPPORTED = "unsupported"
    OUTDATED = "outdated"
    MISSING_EVIDENCE = "missing_evidence"


@dataclass(frozen=True, slots=True)
class ContentFactFinding:
    """One draft claim mapped to persisted evidence, or an explicit gap."""

    statement: str
    classification: FactReviewClassification
    claim_id: str | None
    claim_state: ClaimState | None
    citation_refs: tuple[ResearchBriefCitationReference, ...]
    missing_evidence: str | None


@dataclass(frozen=True, slots=True)
class ContentFactReview:
    """Advisory evidence mapping for a draft; it is not a factual guarantee."""

    content_id: str
    research_project_id: str
    factual_review_status: ContentFactualReviewStatus
    supported: int
    unsupported: int
    contested: int
    outdated: int
    findings: tuple[ContentFactFinding, ...]
    citation_refs: tuple[ResearchBriefCitationReference, ...]
    missing_evidence: tuple[str, ...]


@tenant_service
class ContentResearchIntegrationService:
    """Join V2 content with only explicit, authorized V3 claim evidence."""

    def __init__(
        self,
        projects: ResearchProjectLookup,
        claims: ResearchClaimRepositoryFactory,
        citations: ResearchCitationRepositoryFactory,
        evidence: ResearchEvidenceReader,
    ) -> None:
        self._projects = projects
        self._claims = claims
        self._citations = citations
        self._evidence = evidence

    async def materialize_brief_references(
        self,
        *,
        research_project_id: str,
        claim_refs: Sequence[ResearchBriefClaimReference],
        citation_refs: Sequence[ResearchBriefCitationReference],
        allowed_project_ids: tuple[str, ...] | None,
    ) -> ContentBriefResearchMaterial:
        """Replace caller copies with the current, authorized persisted references.

        V2 never accepts a caller-invented citation excerpt or source locator.
        It stores the canonical V3 record seen at brief creation, which makes
        later research edits auditable rather than silently rewriting a brief.
        """
        project = await self._require_project(research_project_id, allowed_project_ids)
        if len(claim_refs) > _MAX_FACT_CLAIMS or len(citation_refs) > _MAX_FACT_CLAIMS * 2:
            raise ValueError("too many research references for one content brief")
        requested_claim_ids = [reference.claim_id for reference in claim_refs]
        if len(requested_claim_ids) != len(set(requested_claim_ids)):
            raise ValueError("claim_refs must be unique by claim_id")
        canonical_claims: dict[str, ResearchBriefClaimReference] = {}
        for claim_id in requested_claim_ids:
            claim = await self._claims(research_project_id).get(claim_id)
            if claim is None or not _allowed(claim.authorization, allowed_project_ids):
                raise ValueError("research claim reference was not found")
            canonical_claims[claim.claim_id] = _claim_reference(claim)
        citation_keys = [(reference.claim_id, reference.citation_id) for reference in citation_refs]
        if len(citation_keys) != len(set(citation_keys)):
            raise ValueError("citation_refs must be unique by claim_id and citation_id")
        canonical_citations: list[ResearchBriefCitationReference] = []
        for claim_id, citation_id in citation_keys:
            if claim_id not in canonical_claims:
                raise ValueError("citation_refs must reference a claim in claim_refs")
            citation = await self._citations(research_project_id).get(claim_id, citation_id)
            if citation is None or not _allowed(citation.authorization, allowed_project_ids):
                raise ValueError("research citation reference was not found")
            canonical_citations.append(_citation_reference(citation))
        source_refs = tuple(
            ContentSourceReference(
                source_type="research_project",
                source_id=project.research_project_id,
                project_id=project_id,
            )
            for project_id in project.related_project_ids
        )
        return ContentBriefResearchMaterial(
            research_project_id=project.research_project_id,
            claim_refs=tuple(canonical_claims.values()),
            citation_refs=tuple(canonical_citations),
            source_refs=source_refs,
        )

    async def review_content_facts(
        self,
        *,
        content: ContentItemDocument,
        research_project_id: str,
        claim_ids: Sequence[str],
        claim_statements: Sequence[str],
        allowed_project_ids: tuple[str, ...] | None,
    ) -> ContentFactReview:
        """Map a draft to stored claim states without semantic truth inference.

        Automatic extraction is intentionally conservative: it identifies only
        V3 claim statements that occur exactly after normalization in the draft.
        Other draft statements are reported only when a caller supplies them as
        explicit claim text, in which case an exact V3 claim match is required.
        """
        await self._require_project(research_project_id, allowed_project_ids)
        if len(claim_ids) + len(claim_statements) > _MAX_FACT_CLAIMS:
            raise ValueError("too many factual claims for one review")
        requested_ids = list(claim_ids)
        if len(requested_ids) != len(set(requested_ids)):
            raise ValueError("claim_ids must be unique")
        requested_statements = list(claim_statements)
        if len(requested_statements) != len(set(requested_statements)):
            raise ValueError("claim_statements must be unique")

        selected: list[ResearchClaimDocument] = []
        seen_claim_ids: set[str] = set()
        unmatched_claim_ids: list[str] = []
        for claim_id in requested_ids:
            claim = await self._claims(research_project_id).get(claim_id)
            if claim is not None and _allowed(claim.authorization, allowed_project_ids):
                selected.append(claim)
                seen_claim_ids.add(claim.claim_id)
            else:
                unmatched_claim_ids.append(claim_id)

        listed = await self._list_claims_for_review(
            research_project_id=research_project_id,
            allowed_project_ids=allowed_project_ids,
        )
        claims_by_normalized_statement = {
            _normalize(claim.statement): claim
            for claim in listed
            if _allowed(claim.authorization, allowed_project_ids)
        }
        unmatched_statements: list[str] = []
        for statement in requested_statements:
            claim = claims_by_normalized_statement.get(_normalize(statement))
            if claim is None:
                unmatched_statements.append(statement)
            elif claim.claim_id not in seen_claim_ids:
                selected.append(claim)
                seen_claim_ids.add(claim.claim_id)

        if not requested_ids and not requested_statements:
            normalized_draft = _normalize(f"{content.title} {content.body}")
            for claim in listed:
                normalized_statement = _normalize(claim.statement)
                if (
                    len(normalized_statement) >= 8
                    and _contains_normalized(normalized_draft, normalized_statement)
                    and claim.claim_id not in seen_claim_ids
                ):
                    selected.append(claim)
                    seen_claim_ids.add(claim.claim_id)

        findings: list[ContentFactFinding] = []
        missing: list[str] = []
        for claim_id in unmatched_claim_ids:
            reason = "No authorized research claim matched this explicit claim identifier."
            findings.append(
                ContentFactFinding(
                    statement=f"Claim identifier: {claim_id}",
                    classification=FactReviewClassification.MISSING_EVIDENCE,
                    claim_id=None,
                    claim_state=None,
                    citation_refs=(),
                    missing_evidence=reason,
                )
            )
            missing.append(reason)
        for statement in unmatched_statements:
            reason = "No exact authorized research claim matched this explicit draft claim."
            findings.append(
                ContentFactFinding(
                    statement=statement,
                    classification=FactReviewClassification.MISSING_EVIDENCE,
                    claim_id=None,
                    claim_state=None,
                    citation_refs=(),
                    missing_evidence=reason,
                )
            )
            missing.append(reason)
        for claim in selected:
            evidence = await self._claim_evidence(
                research_project_id=research_project_id,
                claim_id=claim.claim_id,
                allowed_project_ids=allowed_project_ids,
            )
            references = tuple(_citation_reference(item.citation) for item in evidence)
            classification, claim_missing_evidence = _classify_claim(claim, evidence)
            findings.append(
                ContentFactFinding(
                    statement=claim.statement,
                    classification=classification,
                    claim_id=claim.claim_id,
                    claim_state=claim.status,
                    citation_refs=references,
                    missing_evidence=claim_missing_evidence,
                )
            )
            if claim_missing_evidence is not None:
                missing.append(claim_missing_evidence)

        all_citations = _deduplicate_citations(
            reference for finding in findings for reference in finding.citation_refs
        )
        status = (
            ContentFactualReviewStatus.VERIFIED
            if findings
            and all(item.classification is FactReviewClassification.SUPPORTED for item in findings)
            else ContentFactualReviewStatus.NEEDS_REVIEW
        )
        return ContentFactReview(
            content_id=content.content_id,
            research_project_id=research_project_id,
            factual_review_status=status,
            supported=sum(
                item.classification is FactReviewClassification.SUPPORTED for item in findings
            ),
            unsupported=sum(
                item.classification is FactReviewClassification.UNSUPPORTED for item in findings
            ),
            contested=sum(
                item.classification is FactReviewClassification.CONTESTED for item in findings
            ),
            outdated=sum(
                item.classification is FactReviewClassification.OUTDATED for item in findings
            ),
            findings=tuple(findings),
            citation_refs=all_citations,
            missing_evidence=tuple(dict.fromkeys(missing)),
        )

    async def _require_project(
        self, research_project_id: str, allowed_project_ids: tuple[str, ...] | None
    ) -> ResearchProjectDocument:
        project = await self._projects.get(research_project_id)
        if project is None or not _allowed(project.authorization, allowed_project_ids):
            raise ResearchProjectNotFoundError("research project was not found")
        return project

    async def _claim_evidence(
        self,
        *,
        research_project_id: str,
        claim_id: str,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> tuple[CitationEvidence, ...]:
        try:
            return await self._evidence.get_claim_evidence(
                research_project_id=research_project_id,
                claim_id=claim_id,
                allowed_project_ids=allowed_project_ids,
            )
        except ResearchClaimNotFoundError:
            # A claim cannot normally disappear from a stored collection. If it
            # does, keep the gap visible rather than substituting a retrieval hit.
            return ()

    async def _list_claims_for_review(
        self,
        *,
        research_project_id: str,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> tuple[ResearchClaimDocument, ...]:
        """Read at most the advertised review limit through keyset pagination.

        Repositories deliberately cap page sizes. The review limit can span
        two pages, but it must never bypass that boundary or load an unbounded
        research project into a request.
        """
        records: list[ResearchClaimDocument] = []
        cursor: str | None = None
        while len(records) < _MAX_FACT_CLAIMS:
            page = await self._claims(research_project_id).list(
                allowed_project_ids=allowed_project_ids,
                limit=min(_FACT_REVIEW_PAGE_SIZE, _MAX_FACT_CLAIMS - len(records)),
                cursor=cursor,
            )
            records.extend(page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        return tuple(records)


def _allowed(
    authorization: ResearchAuthorization, allowed_project_ids: tuple[str, ...] | None
) -> bool:
    """Require every historical V1 scope recorded with a research artifact."""
    return allowed_project_ids is None or set(authorization.project_ids).issubset(
        allowed_project_ids
    )


def _claim_reference(claim: ResearchClaimDocument) -> ResearchBriefClaimReference:
    return ResearchBriefClaimReference(
        claim_id=claim.claim_id,
        statement=claim.statement,
        status=claim.status,
        confidence=claim.confidence,
    )


def _citation_reference(citation: ResearchCitationDocument) -> ResearchBriefCitationReference:
    return ResearchBriefCitationReference(
        citation_id=citation.citation_id,
        claim_id=citation.claim_id,
        source_id=citation.source_id,
        snapshot_id=citation.snapshot_id,
        chunk_id=citation.chunk_id,
        source_class=citation.source_class,
        locator=citation.locator,
        evidence_excerpt=citation.evidence_excerpt,
        relation=citation.relation,
    )


def _classify_claim(
    claim: ResearchClaimDocument, evidence: Sequence[CitationEvidence]
) -> tuple[FactReviewClassification, str | None]:
    relations = {item.citation.relation for item in evidence}
    has_support = CitationRelation.SUPPORTS in relations
    has_contradiction = CitationRelation.CONTRADICTS in relations
    if claim.status is ClaimState.SUPPORTED:
        if has_support:
            return FactReviewClassification.SUPPORTED, None
        return (
            FactReviewClassification.MISSING_EVIDENCE,
            f"Supported claim {claim.claim_id} has no authorized supporting citation.",
        )
    if claim.status is ClaimState.PARTIALLY_SUPPORTED:
        return (
            FactReviewClassification.PARTIALLY_SUPPORTED,
            f"Claim {claim.claim_id} remains explicitly partially_supported.",
        )
    if claim.status is ClaimState.CONTESTED:
        if has_support and has_contradiction:
            return FactReviewClassification.CONTESTED, None
        return (
            FactReviewClassification.CONTESTED,
            f"Contested claim {claim.claim_id} lacks complete authorized opposing evidence.",
        )
    if claim.status is ClaimState.UNSUPPORTED:
        return FactReviewClassification.UNSUPPORTED, None
    if claim.status is ClaimState.OUTDATED:
        return FactReviewClassification.OUTDATED, None
    return (
        FactReviewClassification.MISSING_EVIDENCE,
        f"Claim {claim.claim_id} remains explicitly unreviewed.",
    )


def _normalize(value: str) -> str:
    return " ".join(_NORMALIZE.sub(" ", value.casefold()).split())


def _contains_normalized(haystack: str, needle: str) -> bool:
    return f" {needle} " in f" {haystack} "


def _deduplicate_citations(
    references: Iterable[ResearchBriefCitationReference],
) -> tuple[ResearchBriefCitationReference, ...]:
    values: list[ResearchBriefCitationReference] = []
    seen: set[tuple[str, str]] = set()
    for reference in references:
        key = (reference.claim_id, reference.citation_id)
        if key not in seen:
            seen.add(key)
            values.append(reference)
    return tuple(values)


__all__ = [
    "ContentBriefResearchMaterial",
    "ContentFactFinding",
    "ContentFactReview",
    "ContentResearchIntegrationService",
    "FactReviewClassification",
]
