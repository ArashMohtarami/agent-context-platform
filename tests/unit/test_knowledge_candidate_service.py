"""V3 Phase 9 review and Phase 10 controlled-promotion workflow tests."""

from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.models.common import Page, deterministic_object_id
from app.models.memory import MemoryDocument
from app.models.project_context import ProjectContextDocument
from app.models.research import (
    CandidateState,
    CandidateType,
    CitationLocator,
    CitationRelation,
    ClaimState,
    KnowledgeCandidateDocument,
    PromotionEventDocument,
    ResearchAuthorization,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
    ResearchProjectType,
    SourceClass,
)
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError
from app.research.errors import (
    CandidatePromotionStateError,
    CandidatePromotionTargetError,
    CandidateProvenanceError,
    CandidateReviewStateError,
    CandidateTargetProjectNotFoundError,
)
from app.services.knowledge_candidate_service import KnowledgeCandidateService
from app.services.memory_service import memory_scope
from tests.project_doubles import FakeProjectRepository
from tests.project_doubles import project as v1_project
from tests.research_doubles import FakeResearchProjectRepository

PROJECT = "payments-api"
PRIVATE_PROJECT = "private-ledger"
RESEARCH = "retry-evidence"
WHEN = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def authorization(*project_ids: str) -> ResearchAuthorization:
    return ResearchAuthorization(project_ids=list(project_ids), owner_key_id="writer-key")


def research_project() -> ResearchProjectDocument:
    return ResearchProjectDocument(
        research_project_id=RESEARCH,
        name="Retry evidence",
        question="What constrains safe payment retries?",
        type=ResearchProjectType.TECHNICAL_RESEARCH,
        related_project_ids=[PROJECT],
        authorization=authorization(PROJECT),
    )


def claim(claim_id: str = "retry-key", *, private: bool = False) -> ResearchClaimDocument:
    return ResearchClaimDocument(
        research_project_id=RESEARCH,
        claim_id=claim_id,
        statement="Retryable writes require an idempotency key.",
        status=ClaimState.SUPPORTED,
        confidence=0.9,
        confidence_rationale="An explicit reviewer assessment.",
        authorization=authorization(PRIVATE_PROJECT if private else PROJECT),
    )


def citation(citation_id: str = "provider-support") -> ResearchCitationDocument:
    return ResearchCitationDocument(
        research_project_id=RESEARCH,
        claim_id="retry-key",
        citation_id=citation_id,
        source_id="provider-docs",
        snapshot_id="provider-v1",
        chunk_id="retry-chunk",
        source_class=SourceClass.OFFICIAL_DOCUMENTATION,
        locator=CitationLocator(url="https://docs.example.test/retries", page=2),
        evidence_excerpt="Retries require an idempotency key.",
        relation=CitationRelation.SUPPORTS,
        authorization=authorization(PROJECT),
    )


class Store:
    def __init__(self) -> None:
        self.claims: dict[str, ResearchClaimDocument] = {"retry-key": claim()}
        self.citations: dict[tuple[str, str], ResearchCitationDocument] = {
            ("retry-key", "provider-support"): citation()
        }
        self.candidates: dict[str, KnowledgeCandidateDocument] = {}
        self.events: dict[tuple[str, str], PromotionEventDocument] = {}

    def claim_store(self, research_project_id: str) -> "ClaimStore":
        return ClaimStore(self, research_project_id)

    def citation_store(self, research_project_id: str) -> "CitationStore":
        return CitationStore(self, research_project_id)

    def candidate_store(self, research_project_id: str) -> "CandidateStore":
        return CandidateStore(self, research_project_id)

    def event_store(self, research_project_id: str) -> "EventStore":
        return EventStore(self, research_project_id)


class ClaimStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def get(self, claim_id: str) -> ResearchClaimDocument | None:
        record = self._store.claims.get(claim_id)
        if record is None or record.research_project_id != self._research_project_id:
            return None
        return record


class CitationStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def get(self, claim_id: str, citation_id: str) -> ResearchCitationDocument | None:
        record = self._store.citations.get((claim_id, citation_id))
        if record is None or record.research_project_id != self._research_project_id:
            return None
        return record


class CandidateStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def create(self, document: KnowledgeCandidateDocument) -> KnowledgeCandidateDocument:
        if document.research_project_id != self._research_project_id:
            raise AssertionError("candidate escaped its research project")
        if document.candidate_id in self._store.candidates:
            raise DuplicateDocumentError("candidate exists")
        self._store.candidates[document.candidate_id] = document
        return document

    async def get(self, candidate_id: str) -> KnowledgeCandidateDocument | None:
        record = self._store.candidates.get(candidate_id)
        if record is None or record.research_project_id != self._research_project_id:
            return None
        return record

    async def list(
        self,
        *,
        status: CandidateState | None = None,
        target_project_id: str | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[KnowledgeCandidateDocument]:
        records = [
            item
            for item in sorted(self._store.candidates.values(), key=lambda item: item.candidate_id)
            if item.research_project_id == self._research_project_id
            and (status is None or item.status is status)
            and (target_project_id is None or item.target_project_id == target_project_id)
            and (
                allowed_project_ids is None
                or set(item.authorization.project_ids).issubset(allowed_project_ids)
            )
        ]
        if cursor is not None:
            records = [item for item in records if item.candidate_id > cursor]
        page = tuple(records[:limit])
        next_cursor = page[-1].candidate_id if len(records) > limit and page else None
        return Page(items=page, next_cursor=next_cursor)

    async def update(
        self, document: KnowledgeCandidateDocument, *, expected_revision: int
    ) -> KnowledgeCandidateDocument:
        stored = self._store.candidates.get(document.candidate_id)
        if stored is None or stored.revision != expected_revision:
            raise ConcurrencyConflictError("candidate changed")
        values = document.model_dump(by_alias=True, mode="python")
        values["revision"] = expected_revision + 1
        updated = KnowledgeCandidateDocument.model_validate(values)
        self._store.candidates[updated.candidate_id] = updated
        return updated


class EventStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def create(self, document: PromotionEventDocument) -> PromotionEventDocument:
        if document.research_project_id != self._research_project_id:
            raise AssertionError("event escaped its research project")
        key = (document.candidate_id, document.event_id)
        if key in self._store.events:
            raise DuplicateDocumentError("event exists")
        self._store.events[key] = document
        return document


def service(
    *, memories: object | None = None, contexts: object | None = None
) -> tuple[KnowledgeCandidateService, Store]:
    store = Store()
    return (
        KnowledgeCandidateService(
            FakeResearchProjectRepository([research_project()]),
            FakeProjectRepository([v1_project(PROJECT)]),
            store.claim_store,
            store.citation_store,
            store.candidate_store,
            store.event_store,
            memories=memories,  # type: ignore[arg-type]
            contexts=contexts,  # type: ignore[arg-type]
            clock=lambda: WHEN,
        ),
        store,
    )


async def create_candidate(
    candidate_service: KnowledgeCandidateService,
    candidate_id: str = "retry-context",
) -> KnowledgeCandidateDocument:
    return await candidate_service.create_candidate(
        research_project_id=RESEARCH,
        candidate_id=candidate_id,
        candidate_type=CandidateType.PROJECT_CONTEXT,
        target_project_id=PROJECT,
        target_skill_id=None,
        current_skill_version=None,
        title="Retry idempotency requirement",
        content="Use an idempotency key for retryable provider writes.",
        source_claim_ids=["retry-key"],
        source_citation_ids=["provider-support"],
        confidence=0.9,
        confidence_rationale="The source claim is explicitly supported.",
        created_by="writer-key",
        allowed_project_ids=(PROJECT,),
    )


@pytest.mark.asyncio
async def test_create_pending_candidate_retains_supported_claim_and_citation() -> None:
    candidate_service, store = service()

    candidate = await create_candidate(candidate_service)

    assert candidate.status is CandidateState.PENDING_REVIEW
    assert candidate.reviewer is None
    assert candidate.source_claim_ids == ["retry-key"]
    assert candidate.source_citation_ids == ["provider-support"]
    assert candidate.authorization.project_ids == [PROJECT]
    assert store.events == {}


@pytest.mark.asyncio
async def test_approve_records_immutable_event_without_v1_mutation() -> None:
    candidate_service, store = service()
    candidate = await create_candidate(candidate_service)

    reviewed = await candidate_service.review_candidate(
        research_project_id=RESEARCH,
        candidate_id=candidate.candidate_id,
        expected_revision=candidate.revision,
        decision=CandidateState.APPROVED,
        reviewer="reviewer-key",
        reason="Evidence has been reviewed.",
        allowed_project_ids=(PROJECT,),
    )

    assert reviewed.candidate.status is CandidateState.APPROVED
    assert reviewed.candidate.reviewer == "reviewer-key"
    assert reviewed.candidate.revision == 2
    assert reviewed.candidate.source_claim_ids == ["retry-key"]
    assert reviewed.event.decision is CandidateState.APPROVED
    assert reviewed.event.candidate_id == candidate.candidate_id
    assert len(store.events) == 1
    # Approval alone keeps V1 targets inert; promotion is a separate call.
    assert candidate_service._memories is None
    assert candidate_service._contexts is None
    assert not hasattr(candidate_service, "_skills")


@pytest.mark.asyncio
async def test_reject_and_needs_changes_are_explicit_terminal_review_decisions() -> None:
    candidate_service, store = service()
    rejected = await create_candidate(candidate_service, "retry-reject")
    needs_changes = await create_candidate(candidate_service, "retry-revise")

    rejected_review = await candidate_service.review_candidate(
        research_project_id=RESEARCH,
        candidate_id=rejected.candidate_id,
        expected_revision=rejected.revision,
        decision=CandidateState.REJECTED,
        reviewer="reviewer-key",
        reason="The proposal is too broad.",
        allowed_project_ids=(PROJECT,),
    )
    changes_review = await candidate_service.review_candidate(
        research_project_id=RESEARCH,
        candidate_id=needs_changes.candidate_id,
        expected_revision=needs_changes.revision,
        decision=CandidateState.NEEDS_CHANGES,
        reviewer="reviewer-key",
        reason="Narrow the evidence scope.",
        allowed_project_ids=(PROJECT,),
    )

    assert rejected_review.candidate.status is CandidateState.REJECTED
    assert changes_review.candidate.status is CandidateState.NEEDS_CHANGES
    assert {event.decision for event in store.events.values()} == {
        CandidateState.REJECTED,
        CandidateState.NEEDS_CHANGES,
    }


@pytest.mark.asyncio
async def test_candidate_provenance_and_target_must_be_authorized_and_valid() -> None:
    candidate_service, store = service()
    store.claims["private-claim"] = claim("private-claim", private=True)

    with pytest.raises(CandidateProvenanceError, match="authorized"):
        await candidate_service.create_candidate(
            research_project_id=RESEARCH,
            candidate_id="private-evidence",
            candidate_type=CandidateType.KNOWLEDGE,
            target_project_id=None,
            target_skill_id=None,
            current_skill_version=None,
            title="Private evidence",
            content="This must not be exposed.",
            source_claim_ids=["private-claim"],
            source_citation_ids=[],
            confidence=None,
            confidence_rationale=None,
            created_by="writer-key",
            allowed_project_ids=(PROJECT,),
        )

    with pytest.raises(CandidateTargetProjectNotFoundError):
        await candidate_service.create_candidate(
            research_project_id=RESEARCH,
            candidate_id="unknown-target",
            candidate_type=CandidateType.PROJECT_CONTEXT,
            target_project_id="missing-project",
            target_skill_id=None,
            current_skill_version=None,
            title="Unknown target",
            content="A V1 target must exist.",
            source_claim_ids=["retry-key"],
            source_citation_ids=[],
            confidence=None,
            confidence_rationale=None,
            created_by="writer-key",
            allowed_project_ids=(PROJECT,),
        )


@pytest.mark.asyncio
async def test_listing_filters_preserved_authorization_and_paginates() -> None:
    candidate_service, store = service()
    await create_candidate(candidate_service, "candidate-a")
    await create_candidate(candidate_service, "candidate-b")
    store.candidates["private"] = KnowledgeCandidateDocument(
        research_project_id=RESEARCH,
        candidate_id="private",
        candidate_type=CandidateType.KNOWLEDGE,
        title="Private proposal",
        content="Private retained evidence.",
        source_claim_ids=["retry-key"],
        authorization=authorization(PRIVATE_PROJECT),
    )

    first = await candidate_service.list_candidates(
        research_project_id=RESEARCH,
        status=CandidateState.PENDING_REVIEW,
        target_project_id=None,
        allowed_project_ids=(PROJECT,),
        limit=1,
        cursor=None,
    )
    second = await candidate_service.list_candidates(
        research_project_id=RESEARCH,
        status=CandidateState.PENDING_REVIEW,
        target_project_id=None,
        allowed_project_ids=(PROJECT,),
        limit=1,
        cursor=first.next_cursor,
    )

    assert [item.candidate_id for item in first.items] == ["candidate-a"]
    assert first.next_cursor == "candidate-a"
    assert [item.candidate_id for item in second.items] == ["candidate-b"]


@pytest.mark.asyncio
async def test_review_is_revision_guarded_and_never_accepts_promoted_state() -> None:
    candidate_service, _ = service()
    candidate = await create_candidate(candidate_service)

    with pytest.raises(ConcurrencyConflictError):
        await candidate_service.review_candidate(
            research_project_id=RESEARCH,
            candidate_id=candidate.candidate_id,
            expected_revision=99,
            decision=CandidateState.APPROVED,
            reviewer="reviewer-key",
            reason=None,
            allowed_project_ids=(PROJECT,),
        )
    with pytest.raises(CandidateReviewStateError, match="approve, reject, or needs_changes"):
        await candidate_service.review_candidate(
            research_project_id=RESEARCH,
            candidate_id=candidate.candidate_id,
            expected_revision=candidate.revision,
            decision=CandidateState.PROMOTED,
            reviewer="reviewer-key",
            reason=None,
            allowed_project_ids=(PROJECT,),
        )


class MemoryWriter:
    """Captures calls that must pass through the existing V1 memory service."""

    def __init__(self) -> None:
        self.created: list[MemoryDocument] = []

    async def create_memory(self, **values: object) -> MemoryDocument:
        project_id = values["project_id"]
        assert project_id is None or isinstance(project_id, str)
        document = MemoryDocument(
            scope=memory_scope(project_id),
            project_id=project_id,
            type=values["memory_type"],
            content=values["content"],
            summary=values["summary"],
            importance=values["importance"],
            confidence=values["confidence"],
            source=values["source"],
            tags=list(values["tags"]),
            external_id=values["external_id"],
            promotion_provenance=values["promotion_provenance"],
        )
        self.created.append(document)
        return document


class ContextWriter:
    """Captures V1 context creation and the non-destructive supersession lifecycle."""

    def __init__(self, previous: ProjectContextDocument | None = None) -> None:
        self.created: list[ProjectContextDocument] = []
        self.previous = previous
        self.superseded: list[ObjectId] = []

    async def create_context(self, project_id: str, **values: object) -> ProjectContextDocument:
        document = ProjectContextDocument(
            project_id=project_id,
            type=values["context_type"],
            title=values["title"],
            content=values["content"],
            importance=values["importance"],
            source=values["source"],
            tags=list(values["tags"]),
            external_id=values["external_id"],
            promotion_provenance=values["promotion_provenance"],
        )
        self.created.append(document)
        return document

    async def get_context(self, project_id: str, context_id: ObjectId) -> ProjectContextDocument:
        assert project_id == PROJECT
        assert self.previous is not None and context_id == self.previous.id
        return self.previous

    async def update_context(
        self,
        project_id: str,
        context_id: ObjectId,
        *,
        expected_revision: int | None = None,
        status: str | None = None,
    ) -> ProjectContextDocument:
        assert project_id == PROJECT
        assert self.previous is not None and context_id == self.previous.id
        assert expected_revision == self.previous.revision
        assert status == "superseded"
        self.superseded.append(context_id)
        return self.previous


async def approved_candidate(
    candidate_service: KnowledgeCandidateService,
    *,
    candidate_type: CandidateType = CandidateType.PROJECT_CONTEXT,
    candidate_id: str = "retry-context",
) -> KnowledgeCandidateDocument:
    candidate = await candidate_service.create_candidate(
        research_project_id=RESEARCH,
        candidate_id=candidate_id,
        candidate_type=candidate_type,
        target_project_id=PROJECT,
        target_skill_id=None,
        current_skill_version=None,
        title="Retry idempotency requirement",
        content="Use an idempotency key for retryable provider writes.",
        source_claim_ids=["retry-key"],
        source_citation_ids=["provider-support"],
        confidence=0.9,
        confidence_rationale="The source claim is explicitly supported.",
        created_by="writer-key",
        allowed_project_ids=(PROJECT,),
    )
    return (
        await candidate_service.review_candidate(
            research_project_id=RESEARCH,
            candidate_id=candidate.candidate_id,
            expected_revision=candidate.revision,
            decision=CandidateState.APPROVED,
            reviewer="reviewer-key",
            reason="Evidence has been reviewed.",
            allowed_project_ids=(PROJECT,),
        )
    ).candidate


@pytest.mark.asyncio
async def test_promote_memory_reuses_v1_writer_and_retains_traceable_provenance() -> None:
    memories = MemoryWriter()
    candidate_service, store = service(memories=memories)
    candidate = await approved_candidate(
        candidate_service, candidate_type=CandidateType.MEMORY, candidate_id="retry-memory"
    )

    promoted = await candidate_service.promote_candidate(
        research_project_id=RESEARCH,
        candidate_id=candidate.candidate_id,
        expected_revision=candidate.revision,
        reviewer="promoter-key",
        allowed_project_ids=(PROJECT,),
    )

    assert promoted.candidate.status is CandidateState.PROMOTED
    assert promoted.target_type == "memory"
    assert promoted.event.decision is CandidateState.PROMOTED
    assert len(store.events) == 2
    memory = memories.created[0]
    assert memory.promotion_provenance is not None
    assert memory.promotion_provenance.candidate_id == "retry-memory"
    assert memory.promotion_provenance.claim_ids == ["retry-key"]
    assert memory.promotion_provenance.citation_ids == ["provider-support"]
    assert memory.promotion_provenance.source_ids == ["provider-docs"]
    assert memory.promotion_provenance.verified_at == WHEN


@pytest.mark.asyncio
async def test_promote_project_context_supersedes_only_an_explicit_active_record() -> None:
    previous = ProjectContextDocument(
        _id=deterministic_object_id("old-context"),
        project_id=PROJECT,
        type="implementation",
        title="Old retry behavior",
        content="The old behavior required a manual retry.",
        importance=0.5,
        source="user",
        tags=[],
    )
    contexts = ContextWriter(previous)
    candidate_service, _ = service(contexts=contexts)
    candidate = await approved_candidate(candidate_service)

    promoted = await candidate_service.promote_candidate(
        research_project_id=RESEARCH,
        candidate_id=candidate.candidate_id,
        expected_revision=candidate.revision,
        reviewer="promoter-key",
        allowed_project_ids=(PROJECT,),
        supersedes_context_id=str(previous.id),
    )

    assert promoted.target_type == "project_context"
    assert contexts.superseded == [previous.id]
    assert contexts.created[0].promotion_provenance is not None
    assert contexts.created[0].promotion_provenance.source_ids == ["provider-docs"]


@pytest.mark.asyncio
async def test_promotion_requires_approval_and_cannot_repeat_or_modify_skills() -> None:
    memories = MemoryWriter()
    candidate_service, _ = service(memories=memories)
    pending = await create_candidate(candidate_service, "retry-pending")

    with pytest.raises(CandidatePromotionStateError, match="approved"):
        await candidate_service.promote_candidate(
            research_project_id=RESEARCH,
            candidate_id=pending.candidate_id,
            expected_revision=pending.revision,
            reviewer="promoter-key",
            allowed_project_ids=(PROJECT,),
        )

    approved = await approved_candidate(
        candidate_service, candidate_type=CandidateType.MEMORY, candidate_id="retry-promoted"
    )
    await candidate_service.promote_candidate(
        research_project_id=RESEARCH,
        candidate_id=approved.candidate_id,
        expected_revision=approved.revision,
        reviewer="promoter-key",
        allowed_project_ids=(PROJECT,),
    )
    with pytest.raises(CandidatePromotionStateError, match="already promoted"):
        await candidate_service.promote_candidate(
            research_project_id=RESEARCH,
            candidate_id=approved.candidate_id,
            expected_revision=approved.revision + 1,
            reviewer="promoter-key",
            allowed_project_ids=(PROJECT,),
        )

    skill = await candidate_service.create_candidate(
        research_project_id=RESEARCH,
        candidate_id="retry-skill",
        candidate_type=CandidateType.SKILL_UPDATE,
        target_project_id=None,
        target_skill_id="safe-retries",
        current_skill_version="1.0.0",
        title="Skill change",
        content="Must remain a canonical Git change.",
        source_claim_ids=["retry-key"],
        source_citation_ids=[],
        confidence=0.9,
        confidence_rationale="Reviewed.",
        created_by="writer-key",
        allowed_project_ids=(PROJECT,),
    )
    skill = (
        await candidate_service.review_candidate(
            research_project_id=RESEARCH,
            candidate_id=skill.candidate_id,
            expected_revision=skill.revision,
            decision=CandidateState.APPROVED,
            reviewer="reviewer-key",
            reason=None,
            allowed_project_ids=(PROJECT,),
        )
    ).candidate
    with pytest.raises(CandidatePromotionTargetError, match="skills"):
        await candidate_service.promote_candidate(
            research_project_id=RESEARCH,
            candidate_id=skill.candidate_id,
            expected_revision=skill.revision,
            reviewer="promoter-key",
            allowed_project_ids=(PROJECT,),
        )
