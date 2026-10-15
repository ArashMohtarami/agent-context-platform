"""V3 Phase 7 explicit citations and bounded evidence packets.

Citations are authored provenance links.  This service never infers a citation
relation from retrieval, changes a claim state, or promotes research material
into V1 memory, project context, or skills.  It only presents already-persisted
relations and keeps conflicting evidence visible.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from app.models.common import Page
from app.models.research import (
    CitationLocator,
    CitationRelation,
    ResearchAuthorization,
    ResearchChunkDocument,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
    ResearchSourceDocument,
    SourceSnapshotDocument,
)
from app.repositories.errors import DuplicateDocumentError
from app.research.errors import (
    ResearchCitationAlreadyExistsError,
    ResearchCitationProvenanceError,
    ResearchClaimNotFoundError,
    ResearchProjectNotFoundError,
)
from app.retrieval.context_builder import MAX_TOKEN_BUDGET, resolve_token_budget
from app.security.tenant_service import tenant_service
from app.services.research_search_service import (
    ResearchRetrievalProfileName,
    ResearchSearchFilters,
    ResearchSearchItem,
    ResearchSearchMethod,
    ResearchSearchResult,
)
from app.skills.chunker import estimate_tokens


class ResearchProjectLookup(Protocol):
    """The persistent research-project scope for citations and packets."""

    async def get(self, research_project_id: str) -> ResearchProjectDocument | None: ...


class ResearchSourceCatalog(Protocol):
    """Resolve source identities before the service applies project/auth checks."""

    async def get(self, source_id: str) -> ResearchSourceDocument | None: ...


class SourceSnapshotStore(Protocol):
    """Read immutable observations only from one research project."""

    async def get(self, source_id: str, snapshot_id: str) -> SourceSnapshotDocument | None: ...


class ResearchChunkStore(Protocol):
    """Resolve a citation target through a project-scoped repository."""

    async def get_by_source_snapshot_chunk(
        self, source_id: str, snapshot_id: str, chunk_id: str
    ) -> ResearchChunkDocument | None: ...


class ResearchClaimStore(Protocol):
    """Read explicit claim state without judging it."""

    async def get(self, claim_id: str) -> ResearchClaimDocument | None: ...


class ResearchCitationStore(Protocol):
    """Persist and page explicit citation relations."""

    async def create(self, document: ResearchCitationDocument) -> ResearchCitationDocument: ...

    async def get(self, claim_id: str, citation_id: str) -> ResearchCitationDocument | None: ...

    async def list(
        self,
        *,
        claim_id: str | None = ...,
        source_id: str | None = ...,
        allowed_project_ids: tuple[str, ...] | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[ResearchCitationDocument]: ...


class ResearchSearch(Protocol):
    """The existing Phase 5 retrieval surface used only for relevant context."""

    async def search(
        self,
        *,
        project: ResearchProjectDocument,
        query: str,
        filters: ResearchSearchFilters | None = ...,
        retrieval_profile: ResearchRetrievalProfileName = ...,
        method: ResearchSearchMethod = ...,
        phrase: bool = ...,
        limit: int = ...,
        token_budget: int | None = ...,
        cursor: str | None = ...,
        allowed_project_ids: tuple[str, ...] | None = ...,
    ) -> ResearchSearchResult: ...


SourceSnapshotRepositoryFactory = Callable[[str], SourceSnapshotStore]
ResearchChunkRepositoryFactory = Callable[[str], ResearchChunkStore]
ResearchClaimRepositoryFactory = Callable[[str], ResearchClaimStore]
ResearchCitationRepositoryFactory = Callable[[str], ResearchCitationStore]


@dataclass(frozen=True, slots=True)
class CitationEvidence:
    """One authorized citation joined to its claim and immutable source version."""

    citation: ResearchCitationDocument
    claim: ResearchClaimDocument
    source: ResearchSourceDocument
    snapshot: SourceSnapshotDocument


@dataclass(frozen=True, slots=True)
class EvidenceConflict:
    """Explicitly reported conflict; it deliberately has no winning side."""

    claim_id: str
    statement: str
    supporting: tuple[CitationEvidence, ...]
    contradicting: tuple[CitationEvidence, ...]


@dataclass(frozen=True, slots=True)
class EvidencePacket:
    """A token-bounded read model; packet construction does not alter persistence."""

    question: str
    research_project_id: str
    relevant_context: tuple[ResearchSearchItem, ...]
    supporting_evidence: tuple[CitationEvidence, ...]
    contradicting_evidence: tuple[CitationEvidence, ...]
    qualifying_evidence: tuple[CitationEvidence, ...]
    background_evidence: tuple[CitationEvidence, ...]
    source_metadata: tuple[CitationEvidence, ...]
    citation_locators: tuple[CitationLocator, ...]
    conflicts: tuple[EvidenceConflict, ...]
    uncertainties: tuple[str, ...]
    token_budget: int
    tokens_used: int


@tenant_service
class ResearchEvidenceService:
    """Author citations and build read-only, provenance-preserving evidence packets."""

    def __init__(
        self,
        projects: ResearchProjectLookup,
        sources: ResearchSourceCatalog,
        snapshots: SourceSnapshotRepositoryFactory,
        chunks: ResearchChunkRepositoryFactory,
        claims: ResearchClaimRepositoryFactory,
        citations: ResearchCitationRepositoryFactory,
        search: ResearchSearch,
        *,
        token_budget: int = 8_000,
        max_token_budget: int = MAX_TOKEN_BUDGET,
    ) -> None:
        resolve_token_budget(None, default=token_budget, maximum=max_token_budget)
        self._projects = projects
        self._sources = sources
        self._snapshots = snapshots
        self._chunks = chunks
        self._claims = claims
        self._citations = citations
        self._search = search
        self._token_budget = token_budget
        self._max_token_budget = max_token_budget

    async def create_citation(
        self,
        *,
        research_project_id: str,
        citation_id: str,
        claim_id: str,
        source_id: str,
        snapshot_id: str,
        chunk_id: str | None,
        locator: CitationLocator,
        evidence_excerpt: str,
        relation: CitationRelation,
        created_by: str | None,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> ResearchCitationDocument:
        """Store an explicit relation after checking every provenance edge.

        The supplied relation remains an authored assertion.  The service does
        not change the claim's state or confidence, even if an excerpt matches
        its statement exactly.
        """
        project = await self._require_project(research_project_id)
        claim = await self._require_claim(research_project_id, claim_id, allowed_project_ids)
        source = await self._require_source(research_project_id, source_id, allowed_project_ids)
        snapshot = await self._require_snapshot(
            research_project_id, source_id, snapshot_id, allowed_project_ids
        )
        resolved_locator = locator
        if chunk_id is not None:
            chunk = await self._chunks(research_project_id).get_by_source_snapshot_chunk(
                source_id, snapshot_id, chunk_id
            )
            if chunk is None or not self._allowed(chunk.authorization, allowed_project_ids):
                raise ResearchCitationProvenanceError("research citation chunk was not found")
            if evidence_excerpt not in chunk.content:
                raise ResearchCitationProvenanceError(
                    "citation evidence excerpt must occur in the cited research chunk"
                )
            resolved_locator = self._merge_chunk_locator(chunk.locator, locator)
        else:
            self._validate_source_locator(source, locator)
        document = ResearchCitationDocument(
            research_project_id=research_project_id,
            claim_id=claim.claim_id,
            citation_id=citation_id,
            source_id=source.source_id,
            snapshot_id=snapshot.snapshot_id,
            chunk_id=chunk_id,
            # Preserve source class as descriptive metadata at citation time;
            # it is never used to infer factual confidence.
            source_class=source.source_class,
            locator=resolved_locator,
            evidence_excerpt=evidence_excerpt,
            relation=relation,
            authorization=self._authorization(project, source, claim, created_by),
        )
        try:
            return await self._citations(research_project_id).create(document)
        except DuplicateDocumentError as error:
            raise ResearchCitationAlreadyExistsError("research citation already exists") from error

    async def get_claim_evidence(
        self,
        *,
        research_project_id: str,
        claim_id: str,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> tuple[CitationEvidence, ...]:
        """Return only fully authorized persisted evidence for one explicit claim."""
        claim = await self._require_claim(research_project_id, claim_id, allowed_project_ids)
        page = await self._citations(research_project_id).list(
            claim_id=claim.claim_id,
            allowed_project_ids=allowed_project_ids,
            limit=50,
        )
        evidence = await self._materialize(page.items, allowed_project_ids)
        return tuple(item for item in evidence if item.claim.claim_id == claim.claim_id)

    async def build_evidence_packet(
        self,
        *,
        query: str,
        research_project_id: str,
        project_id: str | None,
        token_budget: int | None,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> EvidencePacket:
        """Assemble relevant context and authored evidence without choosing a truth claim."""
        project = await self._require_project(research_project_id)
        if project_id is not None and project_id not in project.related_project_ids:
            raise ResearchCitationProvenanceError(
                "project_id is not linked to this research project"
            )
        budget = resolve_token_budget(
            token_budget, default=self._token_budget, maximum=self._max_token_budget
        )
        # Reserve room for direct citations so an otherwise full retrieval
        # packet cannot suppress contradictory primary evidence.
        context_budget = max(100, budget // 2)
        citation_budget = budget - context_budget
        search_result = await self._search.search(
            project=project,
            query=query,
            filters=ResearchSearchFilters(related_project_id=project_id),
            retrieval_profile="research_general",
            method="hybrid",
            limit=25,
            token_budget=context_budget,
            allowed_project_ids=allowed_project_ids,
        )
        citations = await self._list_authorized_citations(research_project_id, allowed_project_ids)
        evidence = await self._materialize(citations, allowed_project_ids)
        relevant = self._relevant_evidence(evidence, search_result.items)
        grouped = self._group_by_relation(relevant)
        selected, evidence_tokens = self._pack_evidence(grouped, citation_budget)
        sources = self._sources_for(selected, relevant)
        conflicts = self._conflicts(relevant)
        uncertainties = self._uncertainties(relevant, conflicts)
        locators = tuple(item.citation.locator for items in selected.values() for item in items)
        context_tokens = sum(item.tokens for item in search_result.items)
        return EvidencePacket(
            question=query,
            research_project_id=research_project_id,
            relevant_context=search_result.items,
            supporting_evidence=selected[CitationRelation.SUPPORTS],
            contradicting_evidence=selected[CitationRelation.CONTRADICTS],
            qualifying_evidence=selected[CitationRelation.QUALIFIES],
            background_evidence=selected[CitationRelation.BACKGROUND],
            source_metadata=sources,
            citation_locators=locators,
            conflicts=conflicts,
            uncertainties=uncertainties,
            token_budget=budget,
            tokens_used=context_tokens + evidence_tokens,
        )

    async def _list_authorized_citations(
        self, research_project_id: str, allowed_project_ids: tuple[str, ...] | None
    ) -> tuple[ResearchCitationDocument, ...]:
        """Read a bounded evidence universe; packets never page the whole collection."""
        page = await self._citations(research_project_id).list(
            allowed_project_ids=allowed_project_ids, limit=50
        )
        return page.items

    async def _materialize(
        self,
        citations: Sequence[ResearchCitationDocument],
        allowed_project_ids: tuple[str, ...] | None,
    ) -> tuple[CitationEvidence, ...]:
        """Join only records that still satisfy all stored provenance/auth boundaries."""
        evidence: list[CitationEvidence] = []
        for citation in citations:
            if not self._allowed(citation.authorization, allowed_project_ids):
                continue
            claim = await self._claims(citation.research_project_id).get(citation.claim_id)
            source = await self._sources.get(citation.source_id)
            snapshot = await self._snapshots(citation.research_project_id).get(
                citation.source_id, citation.snapshot_id
            )
            if (
                claim is None
                or source is None
                or snapshot is None
                or source.research_project_id != citation.research_project_id
                or not self._allowed(claim.authorization, allowed_project_ids)
                or not self._allowed(source.authorization, allowed_project_ids)
                or not self._allowed(snapshot.authorization, allowed_project_ids)
            ):
                continue
            evidence.append(
                CitationEvidence(citation=citation, claim=claim, source=source, snapshot=snapshot)
            )
        return tuple(evidence)

    @staticmethod
    def _relevant_evidence(
        evidence: Sequence[CitationEvidence], items: Sequence[ResearchSearchItem]
    ) -> tuple[CitationEvidence, ...]:
        """Use retrieval only for relevance, never to create or classify evidence."""
        chunk_ids = {item.identifier for item in items if item.kind == "chunk" and item.identifier}
        claim_ids = {item.identifier for item in items if item.kind == "claim" and item.identifier}
        return tuple(
            item
            for item in evidence
            if item.citation.chunk_id in chunk_ids or item.citation.claim_id in claim_ids
        )

    @staticmethod
    def _group_by_relation(
        evidence: Sequence[CitationEvidence],
    ) -> dict[CitationRelation, tuple[CitationEvidence, ...]]:
        return {
            relation: tuple(item for item in evidence if item.citation.relation is relation)
            for relation in CitationRelation
        }

    @staticmethod
    def _pack_evidence(
        grouped: dict[CitationRelation, tuple[CitationEvidence, ...]], budget: int
    ) -> tuple[dict[CitationRelation, tuple[CitationEvidence, ...]], int]:
        """Fit complete excerpts, not partial quotations, into the reserved budget."""
        selected: dict[CitationRelation, list[CitationEvidence]] = {
            relation: [] for relation in CitationRelation
        }
        used = 0
        # Alternate relationships to retain disagreement when competing sources
        # fit.  This ordering is presentation policy, never a truth ranking.
        remaining = {relation: list(grouped[relation]) for relation in CitationRelation}
        while any(remaining.values()):
            progressed = False
            for relation in CitationRelation:
                if not remaining[relation]:
                    continue
                item = remaining[relation].pop(0)
                tokens = estimate_tokens(item.citation.evidence_excerpt)
                if used + tokens > budget:
                    continue
                selected[relation].append(item)
                used += tokens
                progressed = True
            if not progressed:
                break
        return {relation: tuple(items) for relation, items in selected.items()}, used

    @staticmethod
    def _sources_for(
        selected: dict[CitationRelation, tuple[CitationEvidence, ...]],
        relevant: Sequence[CitationEvidence],
    ) -> tuple[CitationEvidence, ...]:
        """Emit provenance metadata for selected evidence and detected conflicts."""
        ordered = [item for items in selected.values() for item in items]
        # A conflict remains useful even where its excerpt does not fit. Include
        # its source metadata so the disagreement is auditable rather than hidden.
        for item in relevant:
            if item not in ordered:
                ordered.append(item)
        seen: set[tuple[str, str]] = set()
        sources: list[CitationEvidence] = []
        for item in ordered:
            key = (item.source.source_id, item.snapshot.snapshot_id)
            if key not in seen:
                seen.add(key)
                sources.append(item)
        return tuple(sources)

    @staticmethod
    def _conflicts(evidence: Sequence[CitationEvidence]) -> tuple[EvidenceConflict, ...]:
        by_claim: dict[str, list[CitationEvidence]] = {}
        for item in evidence:
            by_claim.setdefault(item.claim.claim_id, []).append(item)
        conflicts: list[EvidenceConflict] = []
        for claim_id, items in by_claim.items():
            supporting = tuple(
                item for item in items if item.citation.relation is CitationRelation.SUPPORTS
            )
            contradicting = tuple(
                item for item in items if item.citation.relation is CitationRelation.CONTRADICTS
            )
            if supporting and contradicting:
                conflicts.append(
                    EvidenceConflict(
                        claim_id=claim_id,
                        statement=items[0].claim.statement,
                        supporting=supporting,
                        contradicting=contradicting,
                    )
                )
        return tuple(conflicts)

    @staticmethod
    def _uncertainties(
        evidence: Sequence[CitationEvidence], conflicts: Sequence[EvidenceConflict]
    ) -> tuple[str, ...]:
        values: list[str] = []
        if not evidence:
            values.append("No persisted citation matched the retrieved context.")
        for conflict in conflicts:
            values.append(
                f"Persisted evidence conflicts for claim {conflict.claim_id}; no side was selected."
            )
        for item in evidence:
            if item.claim.status.value in {
                "unreviewed",
                "partially_supported",
                "contested",
                "outdated",
            }:
                values.append(
                    f"Claim {item.claim.claim_id} remains explicitly {item.claim.status.value}."
                )
        return tuple(dict.fromkeys(values))

    async def _require_project(self, research_project_id: str) -> ResearchProjectDocument:
        project = await self._projects.get(research_project_id)
        if project is None:
            raise ResearchProjectNotFoundError("research project was not found")
        return project

    async def _require_claim(
        self,
        research_project_id: str,
        claim_id: str,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> ResearchClaimDocument:
        claim = await self._claims(research_project_id).get(claim_id)
        if claim is None or not self._allowed(claim.authorization, allowed_project_ids):
            raise ResearchClaimNotFoundError("research claim was not found")
        return claim

    async def _require_source(
        self,
        research_project_id: str,
        source_id: str,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> ResearchSourceDocument:
        source = await self._sources.get(source_id)
        if (
            source is None
            or source.research_project_id != research_project_id
            or not self._allowed(source.authorization, allowed_project_ids)
        ):
            raise ResearchCitationProvenanceError("research citation source was not found")
        return source

    async def _require_snapshot(
        self,
        research_project_id: str,
        source_id: str,
        snapshot_id: str,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> SourceSnapshotDocument:
        snapshot = await self._snapshots(research_project_id).get(source_id, snapshot_id)
        if snapshot is None or not self._allowed(snapshot.authorization, allowed_project_ids):
            raise ResearchCitationProvenanceError("research citation snapshot was not found")
        return snapshot

    @staticmethod
    def _merge_chunk_locator(chunk: CitationLocator, requested: CitationLocator) -> CitationLocator:
        """Keep source-supported location detail while refusing contradictory locators."""
        fields = ("url", "anchor", "path", "page", "line_start", "line_end", "commit")
        chunk_values = chunk.model_dump(exclude_none=True)
        requested_values = requested.model_dump(exclude_none=True)
        for name in fields:
            if (
                name in chunk_values
                and name in requested_values
                and chunk_values[name] != requested_values[name]
            ):
                raise ResearchCitationProvenanceError(
                    "citation locator conflicts with the cited research chunk"
                )
        return CitationLocator.model_validate({**chunk_values, **requested_values})

    @staticmethod
    def _validate_source_locator(source: ResearchSourceDocument, locator: CitationLocator) -> None:
        """A source-level citation cannot silently point at another origin."""
        if locator.url is None:
            return
        candidate = urlsplit(locator.url)
        known = {source.uri, source.canonical_uri}
        known_hosts: set[str] = set()
        for uri in known:
            if uri is None:
                continue
            hostname = urlsplit(uri).hostname
            if hostname is not None:
                known_hosts.add(hostname.casefold())
        if candidate.hostname is not None and candidate.hostname.casefold() not in known_hosts:
            raise ResearchCitationProvenanceError(
                "citation URL locator does not belong to the registered source"
            )

    @staticmethod
    def _authorization(
        project: ResearchProjectDocument,
        source: ResearchSourceDocument,
        claim: ResearchClaimDocument,
        owner_key_id: str | None,
    ) -> ResearchAuthorization:
        """Require future readers to retain every project grant protecting the relation."""
        project_ids = sorted(
            set(project.authorization.project_ids)
            | set(source.authorization.project_ids)
            | set(claim.authorization.project_ids)
        )
        return ResearchAuthorization(project_ids=project_ids, owner_key_id=owner_key_id)

    @staticmethod
    def _allowed(
        authorization: ResearchAuthorization, allowed_project_ids: tuple[str, ...] | None
    ) -> bool:
        return allowed_project_ids is None or set(authorization.project_ids).issubset(
            allowed_project_ids
        )


__all__ = [
    "CitationEvidence",
    "EvidenceConflict",
    "EvidencePacket",
    "ResearchEvidenceService",
]
