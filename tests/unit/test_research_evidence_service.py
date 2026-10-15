"""V3 Phase 7 citation provenance and evidence-packet service tests."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.common import Page
from app.models.research import (
    CitationLocator,
    CitationRelation,
    ClaimState,
    ResearchAuthorization,
    ResearchChunkDocument,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
    ResearchSourceDocument,
    ResearchSourceType,
    SourceClass,
    SourceSnapshotDocument,
)
from app.repositories.errors import DuplicateDocumentError
from app.research.errors import ResearchCitationProvenanceError
from app.schemas.research import ResearchEvidencePacketResponse
from app.services.research_evidence_service import ResearchEvidenceService
from app.services.research_search_service import ResearchSearchItem, ResearchSearchResult
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


def source(
    source_id: str, source_class: SourceClass, *, private: bool = False
) -> ResearchSourceDocument:
    project_id = PRIVATE_PROJECT if private else PROJECT
    return ResearchSourceDocument(
        research_project_id=RESEARCH,
        source_id=source_id,
        source_type=ResearchSourceType.URL,
        uri=f"https://{source_id}.example.test/retries",
        canonical_uri=f"https://{source_id}.example.test/retries",
        title=f"{source_id} retry guide",
        publisher="Example provider",
        author="Documentation team",
        published_at=WHEN,
        retrieved_at=WHEN,
        source_class=source_class,
        authorization=authorization(project_id),
    )


def snapshot(source_id: str, snapshot_id: str, *, private: bool = False) -> SourceSnapshotDocument:
    project_id = PRIVATE_PROJECT if private else PROJECT
    return SourceSnapshotDocument(
        research_project_id=RESEARCH,
        source_id=source_id,
        snapshot_id=snapshot_id,
        retrieved_at=WHEN,
        content_hash=("a" if source_id == "primary" else "b") * 64,
        authorization=authorization(project_id),
    )


def chunk(
    source_id: str,
    snapshot_id: str,
    chunk_id: str,
    content: str,
    *,
    private: bool = False,
) -> ResearchChunkDocument:
    project_id = PRIVATE_PROJECT if private else PROJECT
    return ResearchChunkDocument(
        research_project_id=RESEARCH,
        source_id=source_id,
        snapshot_id=snapshot_id,
        document_id=f"{source_id}-document",
        chunk_id=chunk_id,
        chunk_index=0,
        section="Retry semantics",
        content=content,
        source_class=SourceClass.OFFICIAL_DOCUMENTATION,
        locator=CitationLocator(
            url=f"https://{source_id}.example.test/retries",
            anchor="retry-semantics",
            page=2,
            line_start=10,
            line_end=12,
        ),
        authorization=authorization(project_id),
    )


def claim() -> ResearchClaimDocument:
    return ResearchClaimDocument(
        research_project_id=RESEARCH,
        claim_id="retry-key",
        statement="Retryable writes require idempotency keys.",
        status=ClaimState.CONTESTED,
        confidence=0.7,
        confidence_rationale="A reviewer recorded conflicting evidence.",
        authorization=authorization(PROJECT),
    )


class Store:
    def __init__(self) -> None:
        self.sources = {
            "primary": source("primary", SourceClass.OFFICIAL_DOCUMENTATION),
            "secondary": source("secondary", SourceClass.SECONDARY),
            "private": source("private", SourceClass.PRIMARY, private=True),
        }
        self.snapshots = {
            ("primary", "primary-v1"): snapshot("primary", "primary-v1"),
            ("secondary", "secondary-v1"): snapshot("secondary", "secondary-v1"),
            ("private", "private-v1"): snapshot("private", "private-v1", private=True),
        }
        self.chunks = {
            ("primary", "primary-v1", "primary-chunk"): chunk(
                "primary",
                "primary-v1",
                "primary-chunk",
                "Retryable writes require an idempotency key.",
            ),
            ("secondary", "secondary-v1", "secondary-chunk"): chunk(
                "secondary",
                "secondary-v1",
                "secondary-chunk",
                "Some legacy clients retry without an idempotency key.",
            ),
            ("private", "private-v1", "private-chunk"): chunk(
                "private",
                "private-v1",
                "private-chunk",
                "Private retry policy evidence.",
                private=True,
            ),
        }
        self.claims = {"retry-key": claim()}
        self.citations: dict[tuple[str, str], ResearchCitationDocument] = {}

    async def get(self, source_id: str) -> ResearchSourceDocument | None:
        return self.sources.get(source_id)

    def snapshots_for(self, research_project_id: str) -> "SnapshotStore":
        return SnapshotStore(self, research_project_id)

    def chunks_for(self, research_project_id: str) -> "ChunkStore":
        return ChunkStore(self, research_project_id)

    def claims_for(self, research_project_id: str) -> "ClaimStore":
        return ClaimStore(self, research_project_id)

    def citations_for(self, research_project_id: str) -> "CitationStore":
        return CitationStore(self, research_project_id)


class SnapshotStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def get(self, source_id: str, snapshot_id: str) -> SourceSnapshotDocument | None:
        result = self._store.snapshots.get((source_id, snapshot_id))
        return (
            result if result and result.research_project_id == self._research_project_id else None
        )


class ChunkStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def get_by_source_snapshot_chunk(
        self, source_id: str, snapshot_id: str, chunk_id: str
    ) -> ResearchChunkDocument | None:
        result = self._store.chunks.get((source_id, snapshot_id, chunk_id))
        return (
            result if result and result.research_project_id == self._research_project_id else None
        )


class ClaimStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def get(self, claim_id: str) -> ResearchClaimDocument | None:
        result = self._store.claims.get(claim_id)
        return (
            result if result and result.research_project_id == self._research_project_id else None
        )


class CitationStore:
    def __init__(self, store: Store, research_project_id: str) -> None:
        self._store = store
        self._research_project_id = research_project_id

    async def create(self, document: ResearchCitationDocument) -> ResearchCitationDocument:
        key = (document.claim_id, document.citation_id)
        if key in self._store.citations:
            raise DuplicateDocumentError("citation exists")
        self._store.citations[key] = document
        return document

    async def get(self, claim_id: str, citation_id: str) -> ResearchCitationDocument | None:
        result = self._store.citations.get((claim_id, citation_id))
        return (
            result if result and result.research_project_id == self._research_project_id else None
        )

    async def list(self, **kwargs: Any) -> Page[ResearchCitationDocument]:
        claim_id = kwargs.get("claim_id")
        allowed = kwargs.get("allowed_project_ids")
        values = [
            citation
            for citation in self._store.citations.values()
            if citation.research_project_id == self._research_project_id
            and (claim_id is None or citation.claim_id == claim_id)
            and (allowed is None or set(citation.authorization.project_ids).issubset(allowed))
        ]
        return Page(items=tuple(values))


class Search:
    async def search(self, **kwargs: Any) -> ResearchSearchResult:
        budget = kwargs["token_budget"]
        items = (
            ResearchSearchItem(
                kind="chunk",
                identifier="primary-chunk",
                source="research_official_documentation",
                title="Primary retry guide",
                content="Retryable writes require an idempotency key.",
                status="active",
                source_id="primary",
                snapshot_id="primary-v1",
                document_id="primary-document",
                source_type=ResearchSourceType.URL,
                source_class=SourceClass.OFFICIAL_DOCUMENTATION,
                publisher="Example provider",
                domain="primary.example.test",
                published_at=WHEN,
                retrieved_at=WHEN,
                locator={"url": "https://primary.example.test/retries", "page": 2},
                related_project_ids=(PROJECT,),
                retrieval_score=0.9,
                methods=("lexical",),
                tokens=10,
                updated_at=WHEN,
            ),
            ResearchSearchItem(
                kind="chunk",
                identifier="secondary-chunk",
                source="research_secondary",
                title="Secondary retry guide",
                content="Some legacy clients retry without an idempotency key.",
                status="active",
                source_id="secondary",
                snapshot_id="secondary-v1",
                document_id="secondary-document",
                source_type=ResearchSourceType.URL,
                source_class=SourceClass.SECONDARY,
                publisher="Example provider",
                domain="secondary.example.test",
                published_at=WHEN,
                retrieved_at=WHEN,
                locator={"url": "https://secondary.example.test/retries", "page": 2},
                related_project_ids=(PROJECT,),
                retrieval_score=0.7,
                methods=("lexical",),
                tokens=12,
                updated_at=WHEN,
            ),
        )
        return ResearchSearchResult(
            research_project_id=RESEARCH,
            retrieval_profile="research_general",
            method="hybrid",
            items=items,
            token_budget=budget,
            tokens_used=22,
            next_cursor=None,
            diagnostics={},
        )


def service() -> tuple[ResearchEvidenceService, Store]:
    store = Store()
    return (
        ResearchEvidenceService(
            FakeResearchProjectRepository([project()]),
            store,
            store.snapshots_for,
            store.chunks_for,
            store.claims_for,
            store.citations_for,
            Search(),
            token_budget=200,
            max_token_budget=200,
        ),
        store,
    )


async def add_citation(
    artifacts: ResearchEvidenceService,
    *,
    citation_id: str,
    source_id: str,
    snapshot_id: str,
    chunk_id: str,
    excerpt: str,
    relation: CitationRelation,
) -> ResearchCitationDocument:
    return await artifacts.create_citation(
        research_project_id=RESEARCH,
        citation_id=citation_id,
        claim_id="retry-key",
        source_id=source_id,
        snapshot_id=snapshot_id,
        chunk_id=chunk_id,
        locator=CitationLocator(url=f"https://{source_id}.example.test/retries"),
        evidence_excerpt=excerpt,
        relation=relation,
        created_by="writer-key",
        allowed_project_ids=(PROJECT,),
    )


@pytest.mark.asyncio
async def test_citation_preserves_chunk_locator_and_never_changes_claim_state() -> None:
    artifacts, store = service()
    created = await add_citation(
        artifacts,
        citation_id="supports",
        source_id="primary",
        snapshot_id="primary-v1",
        chunk_id="primary-chunk",
        excerpt="Retryable writes require an idempotency key.",
        relation=CitationRelation.SUPPORTS,
    )

    assert (
        created.id
        == ResearchCitationDocument(
            research_project_id=RESEARCH,
            claim_id="retry-key",
            citation_id="supports",
            source_id="primary",
            snapshot_id="primary-v1",
            source_class=SourceClass.OFFICIAL_DOCUMENTATION,
            locator=created.locator,
            evidence_excerpt=created.evidence_excerpt,
            relation=CitationRelation.SUPPORTS,
            authorization=created.authorization,
        ).id
    )
    assert created.source_class is SourceClass.OFFICIAL_DOCUMENTATION
    assert created.locator.page == 2
    assert created.locator.line_start == 10
    assert created.locator.anchor == "retry-semantics"
    assert store.claims["retry-key"].status is ClaimState.CONTESTED
    assert store.claims["retry-key"].supporting_citation_ids == []


@pytest.mark.asyncio
async def test_packet_surfaces_support_contradiction_qualifier_and_locator_provenance() -> None:
    artifacts, _ = service()
    await add_citation(
        artifacts,
        citation_id="supports",
        source_id="primary",
        snapshot_id="primary-v1",
        chunk_id="primary-chunk",
        excerpt="Retryable writes require an idempotency key.",
        relation=CitationRelation.SUPPORTS,
    )
    await add_citation(
        artifacts,
        citation_id="contradicts",
        source_id="secondary",
        snapshot_id="secondary-v1",
        chunk_id="secondary-chunk",
        excerpt="Some legacy clients retry without an idempotency key.",
        relation=CitationRelation.CONTRADICTS,
    )
    await add_citation(
        artifacts,
        citation_id="qualifies",
        source_id="primary",
        snapshot_id="primary-v1",
        chunk_id="primary-chunk",
        excerpt="Retryable writes require an idempotency key.",
        relation=CitationRelation.QUALIFIES,
    )

    packet = await artifacts.build_evidence_packet(
        query="retry idempotency",
        research_project_id=RESEARCH,
        project_id=PROJECT,
        token_budget=200,
        allowed_project_ids=(PROJECT,),
    )

    assert [item.citation.relation for item in packet.supporting_evidence] == [
        CitationRelation.SUPPORTS
    ]
    assert [item.citation.relation for item in packet.contradicting_evidence] == [
        CitationRelation.CONTRADICTS
    ]
    assert [item.citation.relation for item in packet.qualifying_evidence] == [
        CitationRelation.QUALIFIES
    ]
    assert packet.conflicts[0].statement == "Retryable writes require idempotency keys."
    assert packet.conflicts[0].supporting[0].snapshot.retrieved_at == WHEN
    assert packet.conflicts[0].contradicting[0].citation.source_class is SourceClass.SECONDARY
    assert all(locator.url and locator.page == 2 for locator in packet.citation_locators)
    assert all(
        locator.line_start == 10 and locator.line_end == 12 for locator in packet.citation_locators
    )
    assert any("no side was selected" in message for message in packet.uncertainties)
    response = ResearchEvidencePacketResponse.from_packet(packet)
    assert response.conflicts[0].contradicting_evidence[0].source.retrieved_at == WHEN
    assert (
        response.conflicts[0].contradicting_evidence[0].source.source_class is SourceClass.SECONDARY
    )
    assert response.source_metadata[0].snapshot_id == "primary-v1"
    assert packet.tokens_used <= packet.token_budget


@pytest.mark.asyncio
async def test_citation_rejects_nonmatching_excerpt_and_unreadable_source() -> None:
    artifacts, _ = service()
    with pytest.raises(ResearchCitationProvenanceError, match="must occur"):
        await add_citation(
            artifacts,
            citation_id="wrong-text",
            source_id="primary",
            snapshot_id="primary-v1",
            chunk_id="primary-chunk",
            excerpt="A different statement.",
            relation=CitationRelation.SUPPORTS,
        )
    with pytest.raises(ResearchCitationProvenanceError, match="source was not found"):
        await add_citation(
            artifacts,
            citation_id="private",
            source_id="private",
            snapshot_id="private-v1",
            chunk_id="private-chunk",
            excerpt="Private retry policy evidence.",
            relation=CitationRelation.SUPPORTS,
        )


def test_locator_supports_url_page_and_lines_but_refuses_missing_location() -> None:
    locator = CitationLocator(
        url="https://example.test/retries",
        page=3,
        line_start=21,
        line_end=24,
    )

    assert locator.url == "https://example.test/retries"
    assert locator.page == 3
    assert locator.line_start == 21
    with pytest.raises(ValidationError, match="must identify a source location"):
        CitationLocator()


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
