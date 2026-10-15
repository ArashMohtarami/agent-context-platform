"""V3 Phase 6 service tests for working notes and explicitly reviewed claims."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.common import Page
from app.models.research import (
    CitationLocator,
    CitationRelation,
    ClaimState,
    ResearchAuthorization,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchNoteDocument,
    ResearchNoteStatus,
    ResearchProjectDocument,
    ResearchSourceDocument,
    ResearchSourceReference,
    ResearchSourceType,
    SourceClass,
    SourceSnapshotDocument,
)
from app.repositories.errors import ConcurrencyConflictError
from app.research.errors import ResearchCitationNotFoundError, ResearchSourceReferenceNotFoundError
from app.schemas.research import ResearchClaimCreateRequest
from app.services.research_notes_claims_service import ResearchNotesClaimsService
from tests.research_doubles import FakeResearchProjectRepository

PROJECT = "payments-api"
RESEARCH = "retry-evidence"
WHEN = datetime(2026, 9, 30, tzinfo=UTC)


def research_project() -> ResearchProjectDocument:
    return ResearchProjectDocument(
        research_project_id=RESEARCH,
        name="Retry evidence",
        question="What constrains safe payment retries?",
        type="technical_research",
        related_project_ids=[PROJECT],
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="owner-key"),
    )


def source() -> ResearchSourceDocument:
    return ResearchSourceDocument(
        research_project_id=RESEARCH,
        source_id="provider-docs",
        source_type=ResearchSourceType.URL,
        uri="https://example.test/retries",
        canonical_uri="https://example.test/retries",
        title="Provider retries",
        source_class=SourceClass.OFFICIAL_DOCUMENTATION,
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="owner-key"),
    )


def snapshot() -> SourceSnapshotDocument:
    return SourceSnapshotDocument(
        research_project_id=RESEARCH,
        source_id="provider-docs",
        snapshot_id="provider-v1",
        retrieved_at=WHEN,
        content_hash="a" * 64,
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="owner-key"),
    )


def citation(
    claim_id: str, citation_id: str, relation: CitationRelation
) -> ResearchCitationDocument:
    return ResearchCitationDocument(
        research_project_id=RESEARCH,
        claim_id=claim_id,
        citation_id=citation_id,
        source_id="provider-docs",
        snapshot_id="provider-v1",
        source_class=SourceClass.OFFICIAL_DOCUMENTATION,
        locator=CitationLocator(url="https://example.test/retries", anchor="retry"),
        evidence_excerpt="Use an idempotency key for retryable requests.",
        relation=relation,
        authorization=ResearchAuthorization(project_ids=[PROJECT], owner_key_id="owner-key"),
    )


class Store:
    def __init__(self) -> None:
        self.sources = {"provider-docs": source()}
        self.snapshots = {("provider-docs", "provider-v1"): snapshot()}
        self.notes: dict[str, ResearchNoteDocument] = {}
        self.claims: dict[str, ResearchClaimDocument] = {}
        self.citations = {
            ("contested-retry", "supports"): citation(
                "contested-retry", "supports", CitationRelation.SUPPORTS
            ),
            ("contested-retry", "contradicts"): citation(
                "contested-retry", "contradicts", CitationRelation.CONTRADICTS
            ),
        }

    async def get(self, source_id: str) -> ResearchSourceDocument | None:
        return self.sources.get(source_id)

    def snapshots_for(self, research_project_id: str) -> "SnapshotStore":
        return SnapshotStore(self, research_project_id)

    def notes_for(self, research_project_id: str) -> "NoteStore":
        return NoteStore(self, research_project_id)

    def claims_for(self, research_project_id: str) -> "ClaimStore":
        return ClaimStore(self, research_project_id)

    def citations_for(self, research_project_id: str) -> "CitationStore":
        return CitationStore(self, research_project_id)


class SnapshotStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def get(self, source_id: str, snapshot_id: str) -> SourceSnapshotDocument | None:
        value = self._store.snapshots.get((source_id, snapshot_id))
        return (
            value
            if value is not None and value.research_project_id == self._research_project_id
            else None
        )


class NoteStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def create(self, document: ResearchNoteDocument) -> ResearchNoteDocument:
        self._store.notes[document.note_id] = document
        return document

    async def get(self, note_id: str) -> ResearchNoteDocument | None:
        value = self._store.notes.get(note_id)
        return (
            value
            if value is not None and value.research_project_id == self._research_project_id
            else None
        )

    async def list(self, **kwargs: object) -> Page[ResearchNoteDocument]:
        del kwargs
        return Page(items=tuple(self._store.notes.values()))

    async def text_search(self, *args: object, **kwargs: object) -> tuple[object, ...]:
        del args, kwargs
        return ()

    async def update(
        self, document: ResearchNoteDocument, *, expected_revision: int
    ) -> ResearchNoteDocument:
        assert document.revision == expected_revision
        updated = document.model_copy(update={"revision": expected_revision + 1})
        self._store.notes[document.note_id] = updated
        return updated


class ClaimStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def create(self, document: ResearchClaimDocument) -> ResearchClaimDocument:
        self._store.claims[document.claim_id] = document
        return document

    async def get(self, claim_id: str) -> ResearchClaimDocument | None:
        value = self._store.claims.get(claim_id)
        return (
            value
            if value is not None and value.research_project_id == self._research_project_id
            else None
        )

    async def list(
        self, *, status: ClaimState | None = None, **kwargs: object
    ) -> Page[ResearchClaimDocument]:
        del kwargs
        values = tuple(
            value
            for value in self._store.claims.values()
            if status is None or value.status is status
        )
        return Page(items=values)

    async def update(
        self, document: ResearchClaimDocument, *, expected_revision: int
    ) -> ResearchClaimDocument:
        assert document.revision == expected_revision
        updated = document.model_copy(update={"revision": expected_revision + 1})
        self._store.claims[document.claim_id] = updated
        return updated


class CitationStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def get(self, claim_id: str, citation_id: str) -> ResearchCitationDocument | None:
        value = self._store.citations.get((claim_id, citation_id))
        return (
            value
            if value is not None and value.research_project_id == self._research_project_id
            else None
        )


def service() -> tuple[ResearchNotesClaimsService, Store]:
    store = Store()
    return (
        ResearchNotesClaimsService(
            FakeResearchProjectRepository([research_project()]),
            store,
            store.snapshots_for,
            store.notes_for,
            store.claims_for,
            store.citations_for,
        ),
        store,
    )


@pytest.mark.asyncio
async def test_notes_preserve_provenance_and_remain_working_artifacts() -> None:
    artifacts, store = service()
    created = await artifacts.create_note(
        research_project_id=RESEARCH,
        note_id="provider-requirement",
        title="Retry requirement",
        body="The provider guide requires an idempotency key.",
        source_refs=[ResearchSourceReference(source_id="provider-docs", snapshot_id="provider-v1")],
        status=ResearchNoteStatus.WORKING,
        created_by="writer-key",
        allowed_project_ids=(PROJECT,),
    )
    updated = await artifacts.update_note(
        research_project_id=RESEARCH,
        note_id=created.note_id,
        expected_revision=created.revision,
        changes={"status": ResearchNoteStatus.REVIEWED},
        allowed_project_ids=(PROJECT,),
    )

    assert created.source_refs[0].snapshot_id == "provider-v1"
    assert created.created_by == "writer-key"
    assert updated.status is ResearchNoteStatus.REVIEWED
    assert updated.revision == 2
    assert set(store.__dict__) == {"sources", "snapshots", "notes", "claims", "citations"}


@pytest.mark.asyncio
async def test_claim_states_are_explicit_and_conflicting_evidence_is_preserved() -> None:
    artifacts, _ = service()
    supported = await artifacts.create_claim(
        research_project_id=RESEARCH,
        claim_id="retry-key",
        statement="Retries need an idempotency key.",
        status=ClaimState.SUPPORTED,
        confidence=0.92,
        confidence_rationale="A reviewer assessed the registered evidence.",
        supporting_citation_ids=[],
        contradicting_citation_ids=[],
        created_by="writer-key",
        allowed_project_ids=(PROJECT,),
    )
    contested = await artifacts.create_claim(
        research_project_id=RESEARCH,
        claim_id="contested-retry",
        statement="All retries are safe without a key.",
        status=ClaimState.CONTESTED,
        confidence=0.4,
        confidence_rationale="The retained evidence disagrees.",
        supporting_citation_ids=["supports"],
        contradicting_citation_ids=["contradicts"],
        created_by="writer-key",
        allowed_project_ids=(PROJECT,),
    )
    unsupported = await artifacts.create_claim(
        research_project_id=RESEARCH,
        claim_id="unsupported-retry",
        statement="Retries always succeed.",
        status=ClaimState.UNSUPPORTED,
        confidence=None,
        confidence_rationale=None,
        supporting_citation_ids=[],
        contradicting_citation_ids=[],
        created_by="writer-key",
        allowed_project_ids=(PROJECT,),
    )

    assert supported.status is ClaimState.SUPPORTED
    assert contested.status is ClaimState.CONTESTED
    assert contested.supporting_citation_ids == ["supports"]
    assert contested.contradicting_citation_ids == ["contradicts"]
    assert unsupported.status is ClaimState.UNSUPPORTED


@pytest.mark.asyncio
async def test_note_update_rejects_a_stale_revision_before_it_can_overwrite() -> None:
    artifacts, _ = service()
    created = await artifacts.create_note(
        research_project_id=RESEARCH,
        note_id="revision-guard",
        title="Revision guard",
        body="A stale note update must not overwrite later work.",
        source_refs=[],
        status=ResearchNoteStatus.WORKING,
        created_by="writer-key",
        allowed_project_ids=(PROJECT,),
    )
    await artifacts.update_note(
        research_project_id=RESEARCH,
        note_id=created.note_id,
        expected_revision=created.revision,
        changes={"status": ResearchNoteStatus.REVIEWED},
        allowed_project_ids=(PROJECT,),
    )

    with pytest.raises(ConcurrencyConflictError):
        await artifacts.update_note(
            research_project_id=RESEARCH,
            note_id=created.note_id,
            expected_revision=created.revision,
            changes={"status": ResearchNoteStatus.ARCHIVED},
            allowed_project_ids=(PROJECT,),
        )


@pytest.mark.asyncio
async def test_claim_evidence_links_must_match_their_persisted_relation() -> None:
    artifacts, _ = service()

    with pytest.raises(ResearchCitationNotFoundError):
        await artifacts.create_claim(
            research_project_id=RESEARCH,
            claim_id="contested-retry",
            statement="The evidence relation cannot be mislabeled.",
            status=ClaimState.CONTESTED,
            confidence=None,
            confidence_rationale=None,
            supporting_citation_ids=["contradicts"],
            contradicting_citation_ids=["supports"],
            created_by="writer-key",
            allowed_project_ids=(PROJECT,),
        )


def test_claim_transport_rejects_invalid_confidence_and_self_conflicting_citations() -> None:
    with pytest.raises(ValidationError):
        ResearchClaimCreateRequest(
            research_project_id=RESEARCH,
            claim_id="invalid-confidence",
            statement="This confidence is out of range.",
            confidence=1.01,
        )
    with pytest.raises(ValidationError):
        ResearchClaimCreateRequest(
            research_project_id=RESEARCH,
            claim_id="self-conflicting",
            statement="A citation cannot occupy both relations.",
            supporting_citations=["same"],
            contradicting_citations=["same"],
        )


@pytest.mark.asyncio
async def test_note_reference_cannot_cross_an_unauthorized_v1_project_boundary() -> None:
    artifacts, store = service()
    store.sources["provider-docs"] = store.sources["provider-docs"].model_copy(
        update={"authorization": ResearchAuthorization(project_ids=["private-ledger"])}
    )

    with pytest.raises(ResearchSourceReferenceNotFoundError):
        await artifacts.create_note(
            research_project_id=RESEARCH,
            note_id="private-source",
            title="Private source",
            body="This source must not leak.",
            source_refs=[ResearchSourceReference(source_id="provider-docs")],
            status=ResearchNoteStatus.WORKING,
            created_by="writer-key",
            allowed_project_ids=(PROJECT,),
        )


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
