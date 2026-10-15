"""V3 Phase 11 generated skill update proposals.

This service records cited, review-only Markdown patch proposals. It can read the
current V1 skill metadata to prevent a stale patch base, but has no dependency
that can write canonical skill Markdown, Git, the V1 importer, skill chunks,
or the V1 ``skills`` collection.
"""

from collections.abc import Callable, Sequence
from typing import Protocol

from app.models.common import Page
from app.models.research import (
    ResearchAuthorization,
    ResearchCitationDocument,
    ResearchClaimDocument,
    ResearchProjectDocument,
    SkillUpdateProposalDocument,
    SkillUpdateProposalStatus,
)
from app.models.skill import SemanticVersion, SkillDocument
from app.repositories.errors import DuplicateDocumentError
from app.research.errors import (
    ResearchProjectNotFoundError,
    SkillUpdateProposalAlreadyExistsError,
    SkillUpdateProposalNotFoundError,
    SkillUpdateProposalProvenanceError,
    SkillUpdateProposalSkillNotFoundError,
    SkillUpdateProposalVersionMismatchError,
)
from app.security.tenant_service import tenant_service


class ResearchProjectLookup(Protocol):
    """Read the research-project boundary that owns a proposal."""

    async def get(self, research_project_id: str) -> ResearchProjectDocument | None: ...


class V1SkillLookup(Protocol):
    """Read only the active imported canonical skill metadata."""

    async def get_active(self, skill_id: str) -> SkillDocument | None: ...


class ResearchClaimStore(Protocol):
    """Read exact claims; no state assessment or mutation is available here."""

    async def get(self, claim_id: str) -> ResearchClaimDocument | None: ...


class ResearchCitationStore(Protocol):
    """Read exact citations under their claim key; no evidence is changed here."""

    async def get(self, claim_id: str, citation_id: str) -> ResearchCitationDocument | None: ...


class SkillUpdateProposalStore(Protocol):
    """Project-scoped persistence for inert generated proposals."""

    async def create(
        self, document: SkillUpdateProposalDocument
    ) -> SkillUpdateProposalDocument: ...

    async def get(self, proposal_id: str) -> SkillUpdateProposalDocument | None: ...

    async def list(
        self,
        *,
        status: SkillUpdateProposalStatus | None = ...,
        skill_id: str | None = ...,
        allowed_project_ids: tuple[str, ...] | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[SkillUpdateProposalDocument]: ...


ResearchClaimRepositoryFactory = Callable[[str], ResearchClaimStore]
ResearchCitationRepositoryFactory = Callable[[str], ResearchCitationStore]
SkillUpdateProposalRepositoryFactory = Callable[[str], SkillUpdateProposalStore]


@tenant_service
class SkillUpdateProposalService:
    """Create, read, list, and export generated patches without applying them."""

    def __init__(
        self,
        projects: ResearchProjectLookup,
        skills: V1SkillLookup,
        claims: ResearchClaimRepositoryFactory,
        citations: ResearchCitationRepositoryFactory,
        proposals: SkillUpdateProposalRepositoryFactory,
    ) -> None:
        self._projects = projects
        self._skills = skills
        self._claims = claims
        self._citations = citations
        self._proposals = proposals

    async def create_proposal(
        self,
        *,
        research_project_id: str,
        proposal_id: str,
        skill_id: str,
        current_version: SemanticVersion,
        proposed_change: str,
        proposed_patch: str,
        source_claim_ids: Sequence[str],
        source_citation_ids: Sequence[str],
        reason: str,
        created_by: str | None,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> SkillUpdateProposalDocument:
        """Store one generated patch after validating its exact current base and evidence."""
        project = await self._require_project(research_project_id)
        current = await self._skills.get_active(skill_id)
        if current is None:
            raise SkillUpdateProposalSkillNotFoundError("canonical skill was not found")
        if current.version != current_version:
            raise SkillUpdateProposalVersionMismatchError(
                "current_skill_version does not match the active canonical skill"
            )
        provenance_authorizations = await self._validate_provenance(
            research_project_id=research_project_id,
            source_claim_ids=source_claim_ids,
            source_citation_ids=source_citation_ids,
            allowed_project_ids=allowed_project_ids,
        )
        document = SkillUpdateProposalDocument(
            research_project_id=research_project_id,
            proposal_id=proposal_id,
            skill_id=skill_id,
            current_version=current_version,
            proposed_change=proposed_change,
            proposed_patch=proposed_patch,
            source_claim_ids=list(source_claim_ids),
            source_citation_ids=list(source_citation_ids),
            reason=reason,
            status=SkillUpdateProposalStatus.PENDING_REVIEW,
            trust_level="generated",
            authorization=self._authorization(
                (project.authorization, *provenance_authorizations), created_by
            ),
        )
        try:
            return await self._proposals(research_project_id).create(document)
        except DuplicateDocumentError as error:
            raise SkillUpdateProposalAlreadyExistsError(
                "skill update proposal already exists"
            ) from error

    async def get_proposal(
        self,
        *,
        research_project_id: str,
        proposal_id: str,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> SkillUpdateProposalDocument:
        """Read one proposal only if every preserved V1 project grant remains available."""
        proposal = await self._proposals(research_project_id).get(proposal_id)
        if proposal is None or not self._allowed(proposal.authorization, allowed_project_ids):
            raise SkillUpdateProposalNotFoundError("skill update proposal was not found")
        return proposal

    async def list_proposals(
        self,
        *,
        research_project_id: str,
        status: SkillUpdateProposalStatus | None,
        skill_id: str | None,
        allowed_project_ids: tuple[str, ...] | None,
        limit: int,
        cursor: str | None,
    ) -> Page[SkillUpdateProposalDocument]:
        """Cursor-page proposals with the database complete-grant filter intact."""
        return await self._proposals(research_project_id).list(
            status=status,
            skill_id=skill_id,
            allowed_project_ids=allowed_project_ids,
            limit=limit,
            cursor=cursor,
        )

    async def export_patch(
        self,
        *,
        research_project_id: str,
        proposal_id: str,
        allowed_project_ids: tuple[str, ...] | None,
    ) -> SkillUpdateProposalDocument:
        """Return stored inert patch text; callers decide whether to review it outside this API."""
        return await self.get_proposal(
            research_project_id=research_project_id,
            proposal_id=proposal_id,
            allowed_project_ids=allowed_project_ids,
        )

    async def _validate_provenance(
        self,
        *,
        research_project_id: str,
        source_claim_ids: Sequence[str],
        source_citation_ids: Sequence[str],
        allowed_project_ids: tuple[str, ...] | None,
    ) -> tuple[ResearchAuthorization, ...]:
        """Require exact, project-local evidence without inferring truth from it."""
        if not source_claim_ids:
            raise SkillUpdateProposalProvenanceError(
                "skill update proposals require at least one source claim"
            )
        if len(source_claim_ids) != len(set(source_claim_ids)):
            raise SkillUpdateProposalProvenanceError(
                "proposal source claim identifiers must be unique"
            )
        if len(source_citation_ids) != len(set(source_citation_ids)):
            raise SkillUpdateProposalProvenanceError(
                "proposal source citation identifiers must be unique"
            )
        claims: list[ResearchClaimDocument] = []
        authorizations: list[ResearchAuthorization] = []
        for claim_id in source_claim_ids:
            claim = await self._claims(research_project_id).get(claim_id)
            if (
                claim is None
                or claim.research_project_id != research_project_id
                or not self._allowed(claim.authorization, allowed_project_ids)
            ):
                raise SkillUpdateProposalProvenanceError(
                    "proposal source claim was not found or authorized"
                )
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
                raise SkillUpdateProposalProvenanceError(
                    "proposal source citation must belong to exactly one authorized source claim"
                )
            authorizations.append(matches[0].authorization)
        return tuple(authorizations)

    async def _require_project(self, research_project_id: str) -> ResearchProjectDocument:
        project = await self._projects.get(research_project_id)
        if project is None:
            raise ResearchProjectNotFoundError("research project was not found")
        return project

    @staticmethod
    def _authorization(
        authorizations: Sequence[ResearchAuthorization], owner_key_id: str | None
    ) -> ResearchAuthorization:
        project_ids = {project_id for item in authorizations for project_id in item.project_ids}
        return ResearchAuthorization(project_ids=sorted(project_ids), owner_key_id=owner_key_id)

    @staticmethod
    def _allowed(
        authorization: ResearchAuthorization, allowed_project_ids: tuple[str, ...] | None
    ) -> bool:
        return allowed_project_ids is None or set(authorization.project_ids).issubset(
            allowed_project_ids
        )
