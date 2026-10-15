"""V3 Phase 8 structured research-brief construction tests."""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.models.common import Page
from app.models.research import (
    CitationLocator,
    CitationRelation,
    ClaimState,
    ResearchAuthorization,
    ResearchBriefDocument,
    ResearchBriefStatus,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
    ResearchRecordStatus,
    ResearchSourceDocument,
    ResearchSourceType,
    SourceClass,
    SourceSnapshotDocument,
)
from app.repositories.errors import DuplicateDocumentError
from app.research.errors import ResearchBriefNotFoundError
from app.services.research_brief_service import ResearchBriefService
from tests.research_doubles import FakeResearchProjectRepository

PROJECT = "payments-api"
PRIVATE_PROJECT = "private-ledger"
RESEARCH = "retry-evidence"
WHEN = datetime(2026, 9, 30, tzinfo=UTC)


def authorization(*project_ids: str) -> ResearchAuthorization:
    return ResearchAuthorization(project_ids=list(project_ids), owner_key_id="owner-key")


def project() -> ResearchProjectDocument:
    return ResearchProjectDocument(
        research_project_id=RESEARCH,
        name="Retry evidence",
        question="What constrains safe payment retries?",
        type="technical_research",
        related_project_ids=[PROJECT],
        authorization=authorization(PROJECT),
    )


def claim(
    claim_id: str,
    status: ClaimState,
    *,
    private: bool = False,
) -> ResearchClaimDocument:
    return ResearchClaimDocument(
        research_project_id=RESEARCH,
        claim_id=claim_id,
        statement=f"{claim_id} statement.",
        status=status,
        confidence=0.8,
        confidence_rationale="Explicit reviewer assessment.",
        authorization=authorization(PRIVATE_PROJECT if private else PROJECT),
    )


def source(
    source_id: str,
    source_class: SourceClass,
    *,
    stale: bool = False,
    private: bool = False,
) -> ResearchSourceDocument:
    return ResearchSourceDocument(
        research_project_id=RESEARCH,
        source_id=source_id,
        source_type=ResearchSourceType.URL,
        uri=f"https://{source_id}.example.test/retries",
        canonical_uri=f"https://{source_id}.example.test/retries",
        title=f"{source_id} retry guide",
        publisher="Example provider",
        published_at=WHEN,
        retrieved_at=WHEN,
        source_class=source_class,
        status=ResearchRecordStatus.SUPERSEDED if stale else ResearchRecordStatus.ACTIVE,
        authorization=authorization(PRIVATE_PROJECT if private else PROJECT),
    )


def snapshot(
    source_id: str,
    *,
    stale: bool = False,
    private: bool = False,
) -> SourceSnapshotDocument:
    return SourceSnapshotDocument(
        research_project_id=RESEARCH,
        source_id=source_id,
        snapshot_id=f"{source_id}-v1",
        retrieved_at=WHEN,
        content_hash=("a" if source_id == "official" else "b") * 64,
        status=ResearchRecordStatus.SUPERSEDED if stale else ResearchRecordStatus.ACTIVE,
        authorization=authorization(PRIVATE_PROJECT if private else PROJECT),
    )


def citation(
    citation_id: str,
    claim_id: str,
    source_id: str,
    relation: CitationRelation,
    *,
    private: bool = False,
) -> ResearchCitationDocument:
    return ResearchCitationDocument(
        research_project_id=RESEARCH,
        citation_id=citation_id,
        claim_id=claim_id,
        source_id=source_id,
        snapshot_id=f"{source_id}-v1",
        chunk_id=f"{source_id}-chunk",
        source_class=SourceClass.OFFICIAL_DOCUMENTATION,
        locator=CitationLocator(
            url=f"https://{source_id}.example.test/retries",
            anchor="retry-policy",
            page=2,
            line_start=10,
            line_end=12,
        ),
        evidence_excerpt=f"{citation_id} excerpt.",
        relation=relation,
        authorization=authorization(PRIVATE_PROJECT if private else PROJECT),
    )


class Store:
    def __init__(self) -> None:
        self.claims: list[ResearchClaimDocument] = []
        self.citations: list[ResearchCitationDocument] = []
        self.sources: dict[str, ResearchSourceDocument] = {}
        self.snapshots: dict[tuple[str, str], SourceSnapshotDocument] = {}
        self.briefs: dict[str, ResearchBriefDocument] = {}
        self.claim_next_cursor: str | None = None
        self.citation_next_cursor: str | None = None

    def claim_store(self, research_project_id: str) -> "ClaimStore":
        return ClaimStore(self, research_project_id)

    def citation_store(self, research_project_id: str) -> "CitationStore":
        return CitationStore(self, research_project_id)

    def source_store(self, research_project_id: str) -> "SourceStore":
        return SourceStore(self, research_project_id)

    def snapshot_store(self, research_project_id: str) -> "SnapshotStore":
        return SnapshotStore(self, research_project_id)

    def brief_store(self, research_project_id: str) -> "BriefStore":
        return BriefStore(self, research_project_id)


class SourceStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def get(self, source_id: str) -> ResearchSourceDocument | None:
        source_value = self._store.sources.get(source_id)
        if source_value is None or source_value.research_project_id != self._research_project_id:
            return None
        return source_value


class ClaimStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def list(self, **kwargs: Any) -> Page[ResearchClaimDocument]:
        _ = kwargs
        return Page(
            items=tuple(
                item
                for item in self._store.claims
                if item.research_project_id == self._research_project_id
            ),
            next_cursor=self._store.claim_next_cursor,
        )


class CitationStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def list(self, **kwargs: Any) -> Page[ResearchCitationDocument]:
        _ = kwargs
        return Page(
            items=tuple(
                item
                for item in self._store.citations
                if item.research_project_id == self._research_project_id
            ),
            next_cursor=self._store.citation_next_cursor,
        )


class SnapshotStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def get(self, source_id: str, snapshot_id: str) -> SourceSnapshotDocument | None:
        snapshot_value = self._store.snapshots.get((source_id, snapshot_id))
        if (
            snapshot_value is None
            or snapshot_value.research_project_id != self._research_project_id
        ):
            return None
        return snapshot_value


class BriefStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def create(self, document: ResearchBriefDocument) -> ResearchBriefDocument:
        if document.research_project_id != self._research_project_id:
            raise AssertionError("brief is outside its research project")
        if document.brief_id in self._store.briefs:
            raise DuplicateDocumentError("brief exists")
        self._store.briefs[document.brief_id] = document
        return document

    async def get(self, brief_id: str) -> ResearchBriefDocument | None:
        document = self._store.briefs.get(brief_id)
        if document is None or document.research_project_id != self._research_project_id:
            return None
        return document


def service() -> tuple[ResearchBriefService, Store]:
    store = Store()
    return (
        ResearchBriefService(
            FakeResearchProjectRepository([project()]),
            store.claim_store,
            store.citation_store,
            store.source_store,
            store.snapshot_store,
            store.brief_store,
        ),
        store,
    )


def add_source(store: Store, source_id: str, *, stale: bool = False, private: bool = False) -> None:
    store.sources[source_id] = source(
        source_id,
        SourceClass.OFFICIAL_DOCUMENTATION,
        stale=stale,
        private=private,
    )
    store.snapshots[(source_id, f"{source_id}-v1")] = snapshot(
        source_id,
        stale=stale,
        private=private,
    )


@pytest.mark.asyncio
async def test_brief_keeps_supported_claims_citations_and_source_provenance() -> None:
    briefs, store = service()
    add_source(store, "official")
    add_source(store, "secondary")
    store.claims = [
        claim("retry-key", ClaimState.SUPPORTED),
        claim("legacy-client", ClaimState.CONTESTED),
        claim("unresolved", ClaimState.UNSUPPORTED),
    ]
    store.citations = [
        citation("official-support", "retry-key", "official", CitationRelation.SUPPORTS),
        citation("legacy-support", "legacy-client", "official", CitationRelation.SUPPORTS),
        citation("legacy-contradicts", "legacy-client", "secondary", CitationRelation.CONTRADICTS),
    ]

    built = await briefs.build_brief(
        research_project_id=RESEARCH,
        brief_id="technical-retry-brief",
        scope="Official and secondary retry evidence.",
        additional_unknowns=("Legacy SDK coverage is incomplete.",),
        next_questions=("Which SDK versions enforce the key?",),
        status=ResearchBriefStatus.READY,
        created_by="writer-key",
        allowed_project_ids=(PROJECT,),
    )

    assert built.key_findings == ["retry-key statement."]
    assert {item.claim_id for item in built.claim_refs} == {
        "retry-key",
        "legacy-client",
        "unresolved",
    }
    assert built.citation_refs[0].locator.page == 2
    assert built.citation_refs[0].locator.line_start == 10
    assert {(item.source_id, item.snapshot_id) for item in built.source_refs} == {
        ("official", "official-v1"),
        ("secondary", "secondary-v1"),
    }
    assert built.contradictions[0].claim_id == "legacy-client"
    assert set(built.contradictions[0].supporting_citation_ids) == {"legacy-support"}
    assert set(built.contradictions[0].contradicting_citation_ids) == {"legacy-contradicts"}
    assert any("unresolved remains explicitly unsupported" in item for item in built.unknowns)
    assert any("does not claim completeness" in item for item in built.unknowns)
    assert built.status is ResearchBriefStatus.READY
    assert built.authorization.project_ids == [PROJECT]
    assert not hasattr(briefs, "promote")


@pytest.mark.asyncio
async def test_brief_marks_missing_and_stale_evidence_as_unknown() -> None:
    briefs, store = service()
    add_source(store, "stale", stale=True)
    store.claims = [
        claim("missing-citation", ClaimState.SUPPORTED),
        claim("outdated-guide", ClaimState.OUTDATED),
    ]
    store.citations = [
        citation("stale-support", "outdated-guide", "stale", CitationRelation.SUPPORTS)
    ]

    built = await briefs.build_brief(
        research_project_id=RESEARCH,
        brief_id="stale-evidence-brief",
        scope="Review current retry guidance.",
        additional_unknowns=(),
        next_questions=(),
        status=ResearchBriefStatus.DRAFT,
        created_by="writer-key",
        allowed_project_ids=(PROJECT,),
    )

    assert any(
        "missing-citation has no authorized supporting citation" in item for item in built.unknowns
    )
    assert any("outdated-guide remains explicitly outdated" in item for item in built.unknowns)
    assert any(
        "stale-support references stale or non-active evidence" in item for item in built.unknowns
    )


@pytest.mark.asyncio
async def test_brief_excludes_claims_and_provenance_outside_callers_project_grant() -> None:
    briefs, store = service()
    add_source(store, "official")
    add_source(store, "private", private=True)
    store.claims = [
        claim("public-claim", ClaimState.SUPPORTED),
        claim("private-claim", ClaimState.SUPPORTED, private=True),
    ]
    store.citations = [
        citation("public-support", "public-claim", "official", CitationRelation.SUPPORTS),
        citation(
            "private-support",
            "private-claim",
            "private",
            CitationRelation.SUPPORTS,
            private=True,
        ),
    ]

    built = await briefs.build_brief(
        research_project_id=RESEARCH,
        brief_id="public-brief",
        scope="Authorized evidence only.",
        additional_unknowns=(),
        next_questions=(),
        status=ResearchBriefStatus.DRAFT,
        created_by="writer-key",
        allowed_project_ids=(PROJECT,),
    )

    assert [item.claim_id for item in built.claim_refs] == ["public-claim"]
    assert [item.source_id for item in built.source_refs] == ["official"]
    assert [item.citation_id for item in built.citation_refs] == ["public-support"]

    private_brief = built.model_copy(
        update={"brief_id": "private-brief", "authorization": authorization(PRIVATE_PROJECT)}
    )
    store.briefs[private_brief.brief_id] = private_brief
    with pytest.raises(ResearchBriefNotFoundError):
        await briefs.get_brief(
            research_project_id=RESEARCH,
            brief_id=private_brief.brief_id,
            allowed_project_ids=(PROJECT,),
        )


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
