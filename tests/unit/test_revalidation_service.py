"""V3 Phase 14 advisory freshness and revalidation tests."""

from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.models.common import Page
from app.models.freshness import FreshnessClass, RevalidationReason
from app.models.memory import MemoryDocument
from app.models.promotion import PromotionProvenance
from app.models.research import (
    CandidateState,
    CandidateType,
    CitationLocator,
    CitationRelation,
    KnowledgeCandidateDocument,
    PromotionEventDocument,
    ResearchAuthorization,
    ResearchCitationDocument,
    ResearchProjectDocument,
    ResearchProjectType,
    SourceClass,
    SourceSnapshotDocument,
)
from app.research.errors import ResearchProjectNotFoundError
from app.services.revalidation_service import RevalidationService

PROJECT = "payments"
RESEARCH = "payment-retries"
WHEN = datetime(2026, 10, 1, tzinfo=UTC)
AFTER = datetime(2026, 10, 8, tzinfo=UTC)
AUTH = ResearchAuthorization(project_ids=[PROJECT], owner_key_id="writer")


def project() -> ResearchProjectDocument:
    return ResearchProjectDocument(
        research_project_id=RESEARCH,
        name="Payment retry research",
        question="When is payment retry knowledge stale?",
        type=ResearchProjectType.TECHNICAL_RESEARCH,
        related_project_ids=[PROJECT],
        authorization=AUTH,
    )


def provenance(
    *, freshness_class: FreshnessClass = FreshnessClass.STABLE, review_after: datetime | None = None
) -> PromotionProvenance:
    return PromotionProvenance(
        candidate_id="retry-knowledge",
        research_project_id=RESEARCH,
        claim_ids=["retry-claim"],
        citation_ids=["retry-citation"],
        source_ids=["retry-standard"],
        source_snapshot_ids=["retry-v1"],
        verified_at=WHEN,
        review_after=review_after,
        freshness_class=freshness_class,
    )


def promoted_candidate(
    *, freshness_class: FreshnessClass = FreshnessClass.STABLE, review_after: datetime | None = None
) -> KnowledgeCandidateDocument:
    return KnowledgeCandidateDocument(
        research_project_id=RESEARCH,
        candidate_id="retry-knowledge",
        candidate_type=CandidateType.MEMORY,
        target_project_id=PROJECT,
        title="Idempotency key retry behavior",
        content="Payment retries preserve the original idempotency key.",
        source_claim_ids=["retry-claim"],
        source_citation_ids=["retry-citation"],
        freshness_class=freshness_class,
        review_after=review_after,
        status=CandidateState.PROMOTED,
        reviewer="reviewer",
        reviewed_at=WHEN,
        authorization=AUTH,
    )


def memory(value: PromotionProvenance) -> MemoryDocument:
    return MemoryDocument(
        scope="project",
        project_id=PROJECT,
        type="research_finding",
        content="Payment retries preserve the original idempotency key.",
        summary="Idempotency key retry behavior",
        importance=0.8,
        confidence=0.8,
        source="research-promotion",
        promotion_provenance=value,
    )


def event(target: MemoryDocument) -> PromotionEventDocument:
    return PromotionEventDocument(
        research_project_id=RESEARCH,
        candidate_id="retry-knowledge",
        event_id="retry-promotion",
        target_type=CandidateType.MEMORY,
        target_id=str(target.id),
        reviewer="reviewer",
        decision=CandidateState.PROMOTED,
        decided_at=WHEN,
        authorization=AUTH,
    )


def citation() -> ResearchCitationDocument:
    return ResearchCitationDocument(
        research_project_id=RESEARCH,
        claim_id="retry-claim",
        citation_id="retry-citation",
        source_id="retry-standard",
        snapshot_id="retry-v1",
        source_class=SourceClass.STANDARD,
        locator=CitationLocator(url="https://example.test/retries"),
        evidence_excerpt="Payment retries preserve the original idempotency key.",
        relation=CitationRelation.SUPPORTS,
        authorization=AUTH,
    )


def snapshot(
    snapshot_id: str,
    content_hash: str,
    *,
    retrieved_at: datetime = WHEN,
    version_identifier: str | None = "v1",
    metadata: dict[str, object] | None = None,
) -> SourceSnapshotDocument:
    return SourceSnapshotDocument(
        research_project_id=RESEARCH,
        source_id="retry-standard",
        snapshot_id=snapshot_id,
        retrieved_at=retrieved_at,
        content_hash=content_hash,
        version_identifier=version_identifier,
        metadata=metadata or {"etag": "etag-v1", "last_modified": "Wed, 01 Oct 2026"},
        authorization=AUTH,
    )


class Projects:
    async def get(self, research_project_id: str) -> ResearchProjectDocument | None:
        return project() if research_project_id == RESEARCH else None


class Candidates:
    def __init__(self, value: KnowledgeCandidateDocument) -> None:
        self.records = {value.candidate_id: value}

    async def get(self, candidate_id: str) -> KnowledgeCandidateDocument | None:
        return self.records.get(candidate_id)

    async def list(
        self,
        *,
        status: CandidateState | None = None,
        target_project_id: str | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[KnowledgeCandidateDocument]:
        del target_project_id, cursor
        values = tuple(
            value
            for value in self.records.values()
            if (status is None or value.status is status)
            and (
                allowed_project_ids is None
                or set(value.authorization.project_ids).issubset(allowed_project_ids)
            )
        )
        return Page(items=values[:limit])


class Events:
    def __init__(self, value: PromotionEventDocument) -> None:
        self.value = value

    async def list(
        self,
        *,
        candidate_id: str | None = None,
        allowed_project_ids: tuple[str, ...] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[PromotionEventDocument]:
        del cursor
        if candidate_id != self.value.candidate_id or (
            allowed_project_ids is not None
            and not set(self.value.authorization.project_ids).issubset(allowed_project_ids)
        ):
            return Page(items=())
        return Page(items=(self.value,)[:limit])


class Citations:
    async def get(self, claim_id: str, citation_id: str) -> ResearchCitationDocument | None:
        return citation() if (claim_id, citation_id) == ("retry-claim", "retry-citation") else None


class Snapshots:
    def __init__(self, values: list[SourceSnapshotDocument]) -> None:
        self.values = values

    async def get(self, source_id: str, snapshot_id: str) -> SourceSnapshotDocument | None:
        return next(
            (
                value
                for value in self.values
                if value.source_id == source_id and value.snapshot_id == snapshot_id
            ),
            None,
        )

    async def latest_for_source(self, source_id: str) -> SourceSnapshotDocument | None:
        values = [value for value in self.values if value.source_id == source_id]
        return max(values, key=lambda value: value.retrieved_at) if values else None


class Memories:
    def __init__(self, value: MemoryDocument) -> None:
        self.value = value

    async def get(self, document_id: ObjectId) -> MemoryDocument | None:
        return self.value if document_id == self.value.id else None


class Contexts:
    async def get(self, document_id: ObjectId) -> None:
        del document_id
        return None


class CandidateWriter:
    def __init__(self) -> None:
        self.created: list[KnowledgeCandidateDocument] = []

    async def create_candidate(self, **values: object) -> KnowledgeCandidateDocument:
        record = KnowledgeCandidateDocument(
            research_project_id=RESEARCH,
            candidate_id=str(values["candidate_id"]),
            candidate_type=CandidateType(values["candidate_type"]),
            target_project_id=values["target_project_id"],
            target_skill_id=values["target_skill_id"],
            current_skill_version=values["current_skill_version"],
            title=str(values["title"]),
            content=str(values["content"]),
            source_claim_ids=list(values["source_claim_ids"]),
            source_citation_ids=list(values["source_citation_ids"]),
            confidence=values["confidence"],
            confidence_rationale=values["confidence_rationale"],
            freshness_class=FreshnessClass(values["freshness_class"]),
            review_after=values["review_after"],
            revalidation_of_candidate_id=values["revalidation_of_candidate_id"],
            revalidation_reasons=list(values["revalidation_reasons"]),
            authorization=AUTH,
        )
        self.created.append(record)
        return record


def service(
    *,
    freshness_class: FreshnessClass = FreshnessClass.STABLE,
    review_after: datetime | None = None,
    snapshots: list[SourceSnapshotDocument] | None = None,
) -> tuple[RevalidationService, MemoryDocument, CandidateWriter]:
    original = promoted_candidate(freshness_class=freshness_class, review_after=review_after)
    target = memory(provenance(freshness_class=freshness_class, review_after=review_after))
    writer = CandidateWriter()
    return (
        RevalidationService(
            Projects(),
            lambda _: Candidates(original),
            lambda _: Events(event(target)),
            lambda _: Citations(),
            lambda _: Snapshots(snapshots or [snapshot("retry-v1", "a" * 64)]),
            lambda _: Memories(target),
            lambda _: Contexts(),
            writer,
            clock=lambda: AFTER,
        ),
        target,
        writer,
    )


@pytest.mark.asyncio
async def test_stable_unchanged_knowledge_is_not_listed_or_mutated() -> None:
    review, target, _ = service()
    before = target.model_dump(mode="json")

    stale = await review.list_stale_knowledge(
        research_project_id=RESEARCH, allowed_project_ids=(PROJECT,), as_of=AFTER
    )

    assert stale.items == ()
    assert target.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_time_sensitive_knowledge_is_flagged_when_its_review_date_is_reached() -> None:
    review, _, _ = service(
        freshness_class=FreshnessClass.TIME_SENSITIVE,
        review_after=datetime(2026, 10, 2, tzinfo=UTC),
    )

    stale = await review.list_stale_knowledge(
        research_project_id=RESEARCH, allowed_project_ids=(PROJECT,), as_of=AFTER
    )

    assert stale.items[0].freshness_class is FreshnessClass.TIME_SENSITIVE
    assert stale.items[0].reasons == (RevalidationReason.REVIEW_DUE,)


@pytest.mark.asyncio
async def test_changed_source_proposes_pending_candidate_without_mutation() -> None:
    review, target, writer = service(
        snapshots=[
            snapshot("retry-v1", "a" * 64),
            snapshot(
                "retry-v2",
                "b" * 64,
                retrieved_at=AFTER,
                version_identifier="v2",
                metadata={"etag": "etag-v2", "last_modified": "Wed, 08 Oct 2026"},
            ),
        ]
    )
    before = target.model_dump(mode="json")

    proposed = await review.propose_revalidation(
        research_project_id=RESEARCH,
        candidate_id="retry-knowledge",
        created_by="reviewer",
        allowed_project_ids=(PROJECT,),
        as_of=AFTER,
    )

    assert proposed.candidate.status is CandidateState.PENDING_REVIEW
    assert proposed.candidate.revalidation_of_candidate_id == "retry-knowledge"
    assert RevalidationReason.CONTENT_HASH_CHANGED in proposed.stale_knowledge.reasons
    assert RevalidationReason.ETAG_CHANGED in proposed.stale_knowledge.reasons
    assert writer.created == [proposed.candidate]
    assert target.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_new_snapshot_with_unchanged_version_material_is_not_flagged() -> None:
    review, _, _ = service(
        snapshots=[
            snapshot("retry-v1", "a" * 64),
            snapshot("retry-v2", "a" * 64, retrieved_at=AFTER),
        ]
    )

    stale = await review.list_stale_knowledge(
        research_project_id=RESEARCH, allowed_project_ids=(PROJECT,), as_of=AFTER
    )

    assert stale.items == ()


@pytest.mark.asyncio
async def test_version_dependent_repository_commit_change_is_flagged() -> None:
    review, _, _ = service(
        freshness_class=FreshnessClass.VERSION_DEPENDENT,
        snapshots=[
            snapshot("retry-v1", "a" * 64, metadata={"repository_commit": "a" * 40}),
            snapshot(
                "retry-v2",
                "a" * 64,
                retrieved_at=AFTER,
                version_identifier="v2",
                metadata={"repository_commit": "b" * 40},
            ),
        ],
    )

    stale = await review.list_stale_knowledge(
        research_project_id=RESEARCH, allowed_project_ids=(PROJECT,), as_of=AFTER
    )

    reasons = stale.items[0].source_changes[0].reasons
    assert RevalidationReason.VERSION_IDENTIFIER_CHANGED in reasons
    assert RevalidationReason.REPOSITORY_COMMIT_CHANGED in reasons


@pytest.mark.asyncio
async def test_revalidation_respects_research_project_authorization() -> None:
    review, _, _ = service()

    with pytest.raises(ResearchProjectNotFoundError):
        await review.list_stale_knowledge(
            research_project_id=RESEARCH, allowed_project_ids=("other-project",), as_of=AFTER
        )
