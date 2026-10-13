"""V3 Phase 8 structured research briefs.

The service assembles persisted claim and citation records into a durable,
provenance-preserving brief.  It intentionally produces no prose synthesis,
truth verdict, candidate, memory, context, skill change, or promotion.
"""

from collections.abc import Callable, Sequence
from typing import Protocol

from app.models.common import Page
from app.models.research import (
    CitationRelation,
    ClaimState,
    ResearchAuthorization,
    ResearchBriefCitationReference,
    ResearchBriefClaimReference,
    ResearchBriefContradiction,
    ResearchBriefDocument,
    ResearchBriefStatus,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
    ResearchRecordStatus,
    ResearchSourceDocument,
    ResearchSourceReference,
    SourceSnapshotDocument,
)
from app.repositories.errors import DuplicateDocumentError
from app.research.errors import (
    ResearchBriefAlreadyExistsError,
    ResearchBriefNotFoundError,
    ResearchProjectNotFoundError,
)


class ResearchProjectLookup(Protocol):
    """The research-project boundary inherited by every constructed brief."""

    async def get(self, research_project_id: str) -> ResearchProjectDocument | None: ...


class ResearchClaimStore(Protocol):
    """Read explicit claim state; this service never evaluates that state."""

    async def list(
        self,
        *,
        status: ClaimState | None = ...,
        allowed_project_ids: tuple[str, ...] | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[ResearchClaimDocument]: ...


class ResearchCitationStore(Protocol):
    """Read persisted citation relations without classifying them automatically."""

    async def list(
        self,
        *,
        claim_id: str | None = ...,
        source_id: str | None = ...,
        allowed_project_ids: tuple[str, ...] | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[ResearchCitationDocument]: ...


class ResearchSourceStore(Protocol):
    """Resolve a source only from its owning research-project repository."""

    async def get(self, source_id: str) -> ResearchSourceDocument | None: ...


class SourceSnapshotStore(Protocol):
    """Look up exact immutable observations within one research project."""

    async def get(self, source_id: str, snapshot_id: str) -> SourceSnapshotDocument | None: ...


class ResearchBriefStore(Protocol):
    """Project-scoped durable storage for structured research material."""

    async def create(self, document: ResearchBriefDocument) -> ResearchBriefDocument: ...

    async def get(self, brief_id: str) -> ResearchBriefDocument | None: ...


ResearchClaimRepositoryFactory = Callable[[str], ResearchClaimStore]
ResearchCitationRepositoryFactory = Callable[[str], ResearchCitationStore]
ResearchSourceRepositoryFactory = Callable[[str], ResearchSourceStore]
SourceSnapshotRepositoryFactory = Callable[[str], SourceSnapshotStore]
ResearchBriefRepositoryFactory = Callable[[str], ResearchBriefStore]


class ResearchBriefService:
    """Build explicit, bounded brief structure from authorized persistent evidence."""

    def __init__(
        self,
        projects: ResearchProjectLookup,
        claims: ResearchClaimRepositoryFactory,
        citations: ResearchCitationRepositoryFactory,
        sources: ResearchSourceRepositoryFactory,
        snapshots: SourceSnapshotRepositoryFactory,
        briefs: ResearchBriefRepositoryFactory,
    ) -> None:
        self._projects = projects
        self._claims = claims
        self._citations = citations
        self._sources = sources
        self._snapshots = snapshots
        self._briefs = briefs

    async def build_brief(
        self,
        *,
        research_project_id: str,
        brief_id: str,
        scope: str,
        additional_unknowns: Sequence[str],
        next_questions: Sequence[str],
        status: ResearchBriefStatus,
        created_by: str | None,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> ResearchBriefDocument:
        """Persist structured evidence references without generating a conclusion prose draft."""
        project = await self._require_project(research_project_id)
        claims_page = await self._claims(research_project_id).list(
            status=None,
            allowed_project_ids=allowed_project_ids,
            limit=50,
        )
        claims = tuple(
            claim
            for claim in claims_page.items
            if self._allowed(claim.authorization, allowed_project_ids)
        )
        claim_by_id = {claim.claim_id: claim for claim in claims}
        citations_page = await self._citations(research_project_id).list(
            allowed_project_ids=allowed_project_ids,
            limit=50,
        )
        references, source_refs, stale_unknowns, authorization = await self._citation_structure(
            project=project,
            citations=citations_page.items,
            claims=claim_by_id,
            allowed_project_ids=allowed_project_ids,
            created_by=created_by,
        )
        citations_by_claim: dict[str, list[ResearchBriefCitationReference]] = {}
        for reference in references:
            citations_by_claim.setdefault(reference.claim_id, []).append(reference)
        contradiction_records = self._contradictions(claims, citations_by_claim)
        unknowns = self._unknowns(
            claims=claims,
            citations_by_claim=citations_by_claim,
            stale_unknowns=stale_unknowns,
            additional_unknowns=additional_unknowns,
            paginated=claims_page.next_cursor is not None or citations_page.next_cursor is not None,
        )
        authorization = self._authorization(
            (authorization, *(claim.authorization for claim in claims)), created_by
        )
        supporting = tuple(
            dict.fromkeys(
                reference.citation_id
                for reference in references
                if reference.relation
                in {
                    CitationRelation.SUPPORTS,
                    CitationRelation.QUALIFIES,
                    CitationRelation.BACKGROUND,
                }
            )
        )
        contradicting = tuple(
            dict.fromkeys(
                reference.citation_id
                for reference in references
                if reference.relation is CitationRelation.CONTRADICTS
            )
        )
        document = ResearchBriefDocument(
            research_project_id=research_project_id,
            brief_id=brief_id,
            # These two legacy required fields are deliberately mechanical,
            # not a server-authored research conclusion. Prose belongs to the
            # calling agent after it has inspected this structure.
            title=project.name,
            question=project.question,
            scope=scope,
            summary=(
                "Structured research material only; the calling agent may create prose synthesis. "
                "This brief does not claim completeness."
            ),
            key_findings=[
                claim.statement for claim in claims if claim.status is ClaimState.SUPPORTED
            ],
            evidence_citation_ids=list(supporting),
            contradicting_citation_ids=list(contradicting),
            source_ids=list(dict.fromkeys(reference.source_id for reference in references)),
            claim_refs=[self._claim_reference(claim) for claim in claims],
            citation_refs=list(references),
            contradictions=list(contradiction_records),
            source_refs=list(source_refs),
            unknowns=list(unknowns),
            next_questions=list(dict.fromkeys(next_questions)),
            status=status,
            authorization=authorization,
        )
        try:
            return await self._briefs(research_project_id).create(document)
        except DuplicateDocumentError as error:
            raise ResearchBriefAlreadyExistsError("research brief already exists") from error

    async def get_brief(
        self,
        *,
        research_project_id: str,
        brief_id: str,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> ResearchBriefDocument:
        """Read a brief only when its retained authorization is fully admitted."""
        brief = await self._briefs(research_project_id).get(brief_id)
        if brief is None or not self._allowed(brief.authorization, allowed_project_ids):
            raise ResearchBriefNotFoundError("research brief was not found")
        return brief

    async def _citation_structure(
        self,
        *,
        project: ResearchProjectDocument,
        citations: Sequence[ResearchCitationDocument],
        claims: dict[str, ResearchClaimDocument],
        allowed_project_ids: tuple[str, ...] | None,
        created_by: str | None,
    ) -> tuple[
        tuple[ResearchBriefCitationReference, ...],
        tuple[ResearchSourceReference, ...],
        tuple[str, ...],
        ResearchAuthorization,
    ]:
        """Materialize only citations whose claim, source, and snapshot remain authorized."""
        references: list[ResearchBriefCitationReference] = []
        source_refs: list[ResearchSourceReference] = []
        unknowns: list[str] = []
        authorizations: list[ResearchAuthorization] = [project.authorization]
        for citation in citations:
            claim = claims.get(citation.claim_id)
            if claim is None or not self._allowed(citation.authorization, allowed_project_ids):
                continue
            source = await self._sources(project.research_project_id).get(citation.source_id)
            snapshot = await self._snapshots(project.research_project_id).get(
                citation.source_id, citation.snapshot_id
            )
            if (
                source is None
                or snapshot is None
                or source.research_project_id != project.research_project_id
                or not self._allowed(source.authorization, allowed_project_ids)
                or not self._allowed(snapshot.authorization, allowed_project_ids)
            ):
                unknowns.append(
                    f"Citation {citation.citation_id} lacks an authorized source snapshot."
                )
                continue
            references.append(
                ResearchBriefCitationReference(
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
            )
            source_refs.append(
                ResearchSourceReference(
                    source_id=citation.source_id,
                    snapshot_id=citation.snapshot_id,
                    chunk_id=citation.chunk_id,
                )
            )
            authorizations.extend(
                (
                    claim.authorization,
                    citation.authorization,
                    source.authorization,
                    snapshot.authorization,
                )
            )
            if (
                claim.status is ClaimState.OUTDATED
                or citation.status is not ResearchRecordStatus.ACTIVE
                or source.status is not ResearchRecordStatus.ACTIVE
                or snapshot.status is not ResearchRecordStatus.ACTIVE
            ):
                unknowns.append(
                    f"Citation {citation.citation_id} references stale or non-active evidence."
                )
        unique_source_refs = tuple({item.model_dump_json(): item for item in source_refs}.values())
        return (
            tuple(references),
            unique_source_refs,
            tuple(dict.fromkeys(unknowns)),
            self._authorization(authorizations, created_by),
        )

    @staticmethod
    def _claim_reference(claim: ResearchClaimDocument) -> ResearchBriefClaimReference:
        return ResearchBriefClaimReference(
            claim_id=claim.claim_id,
            statement=claim.statement,
            status=claim.status,
            confidence=claim.confidence,
        )

    @staticmethod
    def _contradictions(
        claims: Sequence[ResearchClaimDocument],
        citations_by_claim: dict[str, list[ResearchBriefCitationReference]],
    ) -> tuple[ResearchBriefContradiction, ...]:
        """Keep contested state and actual opposing citations visible without a winner."""
        records: list[ResearchBriefContradiction] = []
        for claim in claims:
            references = citations_by_claim.get(claim.claim_id, [])
            supporting = [
                item.citation_id
                for item in references
                if item.relation is CitationRelation.SUPPORTS
            ]
            contradicting = [
                item.citation_id
                for item in references
                if item.relation is CitationRelation.CONTRADICTS
            ]
            if claim.status is ClaimState.CONTESTED or (supporting and contradicting):
                records.append(
                    ResearchBriefContradiction(
                        claim_id=claim.claim_id,
                        statement=claim.statement,
                        supporting_citation_ids=supporting,
                        contradicting_citation_ids=contradicting,
                    )
                )
        return tuple(records)

    @staticmethod
    def _unknowns(
        *,
        claims: Sequence[ResearchClaimDocument],
        citations_by_claim: dict[str, list[ResearchBriefCitationReference]],
        stale_unknowns: Sequence[str],
        additional_unknowns: Sequence[str],
        paginated: bool,
    ) -> tuple[str, ...]:
        """State missing/stale evidence explicitly; absence is never a completeness claim."""
        values = list(additional_unknowns)
        if not claims:
            values.append(
                "No authorized claims were available; this brief does not claim completeness."
            )
        if paginated:
            values.append("Brief construction reached its bounded claim or citation page limit.")
        for claim in claims:
            references = citations_by_claim.get(claim.claim_id, [])
            has_support = any(item.relation is CitationRelation.SUPPORTS for item in references)
            has_contradiction = any(
                item.relation is CitationRelation.CONTRADICTS for item in references
            )
            if claim.status is ClaimState.SUPPORTED and not has_support:
                values.append(
                    f"Supported claim {claim.claim_id} has no authorized supporting citation "
                    "in this brief."
                )
            if claim.status is ClaimState.CONTESTED and not (has_support and has_contradiction):
                values.append(
                    f"Contested claim {claim.claim_id} lacks complete opposing citation coverage."
                )
            if claim.status in {
                ClaimState.UNREVIEWED,
                ClaimState.PARTIALLY_SUPPORTED,
                ClaimState.UNSUPPORTED,
                ClaimState.OUTDATED,
            }:
                values.append(f"Claim {claim.claim_id} remains explicitly {claim.status.value}.")
        values.extend(stale_unknowns)
        values.append("This brief is structured evidence material and does not claim completeness.")
        return tuple(dict.fromkeys(values))

    async def _require_project(self, research_project_id: str) -> ResearchProjectDocument:
        project = await self._projects.get(research_project_id)
        if project is None:
            raise ResearchProjectNotFoundError("research project was not found")
        return project

    @staticmethod
    def _authorization(
        authorizations: Sequence[ResearchAuthorization], owner_key_id: str | None
    ) -> ResearchAuthorization:
        project_ids = sorted(
            {project_id for item in authorizations for project_id in item.project_ids}
        )
        return ResearchAuthorization(project_ids=project_ids, owner_key_id=owner_key_id)

    @staticmethod
    def _allowed(
        authorization: ResearchAuthorization, allowed_project_ids: tuple[str, ...] | None
    ) -> bool:
        return allowed_project_ids is None or set(authorization.project_ids).issubset(
            allowed_project_ids
        )


__all__ = ["ResearchBriefService"]
