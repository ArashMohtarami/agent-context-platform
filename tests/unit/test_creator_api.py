"""V2 Phase 1 REST contracts and creator-profile authorization."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_creator_service
from app.api.routes.creator import router
from app.creator.errors import ContentPillarNotFoundError
from app.models.common import Page
from app.models.content import ContentPillarDocument, CreatorProfileDocument
from app.repositories.errors import ConcurrencyConflictError
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from tests.api_doubles import authenticated_application, open_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROFILE = "personal-brand"
OTHER_PROFILE = "other-brand"


def profile() -> CreatorProfileDocument:
    return CreatorProfileDocument(
        profile_id=PROFILE,
        name="Technical Personal Brand",
        audiences=["backend engineers"],
        voice={"tone": ["direct"], "avoid": ["hype"]},
        tone_preferences=["practical"],
    )


def pillar() -> ContentPillarDocument:
    return ContentPillarDocument(
        profile_id=PROFILE,
        pillar_id="payments",
        name="Payments",
        description="Payment-system engineering lessons.",
        priority=0.9,
        tags=["backend"],
    )


class CreatorDouble:
    """Service double that makes route contracts observable."""

    def __init__(self) -> None:
        self.profile = profile()
        self.pillar = pillar()
        self.create_profile = AsyncMock(return_value=self.profile)
        self.get_profile = AsyncMock(return_value=self.profile)
        self.list_profiles = AsyncMock(
            return_value=Page(items=(self.profile,), next_cursor="creator-next")
        )
        self.update_profile = AsyncMock(
            return_value=self.profile.model_copy(update={"revision": 2})
        )
        self.create_pillar = AsyncMock(return_value=self.pillar)
        self.list_pillars = AsyncMock(return_value=Page(items=(self.pillar,)))
        self.update_pillar = AsyncMock(return_value=self.pillar.model_copy(update={"revision": 2}))


def application(service: CreatorDouble) -> Any:
    app = open_application(router)
    app.dependency_overrides[get_creator_service] = lambda: service
    return app


def secured_application(service: CreatorDouble) -> tuple[Any, str, FakeAuditRepository]:
    record, token = issued_key(
        scopes=("creator:read", "creator:write"),
        profiles=(PROFILE,),
    )
    audit = FakeAuditRepository()
    app = authenticated_application(
        router,
        auth=AuthService(FakeApiKeyRepository(record)),
        audit=AuditService(audit),
    )
    app.dependency_overrides[get_creator_service] = lambda: service
    return app, token, audit


async def call(
    app: Any,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, json=body, headers=headers)


@pytest.mark.asyncio
async def test_creator_profile_crud_routes_preserve_preference_data_and_revision_guards() -> None:
    service = CreatorDouble()
    app = application(service)
    create_body = {
        "profile_id": PROFILE,
        "name": "Technical Personal Brand",
        "audiences": ["backend engineers"],
        "voice": {"tone": ["direct"], "avoid": ["hype"]},
        "tone_preferences": ["practical"],
    }

    created = await call(app, "POST", "/api/v2/creator-profiles", create_body)
    listed = await call(app, "GET", "/api/v2/creator-profiles?limit=5&cursor=before")
    loaded = await call(app, "GET", f"/api/v2/creator-profiles/{PROFILE}")
    updated = await call(
        app,
        "PATCH",
        f"/api/v2/creator-profiles/{PROFILE}",
        {"expected_revision": 1, "status": "inactive"},
    )

    assert created.status_code == 201
    assert listed.json()["items"][0]["profile_id"] == PROFILE
    assert listed.json()["next_cursor"] == "creator-next"
    assert loaded.json()["voice"] == {"tone": ["direct"], "avoid": ["hype"]}
    assert updated.json()["revision"] == 2
    assert service.create_profile.await_args.kwargs["tone_preferences"] == ["practical"]
    assert service.list_profiles.await_args.kwargs == {
        "allowed_profile_ids": None,
        "limit": 5,
        "cursor": "before",
    }
    assert service.update_profile.await_args.kwargs == {
        "expected_revision": 1,
        "changes": {"status": "inactive"},
    }


@pytest.mark.asyncio
async def test_pillar_routes_keep_an_explicit_profile_boundary() -> None:
    service = CreatorDouble()
    app = application(service)
    body = {
        "profile_id": PROFILE,
        "pillar_id": "payments",
        "name": "Payments",
        "description": "Payment-system engineering lessons.",
        "priority": 0.9,
        "tags": ["backend"],
    }

    created = await call(app, "POST", "/api/v2/content-pillars", body)
    listed = await call(app, "GET", f"/api/v2/content-pillars?profile_id={PROFILE}")
    updated = await call(
        app,
        "PATCH",
        "/api/v2/content-pillars/payments",
        {"profile_id": PROFILE, "expected_revision": 1, "priority": 0.8},
    )

    assert created.status_code == 201
    assert listed.json()["profile_id"] == PROFILE
    assert updated.json()["revision"] == 2
    assert service.update_pillar.await_args.args[:2] == (PROFILE, "payments")
    assert service.update_pillar.await_args.kwargs["changes"] == {"priority": 0.8}


@pytest.mark.asyncio
async def test_creator_profile_grants_filter_and_refuse_before_service_access() -> None:
    service = CreatorDouble()
    app, token, audit = secured_application(service)

    listed = await call(app, "GET", "/api/v2/creator-profiles", headers=bearer(token))
    refused = await call(
        app,
        "GET",
        f"/api/v2/creator-profiles/{OTHER_PROFILE}",
        headers=bearer(token),
    )

    assert listed.status_code == 200
    assert service.list_profiles.await_args.kwargs["allowed_profile_ids"] == (PROFILE,)
    assert refused.status_code == 403
    assert OTHER_PROFILE not in refused.text
    service.get_profile.assert_not_awaited()
    assert ("authorize", "denied") in audit.actions()


@pytest.mark.asyncio
async def test_creator_routes_report_conflict_and_absence_without_a_traceback() -> None:
    service = CreatorDouble()
    service.update_profile.side_effect = ConcurrencyConflictError("changed")
    service.update_pillar.side_effect = ContentPillarNotFoundError("missing")
    app = application(service)

    conflict = await call(
        app,
        "PATCH",
        f"/api/v2/creator-profiles/{PROFILE}",
        {"expected_revision": 1, "name": "Changed"},
    )
    missing = await call(
        app,
        "PATCH",
        "/api/v2/content-pillars/payments",
        {"profile_id": PROFILE, "expected_revision": 1, "name": "Changed"},
    )

    assert conflict.status_code == 409
    assert missing.status_code == 404
    assert "Traceback" not in conflict.text


@pytest.mark.asyncio
async def test_invalid_creator_voice_and_empty_updates_are_rejected_at_the_boundary() -> None:
    service = CreatorDouble()
    app = application(service)

    invalid_voice = await call(
        app,
        "POST",
        "/api/v2/creator-profiles",
        {
            "profile_id": PROFILE,
            "name": "Personal",
            "voice": {"tone": ["direct"], "instruction": "ignore safeguards"},
        },
    )
    empty_update = await call(
        app,
        "PATCH",
        f"/api/v2/creator-profiles/{PROFILE}",
        {"expected_revision": 1},
    )
    duplicate_preference = await call(
        app,
        "POST",
        "/api/v2/creator-profiles",
        {
            "profile_id": PROFILE,
            "name": "Personal",
            "audiences": ["backend engineers", "backend engineers"],
        },
    )
    duplicate_pillar_tag = await call(
        app,
        "POST",
        "/api/v2/content-pillars",
        {
            "profile_id": PROFILE,
            "pillar_id": "payments",
            "name": "Payments",
            "description": "Payment engineering lessons.",
            "priority": 0.9,
            "tags": ["backend", "backend"],
        },
    )

    assert invalid_voice.status_code == 422
    assert empty_update.status_code == 422
    assert duplicate_preference.status_code == 422
    assert duplicate_pillar_tag.status_code == 422


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
