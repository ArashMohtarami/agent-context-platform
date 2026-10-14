"""V3 Phase 9 review-gated research knowledge candidates.

This service records proposals and explicit review decisions only.  It does not
write V1 memories, project context, skills, content, or any other promotion
target; Phase 10 owns that separate, explicitly requested action.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal, Protocol

from bson import ObjectId

from app.models.common import Page, utc_now
from app.models.freshness import FreshnessClass, RevalidationReason
from app.models.memory import MemoryDocument
from app.models.project import ProjectDocument
from app.models.project_context import ContextType, ProjectContextDocument
from app.models.promotion import PromotionProvenance
from app.models.research import (
    CandidateState,
    CandidateType,
    KnowledgeCandidateDocument,
    PromotionEventDocument,
    ResearchAuthorization,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
)
from app.models.skill import LifecycleStatus
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError
from app.research.errors import (
    CandidatePromotionStateError,
    CandidatePromotionTargetError,
    CandidateProvenanceError,
    CandidateReviewStateError,
    CandidateTargetProjectNotFoundError,
    KnowledgeCandidateAlreadyExistsError,
    KnowledgeCandidateNotFoundError,
    ResearchProjectNotFoundError,
)


class ResearchProjectLookup(Protocol):
    """The project boundary for every candidate and decision."""

    async def get(self, research_project_id: str) -> ResearchProjectDocument | None: ...


class V1ProjectLookup(Protocol):
    """Only validates an explicitly named V1 target; it never writes it."""

    async def get(self, project_id: str) -> ProjectDocument | None: ...


class ResearchClaimStore(Protocol):
    """Read exact candidate provenance; it cannot change claim state."""

    async def get(self, claim_id: str) -> ResearchClaimDocument | None: ...


class ResearchCitationStore(Protocol):
    """Read exact citation provenance inside a submitted source claim."""

    async def get(self, claim_id: str, citation_id: str) -> ResearchCitationDocument | None: ...


class CandidateStore(Protocol):
    """Project-scoped candidate persistence with revision guards."""

    async def create(self, document: KnowledgeCandidateDocument) -> KnowledgeCandidateDocument: ...

    async def get(self, candidate_id: str) -> KnowledgeCandidateDocument | None: ...

    async def list(
        self,
        *,
        status: CandidateState | None = ...,
        target_project_id: str | None = ...,
        allowed_project_ids: tuple[str, ...] | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[KnowledgeCandidateDocument]: ...

    async def update(
        self,
        document: KnowledgeCandidateDocument,
        *,
        expected_revision: int,
    ) -> KnowledgeCandidateDocument: ...


class PromotionEventStore(Protocol):
    """Append-only decision storage; no update or delete operation exists."""

    async def create(self, document: PromotionEventDocument) -> PromotionEventDocument: ...


class MemoryPromotionWriter(Protocol):
    """The existing V1 memory service boundary used by controlled promotion."""

    async def create_memory(
        self,
        *,
        memory_type: str,
        content: str,
        importance: float,
        confidence: float,
        source: str,
        project_id: str | None = ...,
        summary: str | None = ...,
        tags: Sequence[str] = ...,
        external_id: str | None = ...,
        promotion_provenance: PromotionProvenance | None = ...,
    ) -> MemoryDocument: ...


class ContextPromotionWriter(Protocol):
    """The existing V1 context service boundary used by controlled promotion."""

    async def create_context(
        self,
        project_id: str,
        *,
        context_type: ContextType,
        title: str,
        content: str,
        importance: float,
        source: str,
        tags: Sequence[str] = ...,
        external_id: str | None = ...,
        promotion_provenance: PromotionProvenance | None = ...,
    ) -> ProjectContextDocument: ...

    async def get_context(
        self, project_id: str, context_id: ObjectId
    ) -> ProjectContextDocument: ...

    async def update_context(
        self,
        project_id: str,
        context_id: ObjectId,
        *,
        expected_revision: int | None = ...,
        status: LifecycleStatus | None = ...,
    ) -> ProjectContextDocument: ...


ResearchClaimRepositoryFactory = Callable[[str], ResearchClaimStore]
ResearchCitationRepositoryFactory = Callable[[str], ResearchCitationStore]
CandidateRepositoryFactory = Callable[[str], CandidateStore]
PromotionEventRepositoryFactory = Callable[[str], PromotionEventStore]


@dataclass(frozen=True)
class CandidatePromotion:
    """One approved candidate, its V1 target, and immutable promotion event."""

    candidate: KnowledgeCandidateDocument
    event: PromotionEventDocument
    target_type: Literal["memory", "project_context"]
    target_id: str
    provenance: PromotionProvenance


@dataclass(frozen=True)
class CandidateReview:
    """The candidate revision and its immutable decision record."""

    candidate: KnowledgeCandidateDocument
    event: PromotionEventDocument


class KnowledgeCandidateService:
    """Create, review, and explicitly promote candidates through V1 services."""

    def __init__(
        self,
        projects: ResearchProjectLookup,
        v1_projects: V1ProjectLookup,
        claims: ResearchClaimRepositoryFactory,
        citations: ResearchCitationRepositoryFactory,
        candidates: CandidateRepositoryFactory,
        events: PromotionEventRepositoryFactory,
        memories: MemoryPromotionWriter | None = None,
        contexts: ContextPromotionWriter | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._projects = projects
        self._v1_projects = v1_projects
        self._claims = claims
        self._citations = citations
        self._candidates = candidates
        self._events = events
        self._memories = memories
        self._contexts = contexts
        self._clock = clock

    async def create_candidate(
        self,
        *,
        research_project_id: str,
        candidate_id: str,
        candidate_type: CandidateType,
        target_project_id: str | None,
        target_skill_id: str | None,
        current_skill_version: str | None,
        title: str,
        content: str,
        source_claim_ids: Sequence[str],
        source_citation_ids: Sequence[str],
        confidence: float | None,
        confidence_rationale: str | None,
        freshness_class: FreshnessClass = FreshnessClass.STABLE,
        review_after: datetime | None = None,
        revalidation_of_candidate_id: str | None = None,
        revalidation_reasons: Sequence[RevalidationReason] = (),
        created_by: str | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
    ) -> KnowledgeCandidateDocument:
        """Store a pending proposal after validating its retained research provenance."""
        project = await self._require_research_project(research_project_id)
        if target_project_id is not None and await self._v1_projects.get(target_project_id) is None:
            raise CandidateTargetProjectNotFoundError("candidate target project was not found")
        provenance_authorizations = await self._validate_provenance(
            research_project_id=research_project_id,
            source_claim_ids=source_claim_ids,
            source_citation_ids=source_citation_ids,
            allowed_project_ids=allowed_project_ids,
        )
        authorization = self._authorization(
            [project.authorization, *provenance_authorizations],
            target_project_id=target_project_id,
            owner_key_id=created_by,
        )
        document = KnowledgeCandidateDocument(
            research_project_id=research_project_id,
            candidate_id=candidate_id,
            candidate_type=candidate_type,
            target_project_id=target_project_id,
            target_skill_id=target_skill_id,
            current_skill_version=current_skill_version,
            title=title,
            content=content,
            source_claim_ids=list(source_claim_ids),
            source_citation_ids=list(source_citation_ids),
            confidence=confidence,
            confidence_rationale=confidence_rationale,
            freshness_class=freshness_class,
            review_after=review_after,
            revalidation_of_candidate_id=revalidation_of_candidate_id,
            revalidation_reasons=list(revalidation_reasons),
            status=CandidateState.PENDING_REVIEW,
            authorization=authorization,
        )
        try:
            return await self._candidates(research_project_id).create(document)
        except DuplicateDocumentError as error:
            raise KnowledgeCandidateAlreadyExistsError(
                "knowledge candidate already exists"
            ) from error

    async def get_candidate(
        self,
        *,
        research_project_id: str,
        candidate_id: str,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> KnowledgeCandidateDocument:
        """Read one candidate only under its retained complete V1 project grant."""
        candidate = await self._candidates(research_project_id).get(candidate_id)
        if candidate is None or not self._allowed(candidate.authorization, allowed_project_ids):
            raise KnowledgeCandidateNotFoundError("knowledge candidate was not found")
        return candidate

    async def list_candidates(
        self,
        *,
        research_project_id: str,
        status: CandidateState | None,
        target_project_id: str | None,
        allowed_project_ids: tuple[str, ...] | None,
        limit: int,
        cursor: str | None,
    ) -> Page[KnowledgeCandidateDocument]:
        """Page candidates after database-level complete-grant filtering."""
        return await self._candidates(research_project_id).list(
            status=status,
            target_project_id=target_project_id,
            allowed_project_ids=allowed_project_ids,
            limit=limit,
            cursor=cursor,
        )

    async def review_candidate(
        self,
        *,
        research_project_id: str,
        candidate_id: str,
        expected_revision: int,
        decision: CandidateState,
        reviewer: str | None,
        reason: str | None,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> CandidateReview:
        """Record one explicit non-promotion review and append its immutable event."""
        if decision not in {
            CandidateState.APPROVED,
            CandidateState.REJECTED,
            CandidateState.NEEDS_CHANGES,
        }:
            raise CandidateReviewStateError(
                "candidate decision must be approve, reject, or needs_changes"
            )
        candidate = await self.get_candidate(
            research_project_id=research_project_id,
            candidate_id=candidate_id,
            allowed_project_ids=allowed_project_ids,
        )
        if candidate.revision != expected_revision:
            raise ConcurrencyConflictError("knowledge candidate changed after it was read")
        if candidate.status is not CandidateState.PENDING_REVIEW:
            raise CandidateReviewStateError("only pending_review candidates can be reviewed")
        if reviewer is None:
            raise CandidateReviewStateError("an authenticated reviewer is required")
        reviewed_at = self._clock()
        payload = candidate.model_dump(by_alias=True, mode="python")
        payload.update(
            {
                "status": decision,
                "reviewer": reviewer,
                "reviewed_at": reviewed_at,
                "review_notes": reason,
            }
        )
        reviewed = KnowledgeCandidateDocument.model_validate(
            payload, context={"from_storage": True}
        )
        updated = await self._candidates(research_project_id).update(
            reviewed,
            expected_revision=expected_revision,
        )
        event = PromotionEventDocument(
            research_project_id=research_project_id,
            candidate_id=updated.candidate_id,
            event_id=self._event_id(updated.candidate_id, expected_revision, decision),
            target_type=updated.candidate_type,
            target_id=updated.target_project_id or updated.target_skill_id,
            reviewer=reviewer,
            decision=decision,
            decided_at=reviewed_at,
            reason=reason,
            authorization=ResearchAuthorization(
                project_ids=list(updated.authorization.project_ids),
                owner_key_id=reviewer,
            ),
        )
        try:
            stored_event = await self._events(research_project_id).create(event)
        except DuplicateDocumentError as error:
            raise CandidateReviewStateError(
                "candidate review decision is already recorded"
            ) from error
        return CandidateReview(candidate=updated, event=stored_event)

    async def promote_candidate(
        self,
        *,
        research_project_id: str,
        candidate_id: str,
        expected_revision: int,
        reviewer: str | None,
        allowed_project_ids: tuple[str, ...] | None,
        supersedes_context_id: str | None = None,
    ) -> CandidatePromotion:
        """Promote one explicitly approved candidate and retain its evidence IDs.

        The V1 target is created through its own service, so it receives V1
        validation, embeddings, deterministic idempotency, and lifecycle
        behavior. Skills and unsupported generic targets are deliberately not
        adaptable here.
        """
        candidate = await self.get_candidate(
            research_project_id=research_project_id,
            candidate_id=candidate_id,
            allowed_project_ids=allowed_project_ids,
        )
        if candidate.revision != expected_revision:
            raise ConcurrencyConflictError("knowledge candidate changed after it was read")
        if candidate.status is CandidateState.PROMOTED:
            raise CandidatePromotionStateError("candidate was already promoted")
        if candidate.status is not CandidateState.APPROVED:
            raise CandidatePromotionStateError("only approved candidates can be promoted")
        if reviewer is None:
            raise CandidatePromotionStateError("an authenticated promoter is required")
        verified_at = candidate.reviewed_at
        if verified_at is None:
            raise CandidatePromotionStateError("approved candidates require a verified review time")

        provenance = await self._promotion_provenance(
            candidate=candidate,
            allowed_project_ids=allowed_project_ids,
            verified_at=verified_at,
        )
        target_type: Literal["memory", "project_context"]
        target_id: str
        if candidate.candidate_type is CandidateType.MEMORY:
            if supersedes_context_id is not None:
                raise CandidatePromotionTargetError(
                    "only project-context promotion may supersede project context"
                )
            if self._memories is None:
                raise CandidatePromotionTargetError("V1 memory promotion is not configured")
            memory = await self._memories.create_memory(
                memory_type="research_finding",
                content=candidate.content,
                importance=candidate.confidence if candidate.confidence is not None else 0.5,
                confidence=candidate.confidence if candidate.confidence is not None else 0.5,
                source="research-promotion",
                project_id=candidate.target_project_id,
                summary=candidate.title,
                tags=("research", "promoted"),
                external_id=self._target_external_id(candidate, "memory"),
                promotion_provenance=provenance,
            )
            target_type = "memory"
            target_id = str(memory.id)
        elif candidate.candidate_type in {
            CandidateType.PROJECT_CONTEXT,
            CandidateType.ARCHITECTURE_DECISION,
        }:
            if candidate.target_project_id is None:
                raise CandidatePromotionTargetError(
                    "project-context promotion requires target_project_id"
                )
            if self._contexts is None:
                raise CandidatePromotionTargetError(
                    "V1 project-context promotion is not configured"
                )
            previous: ProjectContextDocument | None = None
            if supersedes_context_id is not None:
                previous = await self._contexts.get_context(
                    candidate.target_project_id, ObjectId(supersedes_context_id)
                )
                if previous.status != "active":
                    raise CandidatePromotionStateError(
                        "only active project context may be superseded"
                    )
            context = await self._contexts.create_context(
                candidate.target_project_id,
                context_type=(
                    "architecture_decision"
                    if candidate.candidate_type is CandidateType.ARCHITECTURE_DECISION
                    else "implementation"
                ),
                title=candidate.title,
                content=candidate.content,
                importance=candidate.confidence if candidate.confidence is not None else 0.5,
                source="research-promotion",
                tags=("research", "promoted"),
                external_id=self._target_external_id(candidate, "project_context"),
                promotion_provenance=provenance,
            )
            if previous is not None:
                await self._contexts.update_context(
                    candidate.target_project_id,
                    previous.id,
                    expected_revision=previous.revision,
                    status="superseded",
                )
            target_type = "project_context"
            target_id = str(context.id)
        else:
            raise CandidatePromotionTargetError(
                "Phase 10 supports only memory and project-context promotion; skills and "
                "generic knowledge targets remain unchanged"
            )

        # Do not violate document monotonicity if an injected/deployed clock is
        # behind a pre-existing candidate timestamp.
        promoted_at = max(self._clock(), candidate.updated_at)
        payload = candidate.model_dump(by_alias=True, mode="python")
        payload.update({"status": CandidateState.PROMOTED, "updated_at": promoted_at})
        promoted = KnowledgeCandidateDocument.model_validate(
            payload, context={"from_storage": True}
        )
        updated = await self._candidates(research_project_id).update(
            promoted,
            expected_revision=expected_revision,
        )
        event = PromotionEventDocument(
            research_project_id=research_project_id,
            candidate_id=updated.candidate_id,
            event_id=self._promotion_event_id(updated.candidate_id, expected_revision, target_id),
            target_type=updated.candidate_type,
            target_id=target_id,
            reviewer=reviewer,
            decision=CandidateState.PROMOTED,
            decided_at=promoted_at,
            reason="approved candidate promoted through controlled V3 workflow",
            authorization=ResearchAuthorization(
                project_ids=list(updated.authorization.project_ids), owner_key_id=reviewer
            ),
        )
        try:
            stored_event = await self._events(research_project_id).create(event)
        except DuplicateDocumentError as error:
            raise CandidatePromotionStateError("candidate promotion is already recorded") from error
        return CandidatePromotion(
            candidate=updated,
            event=stored_event,
            target_type=target_type,
            target_id=target_id,
            provenance=provenance,
        )

    async def _promotion_provenance(
        self,
        *,
        candidate: KnowledgeCandidateDocument,
        allowed_project_ids: tuple[str, ...] | None,
        verified_at: datetime,
    ) -> PromotionProvenance:
        """Re-read exact approved evidence to retain source IDs with the V1 target."""
        for claim_id in candidate.source_claim_ids:
            claim = await self._claims(candidate.research_project_id).get(claim_id)
            if claim is None or not self._allowed(claim.authorization, allowed_project_ids):
                raise CandidateProvenanceError(
                    "candidate promotion claim was not found or authorized"
                )
        source_ids: set[str] = set()
        source_snapshot_ids: set[str] = set()
        for citation_id in candidate.source_citation_ids:
            matches: list[ResearchCitationDocument] = []
            for claim_id in candidate.source_claim_ids:
                citation = await self._citations(candidate.research_project_id).get(
                    claim_id, citation_id
                )
                if citation is not None:
                    matches.append(citation)
            if len(matches) != 1 or not self._allowed(
                matches[0].authorization, allowed_project_ids
            ):
                raise CandidateProvenanceError(
                    "candidate promotion citation was not found or authorized"
                )
            source_ids.add(matches[0].source_id)
            source_snapshot_ids.add(matches[0].snapshot_id)
        return PromotionProvenance(
            candidate_id=candidate.candidate_id,
            research_project_id=candidate.research_project_id,
            claim_ids=list(candidate.source_claim_ids),
            citation_ids=list(candidate.source_citation_ids),
            source_ids=sorted(source_ids),
            source_snapshot_ids=sorted(source_snapshot_ids),
            verified_at=verified_at,
            review_after=candidate.review_after,
            freshness_class=candidate.freshness_class,
        )

    async def _validate_provenance(
        self,
        *,
        research_project_id: str,
        source_claim_ids: Sequence[str],
        source_citation_ids: Sequence[str],
        allowed_project_ids: tuple[str, ...] | None,
    ) -> tuple[ResearchAuthorization, ...]:
        """Require exact, project-local, authorization-admitted research records."""
        if not source_claim_ids:
            raise CandidateProvenanceError("knowledge candidates require at least one source claim")
        if len(source_claim_ids) != len(set(source_claim_ids)):
            raise CandidateProvenanceError("candidate source claim identifiers must be unique")
        if len(source_citation_ids) != len(set(source_citation_ids)):
            raise CandidateProvenanceError("candidate source citation identifiers must be unique")
        claims: list[ResearchClaimDocument] = []
        authorizations: list[ResearchAuthorization] = []
        for claim_id in source_claim_ids:
            claim = await self._claims(research_project_id).get(claim_id)
            if (
                claim is None
                or claim.research_project_id != research_project_id
                or not self._allowed(claim.authorization, allowed_project_ids)
            ):
                raise CandidateProvenanceError("candidate source claim was not found or authorized")
            claims.append(claim)
            authorizations.append(claim.authorization)
        for citation_id in source_citation_ids:
            matches: list[ResearchCitationDocument] = []
            for claim in claims:
                citation = await self._citations(research_project_id).get(
                    claim.claim_id, citation_id
                )
                if citation is not None:
                    matches.append(citation)
            if len(matches) != 1 or not self._allowed(
                matches[0].authorization, allowed_project_ids
            ):
                raise CandidateProvenanceError(
                    "candidate source citation must belong to exactly one authorized source claim"
                )
            authorizations.append(matches[0].authorization)
        return tuple(authorizations)

    async def _require_research_project(self, research_project_id: str) -> ResearchProjectDocument:
        project = await self._projects.get(research_project_id)
        if project is None:
            raise ResearchProjectNotFoundError("research project was not found")
        return project

    @staticmethod
    def _authorization(
        authorizations: Sequence[ResearchAuthorization],
        *,
        target_project_id: str | None,
        owner_key_id: str | None,
    ) -> ResearchAuthorization:
        project_ids = {project_id for item in authorizations for project_id in item.project_ids}
        if target_project_id is not None:
            project_ids.add(target_project_id)
        return ResearchAuthorization(project_ids=sorted(project_ids), owner_key_id=owner_key_id)

    @staticmethod
    def _allowed(
        authorization: ResearchAuthorization, allowed_project_ids: tuple[str, ...] | None
    ) -> bool:
        return allowed_project_ids is None or set(authorization.project_ids).issubset(
            allowed_project_ids
        )

    @staticmethod
    def _target_external_id(candidate: KnowledgeCandidateDocument, target_type: str) -> str:
        """Bound the V1 idempotency key while retaining exact IDs in provenance."""
        digest = sha256(
            f"{candidate.research_project_id}:{candidate.candidate_id}:{target_type}".encode()
        ).hexdigest()[:32]
        return f"research-promotion-{digest}"

    @staticmethod
    def _promotion_event_id(candidate_id: str, revision: int, target_id: str) -> str:
        """Generate one immutable event identity for this target transition."""
        digest = sha256(f"{candidate_id}:{revision}:{target_id}:promoted".encode()).hexdigest()[:32]
        return f"promote-{digest}"

    @staticmethod
    def _event_id(candidate_id: str, revision: int, decision: CandidateState) -> str:
        """Generate a bounded stable idempotency key for one review transition."""
        digest = sha256(f"{candidate_id}:{revision}:{decision.value}".encode()).hexdigest()[:32]
        return f"review-{digest}"


__all__ = ["CandidatePromotion", "CandidateReview", "KnowledgeCandidateService"]
