"""V3 Phase 14 advisory freshness and revalidation workflow.

The service only compares immutable snapshots and creates an explicit pending
knowledge candidate. It never invalidates, edits, archives, supersedes, or
promotes a V1 memory/context record.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Literal, Protocol

from bson import ObjectId

from app.models.common import Page, utc_now
from app.models.freshness import FreshnessClass, RevalidationReason
from app.models.memory import MemoryDocument
from app.models.project_context import ProjectContextDocument
from app.models.promotion import PromotionProvenance
from app.models.research import (
    CandidateState,
    CandidateType,
    KnowledgeCandidateDocument,
    PromotionEventDocument,
    ResearchAuthorization,
    ResearchCitationDocument,
    ResearchProjectDocument,
    SourceSnapshotDocument,
)
from app.research.errors import (
    KnowledgeCandidateAlreadyExistsError,
    KnowledgeCandidateNotFoundError,
    ResearchProjectNotFoundError,
    RevalidationNotNeededError,
)

_MAX_PAGE_SIZE = 50


class ResearchProjectLookup(Protocol):
    async def get(self, research_project_id: str) -> ResearchProjectDocument | None: ...


class CandidateStore(Protocol):
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


class PromotionEventStore(Protocol):
    async def list(
        self,
        *,
        candidate_id: str | None = ...,
        allowed_project_ids: tuple[str, ...] | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[PromotionEventDocument]: ...


class CitationStore(Protocol):
    async def get(self, claim_id: str, citation_id: str) -> ResearchCitationDocument | None: ...


class SnapshotStore(Protocol):
    async def get(self, source_id: str, snapshot_id: str) -> SourceSnapshotDocument | None: ...

    async def latest_for_source(self, source_id: str) -> SourceSnapshotDocument | None: ...


class MemoryReader(Protocol):
    async def get(self, document_id: ObjectId) -> MemoryDocument | None: ...


class ContextReader(Protocol):
    async def get(self, document_id: ObjectId) -> ProjectContextDocument | None: ...


class RevalidationCandidateWriter(Protocol):
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
        freshness_class: FreshnessClass = ...,
        review_after: datetime | None = ...,
        revalidation_of_candidate_id: str | None = ...,
        revalidation_reasons: Sequence[RevalidationReason] = ...,
        created_by: str | None = ...,
        allowed_project_ids: tuple[str, ...] | None = ...,
    ) -> KnowledgeCandidateDocument: ...


CandidateRepositoryFactory = Callable[[str], CandidateStore]
PromotionEventRepositoryFactory = Callable[[str], PromotionEventStore]
CitationRepositoryFactory = Callable[[str], CitationStore]
SnapshotRepositoryFactory = Callable[[str], SnapshotStore]
MemoryRepositoryFactory = Callable[[str | None], MemoryReader]
ContextRepositoryFactory = Callable[[str], ContextReader]


@dataclass(frozen=True, slots=True)
class SourceChange:
    """A comparison of the pinned and newest immutable source observations."""

    source_id: str
    baseline_snapshot_id: str | None
    observed_snapshot_id: str | None
    reasons: tuple[RevalidationReason, ...]


@dataclass(frozen=True, slots=True)
class StaleKnowledge:
    """Potentially stale promoted knowledge; never an invalidation verdict."""

    research_project_id: str
    candidate_id: str
    target_type: Literal["memory", "project_context"]
    target_id: str
    title: str
    verified_at: datetime
    source_snapshot_ids: tuple[str, ...]
    review_after: datetime | None
    freshness_class: FreshnessClass
    reasons: tuple[RevalidationReason, ...]
    source_changes: tuple[SourceChange, ...]


@dataclass(frozen=True, slots=True)
class StaleKnowledgePage:
    """A candidate-keyset page, preserving V3 pagination semantics."""

    items: tuple[StaleKnowledge, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class RevalidationProposal:
    """An explicit pending candidate resulting from advisory staleness detection."""

    candidate: KnowledgeCandidateDocument
    stale_knowledge: StaleKnowledge


class RevalidationService:
    """Compare pinned source snapshots and propose, but never apply, updates."""

    def __init__(
        self,
        projects: ResearchProjectLookup,
        candidates: CandidateRepositoryFactory,
        events: PromotionEventRepositoryFactory,
        citations: CitationRepositoryFactory,
        snapshots: SnapshotRepositoryFactory,
        memories: MemoryRepositoryFactory,
        contexts: ContextRepositoryFactory,
        candidate_writer: RevalidationCandidateWriter,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._projects = projects
        self._candidates = candidates
        self._events = events
        self._citations = citations
        self._snapshots = snapshots
        self._memories = memories
        self._contexts = contexts
        self._candidate_writer = candidate_writer
        self._clock = clock

    async def list_stale_knowledge(
        self,
        *,
        research_project_id: str,
        allowed_project_ids: tuple[str, ...] | None,
        as_of: datetime | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> StaleKnowledgePage:
        """List only promoted records with an observable review or source-change signal."""
        await self._require_project(research_project_id, allowed_project_ids)
        page = await self._candidates(research_project_id).list(
            status=CandidateState.PROMOTED,
            allowed_project_ids=allowed_project_ids,
            limit=limit,
            cursor=cursor,
        )
        now = as_of if as_of is not None else self._clock()
        items: list[StaleKnowledge] = []
        for candidate in page.items:
            stale = await self._inspect_candidate(
                candidate=candidate,
                allowed_project_ids=allowed_project_ids,
                as_of=now,
            )
            if stale is not None and stale.reasons:
                items.append(stale)
        return StaleKnowledgePage(items=tuple(items), next_cursor=page.next_cursor)

    async def propose_revalidation(
        self,
        *,
        research_project_id: str,
        candidate_id: str,
        created_by: str | None,
        allowed_project_ids: tuple[str, ...] | None,
        as_of: datetime | None = None,
    ) -> RevalidationProposal:
        """Create one deterministic pending candidate; never modify its V1 predecessor."""
        await self._require_project(research_project_id, allowed_project_ids)
        original = await self._candidates(research_project_id).get(candidate_id)
        if original is None or not _allowed(original.authorization, allowed_project_ids):
            raise KnowledgeCandidateNotFoundError("promoted knowledge candidate was not found")
        if original.status is not CandidateState.PROMOTED:
            raise RevalidationNotNeededError("only promoted research knowledge can be revalidated")
        now = as_of if as_of is not None else self._clock()
        stale = await self._inspect_candidate(
            candidate=original,
            allowed_project_ids=allowed_project_ids,
            as_of=now,
        )
        if stale is None or not stale.reasons:
            raise RevalidationNotNeededError(
                "no review date or source change requires revalidation"
            )
        new_id = _revalidation_candidate_id(stale)
        try:
            candidate = await self._candidate_writer.create_candidate(
                research_project_id=research_project_id,
                candidate_id=new_id,
                candidate_type=original.candidate_type,
                target_project_id=original.target_project_id,
                target_skill_id=original.target_skill_id,
                current_skill_version=original.current_skill_version,
                title=f"Revalidation: {original.title}",
                content=original.content,
                source_claim_ids=original.source_claim_ids,
                source_citation_ids=original.source_citation_ids,
                confidence=original.confidence,
                confidence_rationale=(
                    "A source snapshot or explicit review date requires human revalidation; "
                    "this does not invalidate the promoted knowledge."
                ),
                freshness_class=stale.freshness_class,
                review_after=_renew_review_after(stale, now),
                revalidation_of_candidate_id=original.candidate_id,
                revalidation_reasons=stale.reasons,
                created_by=created_by,
                allowed_project_ids=allowed_project_ids,
            )
        except KnowledgeCandidateAlreadyExistsError:
            existing = await self._candidates(research_project_id).get(new_id)
            if existing is None or not _allowed(existing.authorization, allowed_project_ids):
                raise
            candidate = existing
        return RevalidationProposal(candidate=candidate, stale_knowledge=stale)

    async def _require_project(
        self, research_project_id: str, allowed_project_ids: tuple[str, ...] | None
    ) -> ResearchProjectDocument:
        project = await self._projects.get(research_project_id)
        if project is None or not _allowed(project.authorization, allowed_project_ids):
            raise ResearchProjectNotFoundError("research project was not found")
        return project

    async def _inspect_candidate(
        self,
        *,
        candidate: KnowledgeCandidateDocument,
        allowed_project_ids: tuple[str, ...] | None,
        as_of: datetime,
    ) -> StaleKnowledge | None:
        event = await self._promotion_event(candidate, allowed_project_ids)
        if event is None or event.target_id is None:
            return None
        target = await self._target(candidate, event.target_id)
        if target is None or target.promotion_provenance is None:
            return None
        provenance = target.promotion_provenance
        if provenance.research_project_id != candidate.research_project_id:
            return None
        changes = await self._source_changes(candidate, provenance, allowed_project_ids)
        reasons: list[RevalidationReason] = []
        if provenance.review_after is not None and provenance.review_after <= as_of:
            reasons.append(RevalidationReason.REVIEW_DUE)
        for change in changes:
            reasons.extend(change.reasons)
        return StaleKnowledge(
            research_project_id=candidate.research_project_id,
            candidate_id=candidate.candidate_id,
            target_type=(
                "memory" if candidate.candidate_type is CandidateType.MEMORY else "project_context"
            ),
            target_id=event.target_id,
            title=_target_title(target),
            verified_at=provenance.verified_at,
            source_snapshot_ids=tuple(provenance.source_snapshot_ids),
            review_after=provenance.review_after,
            freshness_class=provenance.freshness_class,
            reasons=tuple(dict.fromkeys(reasons)),
            source_changes=tuple(changes),
        )

    async def _promotion_event(
        self, candidate: KnowledgeCandidateDocument, allowed_project_ids: tuple[str, ...] | None
    ) -> PromotionEventDocument | None:
        page = await self._events(candidate.research_project_id).list(
            candidate_id=candidate.candidate_id,
            allowed_project_ids=allowed_project_ids,
            limit=_MAX_PAGE_SIZE,
        )
        promoted = [item for item in page.items if item.decision is CandidateState.PROMOTED]
        return max(promoted, key=lambda item: item.decided_at) if promoted else None

    async def _target(
        self, candidate: KnowledgeCandidateDocument, target_id: str
    ) -> MemoryDocument | ProjectContextDocument | None:
        if not ObjectId.is_valid(target_id):
            return None
        if candidate.candidate_type is CandidateType.MEMORY:
            return await self._memories(candidate.target_project_id).get(ObjectId(target_id))
        if (
            candidate.candidate_type
            in {
                CandidateType.PROJECT_CONTEXT,
                CandidateType.ARCHITECTURE_DECISION,
            }
            and candidate.target_project_id is not None
        ):
            return await self._contexts(candidate.target_project_id).get(ObjectId(target_id))
        return None

    async def _source_changes(
        self,
        candidate: KnowledgeCandidateDocument,
        provenance: PromotionProvenance,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> tuple[SourceChange, ...]:
        pairs = await self._citation_snapshot_pairs(candidate, provenance, allowed_project_ids)
        changes: list[SourceChange] = []
        for source_id in provenance.source_ids:
            baseline_ids = pairs.get(source_id, set())
            baseline = await self._baseline_snapshot(
                candidate.research_project_id, source_id, baseline_ids, allowed_project_ids
            )
            latest = await self._snapshots(candidate.research_project_id).latest_for_source(
                source_id
            )
            if latest is not None and not _allowed(latest.authorization, allowed_project_ids):
                latest = None
            if baseline is None:
                changes.append(
                    SourceChange(
                        source_id=source_id,
                        baseline_snapshot_id=None,
                        observed_snapshot_id=None if latest is None else latest.snapshot_id,
                        reasons=(RevalidationReason.BASELINE_SNAPSHOT_UNAVAILABLE,),
                    )
                )
                continue
            if latest is None or latest.snapshot_id == baseline.snapshot_id:
                continue
            reasons = _changed_fields(baseline, latest)
            if reasons:
                changes.append(
                    SourceChange(
                        source_id=source_id,
                        baseline_snapshot_id=baseline.snapshot_id,
                        observed_snapshot_id=latest.snapshot_id,
                        reasons=reasons,
                    )
                )
        return tuple(changes)

    async def _citation_snapshot_pairs(
        self,
        candidate: KnowledgeCandidateDocument,
        provenance: PromotionProvenance,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> dict[str, set[str]]:
        pairs: dict[str, set[str]] = {}
        for citation_id in candidate.source_citation_ids:
            matches: list[ResearchCitationDocument] = []
            for claim_id in candidate.source_claim_ids:
                citation = await self._citations(candidate.research_project_id).get(
                    claim_id, citation_id
                )
                if citation is not None:
                    matches.append(citation)
            if (
                len(matches) == 1
                and matches[0].snapshot_id in provenance.source_snapshot_ids
                and _allowed(matches[0].authorization, allowed_project_ids)
            ):
                pairs.setdefault(matches[0].source_id, set()).add(matches[0].snapshot_id)
        return pairs

    async def _baseline_snapshot(
        self,
        research_project_id: str,
        source_id: str,
        snapshot_ids: set[str],
        allowed_project_ids: tuple[str, ...] | None,
    ) -> SourceSnapshotDocument | None:
        snapshots: list[SourceSnapshotDocument] = []
        for snapshot_id in snapshot_ids:
            snapshot = await self._snapshots(research_project_id).get(source_id, snapshot_id)
            if snapshot is not None and _allowed(snapshot.authorization, allowed_project_ids):
                snapshots.append(snapshot)
        return max(snapshots, key=lambda item: item.retrieved_at) if snapshots else None


def _allowed(
    authorization: ResearchAuthorization, allowed_project_ids: tuple[str, ...] | None
) -> bool:
    return allowed_project_ids is None or set(authorization.project_ids).issubset(
        allowed_project_ids
    )


def _target_title(target: MemoryDocument | ProjectContextDocument) -> str:
    if isinstance(target, MemoryDocument):
        return target.summary or target.content[:500]
    return target.title


def _metadata_value(snapshot: SourceSnapshotDocument, field: str) -> str | None:
    value = snapshot.metadata.get(field)
    return value if isinstance(value, str) else None


def _changed_fields(
    baseline: SourceSnapshotDocument, latest: SourceSnapshotDocument
) -> tuple[RevalidationReason, ...]:
    reasons: list[RevalidationReason] = []
    if baseline.content_hash != latest.content_hash:
        reasons.append(RevalidationReason.CONTENT_HASH_CHANGED)
    if _metadata_value(baseline, "etag") != _metadata_value(latest, "etag"):
        reasons.append(RevalidationReason.ETAG_CHANGED)
    if _metadata_value(baseline, "last_modified") != _metadata_value(latest, "last_modified"):
        reasons.append(RevalidationReason.LAST_MODIFIED_CHANGED)
    if baseline.version_identifier != latest.version_identifier:
        reasons.append(RevalidationReason.VERSION_IDENTIFIER_CHANGED)
    baseline_commit = _metadata_value(baseline, "repository_commit") or _metadata_value(
        baseline, "commit"
    )
    latest_commit = _metadata_value(latest, "repository_commit") or _metadata_value(
        latest, "commit"
    )
    if baseline_commit != latest_commit:
        reasons.append(RevalidationReason.REPOSITORY_COMMIT_CHANGED)
    return tuple(reasons)


def _revalidation_candidate_id(stale: StaleKnowledge) -> str:
    material = "\x00".join(
        (
            stale.research_project_id,
            stale.candidate_id,
            *stale.source_snapshot_ids,
            *(reason.value for reason in stale.reasons),
        )
    )
    return f"revalidation-{sha256(material.encode()).hexdigest()[:24]}"


def _renew_review_after(stale: StaleKnowledge, now: datetime) -> datetime | None:
    if stale.review_after is None:
        return None
    interval: timedelta = stale.review_after - stale.verified_at
    return now + interval if interval > timedelta(0) else None


__all__ = [
    "RevalidationProposal",
    "RevalidationService",
    "SourceChange",
    "StaleKnowledge",
    "StaleKnowledgePage",
]
