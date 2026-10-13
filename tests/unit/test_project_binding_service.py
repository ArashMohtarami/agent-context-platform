"""V4 Phase 4 deterministic project binding and resolution rules."""

from collections.abc import Sequence

import pytest

from app.models.agent import ProjectBindingDocument, ProjectBindingType, ProjectResolutionStatus
from app.models.common import Page
from app.projects.bindings import ProjectBindingValueError, normalize_repository_locator
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError
from app.services.project_binding_service import (
    ProjectBindingConflictError,
    ProjectBindingDuplicateError,
    ProjectBindingService,
)
from tests.project_doubles import FakeProjectRepository, project

PAYMENTS = "payment-service"
WALLET = "wallet-service"


def binding(
    binding_id: str,
    project_id: str,
    binding_type: ProjectBindingType,
    value: str,
) -> ProjectBindingDocument:
    return ProjectBindingDocument(
        binding_id=binding_id,
        project_id=project_id,
        binding_type=binding_type,
        value=value,
    )


class Bindings:
    """In-memory binding persistence preserving the production read semantics."""

    def __init__(self, documents: Sequence[ProjectBindingDocument] = ()) -> None:
        self.documents = {document.binding_id: document for document in documents}

    async def create(self, document: ProjectBindingDocument) -> ProjectBindingDocument:
        if document.binding_id in self.documents:
            raise DuplicateDocumentError("duplicate")
        if any(
            item.binding_type == document.binding_type and item.value == document.value
            for item in self.documents.values()
        ):
            raise DuplicateDocumentError("duplicate")
        self.documents[document.binding_id] = document
        return document

    async def get(self, binding_id: str) -> ProjectBindingDocument | None:
        return self.documents.get(binding_id)

    async def find_mappings(
        self,
        binding_type: ProjectBindingType,
        value: str,
        *,
        project_ids: Sequence[str] | None = None,
    ) -> tuple[ProjectBindingDocument, ...]:
        return tuple(
            item
            for item in self.documents.values()
            if item.binding_type == binding_type
            and item.value == value
            and (project_ids is None or item.project_id in project_ids)
        )

    async def find_repository_mappings(
        self,
        value: str,
        *,
        project_ids: Sequence[str] | None = None,
    ) -> tuple[ProjectBindingDocument, ...]:
        return tuple(
            item
            for item in self.documents.values()
            if item.binding_type
            in {ProjectBindingType.GIT_REMOTE, ProjectBindingType.REPOSITORY_URL}
            and item.value == value
            and (project_ids is None or item.project_id in project_ids)
        )

    async def list(
        self,
        *,
        project_id: str | None = None,
        project_ids: Sequence[str] | None = None,
        binding_type: ProjectBindingType | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ProjectBindingDocument]:
        del cursor
        return Page(
            items=tuple(
                item
                for item in self.documents.values()
                if (project_id is None or item.project_id == project_id)
                and (
                    project_id is not None or project_ids is None or item.project_id in project_ids
                )
                and (binding_type is None or item.binding_type == binding_type)
            )[:limit]
        )

    async def update(
        self, document: ProjectBindingDocument, *, expected_revision: int
    ) -> ProjectBindingDocument:
        current = self.documents.get(document.binding_id)
        if current is None or current.revision != expected_revision:
            raise ConcurrencyConflictError("changed")
        stored = document.model_copy(update={"revision": expected_revision + 1})
        self.documents[stored.binding_id] = stored
        return stored

    async def delete(self, binding_id: str, *, expected_revision: int) -> bool:
        current = self.documents.get(binding_id)
        if current is None or current.revision != expected_revision:
            return False
        del self.documents[binding_id]
        return True


def service(*documents: ProjectBindingDocument) -> tuple[ProjectBindingService, Bindings]:
    repository = Bindings(documents)
    return ProjectBindingService(
        FakeProjectRepository([project(PAYMENTS), project(WALLET)]), repository
    ), repository


def test_repository_locator_normalizes_standard_ssh_and_https_forms() -> None:
    expected = "github.com/example/payment-service"

    assert normalize_repository_locator("git@github.com:example/payment-service.git") == expected
    assert (
        normalize_repository_locator("ssh://git@github.com/example/payment-service.git") == expected
    )
    assert (
        normalize_repository_locator("https://github.com/example/payment-service.git") == expected
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://user:secret@github.com/example/payment-service.git",
        "https://github.com/example/payment-service.git?token=secret",
        "github.com/example/payment-service?token=secret",
        "file:///srv/payment-service",
        "git@github.com:example/../payment-service.git",
    ],
)
def test_repository_locator_refuses_credentialed_or_local_or_unsafe_forms(value: str) -> None:
    with pytest.raises(ProjectBindingValueError):
        normalize_repository_locator(value)


@pytest.mark.asyncio
async def test_resolution_honours_priority_and_maps_ssh_and_https_to_one_binding() -> None:
    resolver, bindings = service()
    await resolver.create_binding(
        project_id=PAYMENTS,
        binding_type=ProjectBindingType.GIT_REMOTE,
        value="git@github.com:example/payment-service.git",
    )
    await resolver.create_binding(
        project_id=PAYMENTS,
        binding_type=ProjectBindingType.REPOSITORY_NAME,
        value="payment-service",
    )
    await resolver.create_binding(
        project_id=PAYMENTS,
        binding_type=ProjectBindingType.WORKSPACE_ALIAS,
        value="payments-local",
    )

    explicit = await resolver.resolve_project(
        project_id=PAYMENTS,
        git_remote="git@github.com:example/not-used.git",
    )
    ssh = await resolver.resolve_project(git_remote="git@github.com:example/payment-service.git")
    https = await resolver.resolve_project(
        repository_url="https://github.com/example/payment-service.git"
    )
    named = await resolver.resolve_project(repository_name="payment-service")
    workspace = await resolver.resolve_project(workspace_hint="payments-local")

    assert len(bindings.documents) == 3
    for outcome, matched_by in (
        (explicit, "explicit_project_id"),
        (ssh, "git_remote"),
        (https, "repository_url"),
        (named, "repository_name"),
        (workspace, "workspace_alias"),
    ):
        assert outcome.status == ProjectResolutionStatus.RESOLVED
        assert outcome.project_id == PAYMENTS
        assert outcome.confidence == 1.0
        assert outcome.matched_by == matched_by


@pytest.mark.asyncio
async def test_binding_creation_rejects_duplicates_and_cross_project_conflicts() -> None:
    resolver, _ = service()
    await resolver.create_binding(
        project_id=PAYMENTS,
        binding_type=ProjectBindingType.GIT_REMOTE,
        value="git@github.com:example/payment-service.git",
    )

    with pytest.raises(ProjectBindingDuplicateError):
        await resolver.create_binding(
            project_id=PAYMENTS,
            binding_type=ProjectBindingType.REPOSITORY_URL,
            value="https://github.com/example/payment-service.git",
        )
    with pytest.raises(ProjectBindingConflictError):
        await resolver.create_binding(
            project_id=WALLET,
            binding_type=ProjectBindingType.REPOSITORY_URL,
            value="https://github.com/example/payment-service.git",
        )


@pytest.mark.asyncio
async def test_ambiguity_is_returned_as_candidates_and_never_selected() -> None:
    # This represents a manually corrupted or pre-migration collection.  The
    # production uniqueness rules prevent creating it; the resolver still
    # must remain safe when it encounters one.
    resolver, _ = service(
        binding("payments-name", PAYMENTS, ProjectBindingType.REPOSITORY_NAME, "shared"),
        binding("wallet-name", WALLET, ProjectBindingType.REPOSITORY_NAME, "shared"),
    )

    outcome = await resolver.resolve_project(repository_name="shared")

    assert outcome.status == ProjectResolutionStatus.AMBIGUOUS
    assert outcome.project_id is None
    assert outcome.confidence == 0.0
    assert [candidate.project_id for candidate in outcome.candidates] == [PAYMENTS, WALLET]


@pytest.mark.asyncio
async def test_resolution_filters_unauthorized_projects_and_reports_no_match() -> None:
    resolver, _ = service(
        binding("payments-name", PAYMENTS, ProjectBindingType.REPOSITORY_NAME, "payment-service")
    )

    denied = await resolver.resolve_project(
        repository_name="payment-service", allowed_project_ids=(WALLET,)
    )
    unmatched = await resolver.resolve_project(repository_name="not-configured")

    assert denied.status == ProjectResolutionStatus.NO_MATCH
    assert denied.project_id is None
    assert unmatched.status == ProjectResolutionStatus.NO_MATCH
