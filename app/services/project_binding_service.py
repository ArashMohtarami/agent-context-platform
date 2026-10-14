"""Explicit project bindings and deterministic project resolution.

This service owns the policy that a workspace hint may only identify a project
when an operator created an exact binding.  Repositories only store and query
the rows; transports only serialize these outcomes.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from app.models.agent import (
    ProjectBindingDocument,
    ProjectBindingType,
    ProjectResolutionMatch,
    ProjectResolutionStatus,
)
from app.models.common import Page
from app.models.project import ProjectDocument
from app.projects.bindings import normalize_binding_value
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError


class ProjectBindingNotFoundError(Exception):
    """The binding was absent when an authorized caller tried to use it."""


class ProjectBindingDuplicateError(Exception):
    """The project already has this effective binding."""


class ProjectBindingConflictError(Exception):
    """The effective binding names a different project."""


class ProjectBindingProjectNotFoundError(Exception):
    """A binding cannot point to a V1 project that does not exist."""


class ProjectReader(Protocol):
    """The one V1 project lookup binding operations need."""

    async def get(self, project_id: str) -> ProjectDocument | None: ...


class ProjectBindingRepository(Protocol):
    """Persistence operations owned by the binding repository."""

    async def create(self, document: ProjectBindingDocument) -> ProjectBindingDocument: ...
    async def get(self, binding_id: str) -> ProjectBindingDocument | None: ...
    async def find_mappings(
        self,
        binding_type: ProjectBindingType,
        value: str,
        *,
        project_ids: Sequence[str] | None = ...,
    ) -> tuple[ProjectBindingDocument, ...]: ...
    async def find_repository_mappings(
        self,
        value: str,
        *,
        project_ids: Sequence[str] | None = ...,
    ) -> tuple[ProjectBindingDocument, ...]: ...
    async def list(
        self,
        *,
        project_id: str | None = ...,
        project_ids: Sequence[str] | None = ...,
        binding_type: ProjectBindingType | None = ...,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[ProjectBindingDocument]: ...
    async def update(
        self, document: ProjectBindingDocument, *, expected_revision: int
    ) -> ProjectBindingDocument: ...
    async def delete(self, binding_id: str, *, expected_revision: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class ProjectResolutionCandidate:
    """One project returned when deterministic lookup has more than one answer."""

    project_id: str
    matched_by: ProjectResolutionMatch
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ProjectResolution:
    """A resolver result that never silently chooses between candidates."""

    status: ProjectResolutionStatus
    project_id: str | None
    confidence: float
    matched_by: ProjectResolutionMatch | None
    candidates: tuple[ProjectResolutionCandidate, ...] = ()


class ProjectBindingService:
    """Manage explicit bindings and resolve them in documented priority order."""

    def __init__(self, projects: ProjectReader, bindings: ProjectBindingRepository) -> None:
        self._projects = projects
        self._bindings = bindings

    async def create_binding(
        self,
        *,
        project_id: str,
        binding_type: ProjectBindingType,
        value: str,
    ) -> ProjectBindingDocument:
        """Create a binding after proving its V1 project and mapping are safe."""
        await self._require_project(project_id)
        document = ProjectBindingDocument(
            binding_id=uuid4().hex,
            project_id=project_id,
            binding_type=binding_type,
            value=value,
        )
        await self._ensure_available(document)
        try:
            return await self._bindings.create(document)
        except DuplicateDocumentError as error:
            # The unique MongoDB index is the final race-safe guard.  A
            # concurrent creator cannot overwrite or replace a mapping.
            raise ProjectBindingConflictError("project binding already exists") from error

    async def get_binding(self, binding_id: str) -> ProjectBindingDocument:
        """Load one binding without making absence look like a project match."""
        document = await self._bindings.get(binding_id)
        if document is None:
            raise ProjectBindingNotFoundError("project binding was not found")
        return document

    async def list_bindings(
        self,
        *,
        project_id: str | None = None,
        allowed_project_ids: Sequence[str] | None = None,
        binding_type: ProjectBindingType | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ProjectBindingDocument]:
        """List only bindings within the caller's existing V1 project grant."""
        return await self._bindings.list(
            project_id=project_id,
            project_ids=allowed_project_ids,
            binding_type=binding_type,
            limit=limit,
            cursor=cursor,
        )

    async def update_binding(
        self,
        binding_id: str,
        *,
        expected_revision: int,
        project_id: str | None = None,
        binding_type: ProjectBindingType | None = None,
        value: str | None = None,
    ) -> ProjectBindingDocument:
        """Revision-guard a binding change and recheck mapping conflicts."""
        current = await self.get_binding(binding_id)
        changes: dict[str, object] = {}
        if project_id is not None:
            await self._require_project(project_id)
            changes["project_id"] = project_id
        if binding_type is not None:
            changes["binding_type"] = binding_type
        if value is not None:
            changes["value"] = value
        if not changes:
            raise ValueError("provide at least one project binding field to update")
        candidate = ProjectBindingDocument.model_validate(
            current.model_dump(by_alias=True, mode="python") | changes,
            context={"from_storage": True},
        )
        await self._ensure_available(candidate, excluding_binding_id=current.binding_id)
        try:
            return await self._bindings.update(candidate, expected_revision=expected_revision)
        except DuplicateDocumentError as error:
            raise ProjectBindingConflictError("project binding already exists") from error

    async def delete_binding(self, binding_id: str, *, expected_revision: int) -> None:
        """Delete only a revision an operator explicitly reviewed."""
        await self.get_binding(binding_id)
        deleted = await self._bindings.delete(binding_id, expected_revision=expected_revision)
        if not deleted:
            raise ConcurrencyConflictError("project binding was concurrently modified")

    async def resolve_project(
        self,
        *,
        project_id: str | None = None,
        git_remote: str | None = None,
        repository_url: str | None = None,
        repository_name: str | None = None,
        workspace_hint: str | None = None,
        allowed_project_ids: Sequence[str] | None = None,
    ) -> ProjectResolution:
        """Resolve only exact configured identifiers in fixed priority order."""
        if project_id is not None:
            if allowed_project_ids is not None and project_id not in allowed_project_ids:
                # The route/tool authorization layer normally refuses explicit
                # IDs first.  Keep the service non-leaky for other callers.
                return _no_match()
            project = await self._projects.get(project_id)
            if project is not None:
                return _resolved(project_id, ProjectResolutionMatch.EXPLICIT_PROJECT_ID)
            return _no_match()

        checks: tuple[tuple[ProjectResolutionMatch, ProjectBindingType, str | None], ...] = (
            (ProjectResolutionMatch.GIT_REMOTE, ProjectBindingType.GIT_REMOTE, git_remote),
            (
                ProjectResolutionMatch.REPOSITORY_URL,
                ProjectBindingType.REPOSITORY_URL,
                repository_url,
            ),
            (
                ProjectResolutionMatch.REPOSITORY_NAME,
                ProjectBindingType.REPOSITORY_NAME,
                repository_name,
            ),
            (
                ProjectResolutionMatch.WORKSPACE_ALIAS,
                ProjectBindingType.WORKSPACE_ALIAS,
                workspace_hint,
            ),
        )
        for matched_by, binding_type, supplied in checks:
            if supplied is None:
                continue
            normalized = normalize_binding_value(binding_type.value, supplied)
            if binding_type in {
                ProjectBindingType.GIT_REMOTE,
                ProjectBindingType.REPOSITORY_URL,
            }:
                matches = await self._bindings.find_repository_mappings(
                    normalized, project_ids=allowed_project_ids
                )
            else:
                matches = await self._bindings.find_mappings(
                    binding_type, normalized, project_ids=allowed_project_ids
                )
            outcome = _outcome(matches, matched_by)
            if outcome.status != ProjectResolutionStatus.NO_MATCH:
                return outcome
        return _no_match()

    async def _require_project(self, project_id: str) -> None:
        if await self._projects.get(project_id) is None:
            raise ProjectBindingProjectNotFoundError("project was not found")

    async def _ensure_available(
        self,
        candidate: ProjectBindingDocument,
        *,
        excluding_binding_id: str | None = None,
    ) -> None:
        """Reject duplicates and cross-project conflicts before write time."""
        if candidate.binding_type in {
            ProjectBindingType.GIT_REMOTE,
            ProjectBindingType.REPOSITORY_URL,
        }:
            existing = await self._bindings.find_repository_mappings(candidate.value)
        else:
            existing = await self._bindings.find_mappings(candidate.binding_type, candidate.value)
        existing = tuple(
            binding for binding in existing if binding.binding_id != excluding_binding_id
        )
        if not existing:
            return
        if any(binding.project_id != candidate.project_id for binding in existing):
            raise ProjectBindingConflictError(
                "this repository or workspace identifier is already bound to another project"
            )
        raise ProjectBindingDuplicateError("this project binding already exists")


def _outcome(
    matches: Sequence[ProjectBindingDocument], matched_by: ProjectResolutionMatch
) -> ProjectResolution:
    """Turn exact rows into a single answer or an explicit candidate set."""
    project_ids = tuple(sorted({item.project_id for item in matches}))
    if not project_ids:
        return _no_match()
    if len(project_ids) == 1:
        return _resolved(project_ids[0], matched_by)
    return ProjectResolution(
        status=ProjectResolutionStatus.AMBIGUOUS,
        project_id=None,
        confidence=0.0,
        matched_by=matched_by,
        candidates=tuple(
            ProjectResolutionCandidate(project_id=project_id, matched_by=matched_by)
            for project_id in project_ids
        ),
    )


def _resolved(project_id: str, matched_by: ProjectResolutionMatch) -> ProjectResolution:
    return ProjectResolution(
        status=ProjectResolutionStatus.RESOLVED,
        project_id=project_id,
        confidence=1.0,
        matched_by=matched_by,
    )


def _no_match() -> ProjectResolution:
    return ProjectResolution(
        status=ProjectResolutionStatus.NO_MATCH,
        project_id=None,
        confidence=0.0,
        matched_by=None,
    )
