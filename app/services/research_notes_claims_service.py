"""Business rules for V3 Phase 6 working notes and explicitly assessed claims.

This service persists user-authored research artifacts only.  It does not run
retrieval to infer a claim state, create citations, promote anything to V1
memory or project context, modify skills, or make an automatic truth decision.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from app.db.research_search import ResearchSearchScope
from app.models.common import Page
from app.models.research import (
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
    SourceSnapshotDocument,
)
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError
from app.repositories.research_repository import ResearchNoteSearchMatch
from app.research.errors import (
    ResearchCitationNotFoundError,
    ResearchClaimAlreadyExistsError,
    ResearchClaimNotFoundError,
    ResearchNoteAlreadyExistsError,
    ResearchNoteNotFoundError,
    ResearchProjectNotFoundError,
    ResearchSourceReferenceNotFoundError,
)


class ResearchProjectLookup(Protocol):
    """The research-project boundary inherited by all Phase 6 records."""

    async def get(self, research_project_id: str) -> ResearchProjectDocument | None: ...


class ResearchSourceCatalog(Protocol):
    """Identity lookup; authorization remains a service and API concern."""

    async def get(self, source_id: str) -> ResearchSourceDocument | None: ...


class SourceSnapshotLookup(Protocol):
    """Read only a source snapshot inside an already-known research project."""

    async def get(self, source_id: str, snapshot_id: str) -> SourceSnapshotDocument | None: ...


class ResearchNoteStore(Protocol):
    """Project-scoped persistence required for working notes."""

    async def create(self, document: ResearchNoteDocument) -> ResearchNoteDocument: ...

    async def get(self, note_id: str) -> ResearchNoteDocument | None: ...

    async def list(
        self,
        *,
        status: ResearchNoteStatus | None = ...,
        allowed_project_ids: tuple[str, ...] | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[ResearchNoteDocument]: ...

    async def text_search(
        self,
        query: str,
        *,
        limit: int,
        phrase: bool,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchNoteSearchMatch, ...]: ...

    async def update(
        self, document: ResearchNoteDocument, *, expected_revision: int
    ) -> ResearchNoteDocument: ...


class ResearchClaimStore(Protocol):
    """Project-scoped persistence for explicit, human-supplied claim state."""

    async def create(self, document: ResearchClaimDocument) -> ResearchClaimDocument: ...

    async def get(self, claim_id: str) -> ResearchClaimDocument | None: ...

    async def list(
        self,
        *,
        status: ClaimState | None = ...,
        allowed_project_ids: tuple[str, ...] | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[ResearchClaimDocument]: ...

    async def update(
        self, document: ResearchClaimDocument, *, expected_revision: int
    ) -> ResearchClaimDocument: ...


class ResearchCitationStore(Protocol):
    """Existing Phase 0 citation persistence, read-only for this phase."""

    async def get(self, claim_id: str, citation_id: str) -> ResearchCitationDocument | None: ...


ResearchNoteRepositoryFactory = Callable[[str], ResearchNoteStore]
ResearchClaimRepositoryFactory = Callable[[str], ResearchClaimStore]
SourceSnapshotRepositoryFactory = Callable[[str], SourceSnapshotLookup]
ResearchCitationRepositoryFactory = Callable[[str], ResearchCitationStore]


class ResearchNotesClaimsService:
    """Keep notes, claims, provenance links, and assessments explicitly authored."""

    def __init__(
        self,
        projects: ResearchProjectLookup,
        sources: ResearchSourceCatalog,
        snapshots: SourceSnapshotRepositoryFactory,
        notes: ResearchNoteRepositoryFactory,
        claims: ResearchClaimRepositoryFactory,
        citations: ResearchCitationRepositoryFactory,
    ) -> None:
        self._projects = projects
        self._sources = sources
        self._snapshots = snapshots
        self._notes = notes
        self._claims = claims
        self._citations = citations

    async def create_note(
        self,
        *,
        research_project_id: str,
        note_id: str,
        title: str,
        body: str,
        source_refs: Sequence[ResearchSourceReference],
        status: ResearchNoteStatus,
        created_by: str | None,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> ResearchNoteDocument:
        """Create a working artifact after checking project-local source provenance."""
        project = await self._require_project(research_project_id)
        refs = list(source_refs)
        await self._validate_source_refs(
            research_project_id=research_project_id,
            source_refs=refs,
            allowed_project_ids=allowed_project_ids,
        )
        document = ResearchNoteDocument(
            research_project_id=research_project_id,
            note_id=note_id,
            title=title,
            body=body,
            source_refs=refs,
            status=status,
            created_by=created_by,
            authorization=self._authorization(project, created_by),
        )
        try:
            return await self._notes(research_project_id).create(document)
        except DuplicateDocumentError as error:
            raise ResearchNoteAlreadyExistsError("research note already exists") from error

    async def get_note(self, *, research_project_id: str, note_id: str) -> ResearchNoteDocument:
        """Load a note; the caller performs both current and historical scope checks."""
        note = await self._notes(research_project_id).get(note_id)
        if note is None:
            raise ResearchNoteNotFoundError("research note was not found")
        return note

    async def update_note(
        self,
        *,
        research_project_id: str,
        note_id: str,
        expected_revision: int,
        changes: Mapping[str, object],
        allowed_project_ids: tuple[str, ...] | None,
    ) -> ResearchNoteDocument:
        """Revision-guard a note while retaining its original authorization metadata."""
        current = await self.get_note(research_project_id=research_project_id, note_id=note_id)
        if current.revision != expected_revision:
            raise ConcurrencyConflictError("research note changed after it was read")
        if "source_refs" in changes:
            refs = changes["source_refs"]
            if not isinstance(refs, list) or not all(
                isinstance(item, ResearchSourceReference) for item in refs
            ):
                raise ValueError("source_refs must be research source references")
            await self._validate_source_refs(
                research_project_id=research_project_id,
                source_refs=refs,
                allowed_project_ids=allowed_project_ids,
            )
        payload = current.model_dump(by_alias=True, mode="python")
        payload.update(changes)
        updated = ResearchNoteDocument.model_validate(payload)
        return await self._notes(research_project_id).update(
            updated, expected_revision=expected_revision
        )

    async def search_notes(
        self,
        *,
        research_project_id: str,
        query: str,
        phrase: bool,
        limit: int,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> tuple[ResearchNoteSearchMatch, ...]:
        """Run bounded lexical note search and filter retained historical authorization."""
        await self._require_project(research_project_id)
        matches = await self._notes(research_project_id).text_search(
            query,
            limit=limit,
            phrase=phrase,
            scope=ResearchSearchScope(research_project_id=research_project_id),
        )
        return tuple(
            match
            for match in matches
            if self._allowed(match.note.authorization, allowed_project_ids)
        )

    async def create_claim(
        self,
        *,
        research_project_id: str,
        claim_id: str,
        statement: str,
        status: ClaimState,
        confidence: float | None,
        confidence_rationale: str | None,
        supporting_citation_ids: Sequence[str],
        contradicting_citation_ids: Sequence[str],
        created_by: str | None,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> ResearchClaimDocument:
        """Persist the supplied assessment without searching or deriving its status."""
        project = await self._require_project(research_project_id)
        supporting = list(supporting_citation_ids)
        contradicting = list(contradicting_citation_ids)
        await self._validate_citations(
            research_project_id=research_project_id,
            claim_id=claim_id,
            supporting_citation_ids=supporting,
            contradicting_citation_ids=contradicting,
            allowed_project_ids=allowed_project_ids,
        )
        document = ResearchClaimDocument(
            research_project_id=research_project_id,
            claim_id=claim_id,
            statement=statement,
            status=status,
            confidence=confidence,
            confidence_rationale=confidence_rationale,
            supporting_citation_ids=supporting,
            contradicting_citation_ids=contradicting,
            authorization=self._authorization(project, created_by),
        )
        try:
            return await self._claims(research_project_id).create(document)
        except DuplicateDocumentError as error:
            raise ResearchClaimAlreadyExistsError("research claim already exists") from error

    async def get_claim(self, *, research_project_id: str, claim_id: str) -> ResearchClaimDocument:
        """Load one claim without converting state into an access decision."""
        claim = await self._claims(research_project_id).get(claim_id)
        if claim is None:
            raise ResearchClaimNotFoundError("research claim was not found")
        return claim

    async def list_claims(
        self,
        *,
        research_project_id: str,
        status: ClaimState | None,
        allowed_project_ids: tuple[str, ...] | None,
        limit: int,
        cursor: str | None,
    ) -> Page[ResearchClaimDocument]:
        """Page only claims whose historical authorization is fully admitted."""
        return await self._claims(research_project_id).list(
            status=status,
            allowed_project_ids=allowed_project_ids,
            limit=limit,
            cursor=cursor,
        )

    async def update_claim(
        self,
        *,
        research_project_id: str,
        claim_id: str,
        expected_revision: int,
        changes: Mapping[str, object],
        allowed_project_ids: tuple[str, ...] | None,
    ) -> ResearchClaimDocument:
        """Revision-guard only an explicit claim edit; no state is inferred."""
        current = await self.get_claim(research_project_id=research_project_id, claim_id=claim_id)
        if current.revision != expected_revision:
            raise ConcurrencyConflictError("research claim changed after it was read")
        payload = current.model_dump(by_alias=True, mode="python")
        if "supporting_citation_ids" in changes:
            payload["supporting_citation_ids"] = changes["supporting_citation_ids"]
        if "contradicting_citation_ids" in changes:
            payload["contradicting_citation_ids"] = changes["contradicting_citation_ids"]
        payload.update(
            {
                key: value
                for key, value in changes.items()
                if key not in {"supporting_citation_ids", "contradicting_citation_ids"}
            }
        )
        updated = ResearchClaimDocument.model_validate(payload)
        await self._validate_citations(
            research_project_id=research_project_id,
            claim_id=claim_id,
            supporting_citation_ids=updated.supporting_citation_ids,
            contradicting_citation_ids=updated.contradicting_citation_ids,
            allowed_project_ids=allowed_project_ids,
        )
        return await self._claims(research_project_id).update(
            updated, expected_revision=expected_revision
        )

    async def _require_project(self, research_project_id: str) -> ResearchProjectDocument:
        project = await self._projects.get(research_project_id)
        if project is None:
            raise ResearchProjectNotFoundError("research project was not found")
        return project

    async def _validate_source_refs(
        self,
        *,
        research_project_id: str,
        source_refs: Sequence[ResearchSourceReference],
        allowed_project_ids: tuple[str, ...] | None,
    ) -> None:
        for reference in source_refs:
            source = await self._sources.get(reference.source_id)
            if (
                source is None
                or source.research_project_id != research_project_id
                or not self._allowed(source.authorization, allowed_project_ids)
            ):
                raise ResearchSourceReferenceNotFoundError(
                    "research source reference was not found"
                )
            if reference.snapshot_id is not None:
                snapshot = await self._snapshots(research_project_id).get(
                    source.source_id, reference.snapshot_id
                )
                if snapshot is None or not self._allowed(
                    snapshot.authorization, allowed_project_ids
                ):
                    raise ResearchSourceReferenceNotFoundError(
                        "research source reference was not found"
                    )

    async def _validate_citations(
        self,
        *,
        research_project_id: str,
        claim_id: str,
        supporting_citation_ids: Sequence[str],
        contradicting_citation_ids: Sequence[str],
        allowed_project_ids: tuple[str, ...] | None,
    ) -> None:
        relationships = tuple(
            (citation_id, CitationRelation.SUPPORTS) for citation_id in supporting_citation_ids
        ) + tuple(
            (citation_id, CitationRelation.CONTRADICTS)
            for citation_id in contradicting_citation_ids
        )
        for citation_id, expected_relation in relationships:
            citation = await self._citations(research_project_id).get(claim_id, citation_id)
            if (
                citation is None
                or citation.relation is not expected_relation
                or not self._allowed(citation.authorization, allowed_project_ids)
            ):
                raise ResearchCitationNotFoundError("research citation was not found")

    @staticmethod
    def _authorization(
        project: ResearchProjectDocument, owner_key_id: str | None
    ) -> ResearchAuthorization:
        return ResearchAuthorization(
            project_ids=list(project.authorization.project_ids), owner_key_id=owner_key_id
        )

    @staticmethod
    def _allowed(
        authorization: ResearchAuthorization, allowed_project_ids: tuple[str, ...] | None
    ) -> bool:
        return allowed_project_ids is None or set(authorization.project_ids).issubset(
            allowed_project_ids
        )


__all__ = ["ResearchNotesClaimsService"]
