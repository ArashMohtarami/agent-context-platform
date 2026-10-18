"""Studio transport contracts and lifecycle invalidation, without a new writer."""

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.api.routes.creator_console import router
from app.content.errors import InvalidContentLifecycleTransitionError
from app.creator.console.contracts import Draft, Export, Receipt, Version
from app.models.content import ContentStatus
from app.security.tenant import TenantContext, tenant_scope
from tests.unit.test_content_service import (
    PROFILE,
    ContentFakes,
    create_draft,
    create_idea_and_brief,
)


@pytest.fixture(autouse=True)
def tenant_context():
    with tenant_scope(TenantContext("org-a")):
        yield


def test_all_creator_routes_are_typed_session_bound_and_cannot_use_a_machine_principal():
    assert len(router.routes) >= 25
    for route in router.routes:
        assert isinstance(route, APIRoute) and route.response_model is not None
        gates = [d for d in route.dependant.dependencies if d.call.__name__ == "gate"]
        assert len(gates) == 1 and any(p.name == "org_id" for p in gates[0].path_params)
        assert any(d.call.__name__ == "current_browser" for d in gates[0].dependencies)
        assert not any("principal" in d.call.__name__ for d in route.dependant.dependencies)


@pytest.mark.parametrize(
    "values",
    [
        {"provider": "linkedin", "confirmed_manual_publication": True},
        {"confirmed_manual_publication": False},
        {},
    ],
)
def test_manual_receipts_require_explicit_confirmation_and_cannot_choose_network_provider(values):
    with pytest.raises(ValidationError):
        Receipt(profile_id=PROFILE, expected_revision=1, **values)


def test_browser_authorship_and_export_revision_cannot_be_supplied_or_omitted():
    with pytest.raises(ValidationError):
        Export(profile_id=PROFILE)
    with pytest.raises(ValidationError):
        Version(
            profile_id=PROFILE,
            expected_revision=1,
            body="draft",
            change_reason="edit",
            created_by="forged-key",
        )
    with pytest.raises(ValidationError):
        Draft(
            profile_id=PROFILE,
            content_id="draft",
            platform="linkedin",
            content_type="post",
            title="draft",
            body="text",
            created_by="forged-user",
        )


@pytest.mark.asyncio
async def test_metadata_edit_returns_approved_or_reviewed_copy_to_drafting_without_approval():
    store = ContentFakes()
    s = store.service()
    await create_idea_and_brief(s)
    item = await create_draft(s)
    for status in (ContentStatus.REVIEW, ContentStatus.APPROVED):
        current = store.items[PROFILE].records[item.content_id]
        store.items[PROFILE].records[item.content_id] = current.model_copy(
            update={"status": status}
        )
        row = await s.update_content(
            PROFILE,
            item.content_id,
            expected_revision=current.revision,
            changes={"title": "Edited " + status},
        )
        assert row.status == ContentStatus.DRAFTING
    with pytest.raises(InvalidContentLifecycleTransitionError, match="editing cannot"):
        await s.update_content(
            PROFILE,
            item.content_id,
            expected_revision=row.revision,
            changes={"title": "Approval bypass", "status": "approved"},
        )
