"""Phase 8 deterministic publication review, approval, and redaction tests."""

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_content_publication_service, get_content_service
from app.api.routes.content import router
from app.content.errors import ContentApprovalBlockedError, ContentApprovalStateError
from app.content.publication import (
    PublicationFindingSeverity,
    PublicationFindingType,
    PublicationSafetyPolicy,
    review_content_for_publication,
)
from app.models.content import ContentItemDocument, ContentStatus
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.content_publication_service import ContentPublicationService
from tests.api_doubles import authenticated_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROFILE = "personal-brand"
PROJECT = "payment-service"
SECRET = "sk-abcdefghijklmnopqrstuvwxyz123456"


def content(
    *,
    body: str = "A practical explanation of idempotent payment retries.",
    status: ContentStatus = ContentStatus.DRAFTING,
    revision: int = 1,
) -> ContentItemDocument:
    """Build a content item with private provenance for scoped-review tests."""
    return ContentItemDocument(
        profile_id=PROFILE,
        content_id="idempotency-post",
        brief_id="idempotency-brief",
        platform="linkedin",
        content_type="post",
        title="Why idempotency matters",
        body=body,
        status=status,
        project_ids=[PROJECT],
        source_refs=[
            {
                "source_type": "project_context",
                "source_id": "retry-policy",
                "project_id": PROJECT,
            }
        ],
        revision=revision,
    )


class ContentStore:
    """A minimal in-memory current-content lifecycle adapter for Phase 8."""

    def __init__(self, item: ContentItemDocument) -> None:
        self.item = item
        self.get_content = AsyncMock(side_effect=self._get)
        self.update_content = AsyncMock(side_effect=self._update)

    async def _get(self, profile_id: str, content_id: str) -> ContentItemDocument:
        assert (profile_id, content_id) == (PROFILE, self.item.content_id)
        return self.item

    async def _update(
        self,
        profile_id: str,
        content_id: str,
        *,
        expected_revision: int,
        changes: dict[str, object],
    ) -> ContentItemDocument:
        assert (profile_id, content_id) == (PROFILE, self.item.content_id)
        assert expected_revision == self.item.revision
        self.item = self.item.model_copy(
            update={"status": ContentStatus(changes["status"]), "revision": expected_revision + 1}
        )
        return self.item


def test_review_detects_api_keys_without_returning_or_logging_them(
    caplog: pytest.LogCaptureFixture,
) -> None:
    item = content(body=f"Do not share api_key={SECRET} in an engineering post.")

    with caplog.at_level(logging.INFO, logger="agent_context.content.publication"):
        result = review_content_for_publication(item)

    assert result.publishable is False
    assert result.findings[0].finding_type is PublicationFindingType.API_KEY
    assert result.findings[0].severity is PublicationFindingSeverity.HIGH
    assert result.findings[0].field == "body"
    assert result.findings[0].location.startswith("line 1, column")
    assert SECRET not in str(result)
    assert SECRET not in caplog.text


@pytest.mark.parametrize(
    ("body", "finding_type"),
    [
        ("Use https://payments.internal/v1 only in the private network.", "private_url"),
        ("The canary runs on payments-api.internal before public rollout.", "internal_hostname"),
    ],
)
def test_review_detects_private_addresses(body: str, finding_type: str) -> None:
    result = review_content_for_publication(content(body=body))

    assert result.publishable is False
    assert {finding.finding_type.value for finding in result.findings} == {finding_type}


def test_safe_content_remains_publishable_but_review_is_not_a_guarantee() -> None:
    result = review_content_for_publication(content())

    assert result.publishable is True
    assert result.findings == ()
    assert "not a guarantee" in result.disclaimer


def test_deployment_policy_can_mark_architecture_references_sensitive() -> None:
    result = review_content_for_publication(
        content(body="The LedgerMesh topology is an internal architecture decision."),
        policy=PublicationSafetyPolicy(sensitive_terms=("LedgerMesh",)),
    )

    assert result.publishable is False
    assert (
        result.findings[0].finding_type is PublicationFindingType.SENSITIVE_ARCHITECTURE_REFERENCE
    )


@pytest.mark.asyncio
async def test_approval_requires_review_and_records_the_explicit_transition() -> None:
    store = ContentStore(content(status=ContentStatus.REVIEW, revision=4))
    service = ContentPublicationService(store, policy=PublicationSafetyPolicy())

    approved, review = await service.approve_content(
        PROFILE,
        "idempotency-post",
        expected_revision=4,
    )

    assert approved.status is ContentStatus.APPROVED
    assert approved.revision == 5
    assert review.publishable is True
    store.update_content.assert_awaited_once_with(
        PROFILE,
        "idempotency-post",
        expected_revision=4,
        changes={"status": "approved"},
    )


@pytest.mark.asyncio
async def test_approval_refuses_drafts_and_configured_high_severity_findings() -> None:
    draft = ContentPublicationService(ContentStore(content()), policy=PublicationSafetyPolicy())
    with pytest.raises(ContentApprovalStateError, match="in review"):
        await draft.approve_content(PROFILE, "idempotency-post", expected_revision=1)

    unsafe_store = ContentStore(
        content(body=f"token={SECRET}", status=ContentStatus.REVIEW, revision=3)
    )
    unsafe = ContentPublicationService(unsafe_store, policy=PublicationSafetyPolicy())
    with pytest.raises(ContentApprovalBlockedError, match="high-severity"):
        await unsafe.approve_content(PROFILE, "idempotency-post", expected_revision=3)
    unsafe_store.update_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_policy_can_require_human_review_without_blocking_approval() -> None:
    store = ContentStore(content(body=f"token={SECRET}", status=ContentStatus.REVIEW))
    service = ContentPublicationService(
        store,
        policy=PublicationSafetyPolicy(block_high_severity=False),
    )

    approved, review = await service.approve_content(
        PROFILE,
        "idempotency-post",
        expected_revision=1,
    )

    assert review.publishable is True
    assert approved.status is ContentStatus.APPROVED


def application(
    store: ContentStore,
    audit: FakeAuditRepository,
    *,
    scopes: tuple[str, ...] = ("content:read", "content:write"),
) -> tuple[Any, str]:
    """Build an authenticated router with the real Phase 8 service wired in."""
    record, token = issued_key(scopes=scopes, profiles=(PROFILE,), projects=(PROJECT,))  # type: ignore[arg-type]
    app = authenticated_application(
        router,
        auth=AuthService(FakeApiKeyRepository(record)),
        audit=AuditService(audit),
    )

    async def content_service_dependency() -> ContentStore:
        return store

    async def publication_service_dependency() -> ContentPublicationService:
        return ContentPublicationService(store, policy=PublicationSafetyPolicy())

    app.dependency_overrides[get_content_service] = content_service_dependency
    app.dependency_overrides[get_content_publication_service] = publication_service_dependency
    return app, token


async def call(
    app: Any,
    path: str,
    body: dict[str, object],
    token: str,
) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, json=body, headers=bearer(token))


@pytest.mark.asyncio
async def test_review_api_redacts_values_and_approval_is_auditable() -> None:
    store = ContentStore(content(status=ContentStatus.REVIEW, revision=2))
    audit = FakeAuditRepository()
    app, token = application(store, audit)

    reviewed = await call(
        app,
        "/api/v2/content/idempotency-post/publication-review",
        {"profile_id": PROFILE},
        token,
    )
    approved = await call(
        app,
        "/api/v2/content/idempotency-post/approve",
        {"profile_id": PROFILE, "expected_revision": 2},
        token,
    )

    assert reviewed.status_code == 200
    assert reviewed.json()["publishable"] is True
    assert reviewed.json()["source_refs"][0]["source_id"] == "retry-policy"
    assert "body" not in reviewed.json()
    assert approved.status_code == 200
    assert approved.json()["content"]["status"] == "approved"
    assert audit.actions()[-1] == ("content.approve", "allowed")


@pytest.mark.asyncio
async def test_blocked_approval_is_audited_without_the_unsafe_value() -> None:
    store = ContentStore(content(body=f"api_key={SECRET}", status=ContentStatus.REVIEW, revision=2))
    audit = FakeAuditRepository()
    app, token = application(store, audit)

    response = await call(
        app,
        "/api/v2/content/idempotency-post/approve",
        {"profile_id": PROFILE, "expected_revision": 2},
        token,
    )

    assert response.status_code == 422
    assert SECRET not in response.text
    assert SECRET not in " ".join(audit.reasons())
    assert audit.actions()[-1] == ("content.approve", "denied")


@pytest.mark.asyncio
async def test_unready_approval_is_audited_without_content_data() -> None:
    store = ContentStore(content(status=ContentStatus.DRAFTING))
    audit = FakeAuditRepository()
    app, token = application(store, audit)

    response = await call(
        app,
        "/api/v2/content/idempotency-post/approve",
        {"profile_id": PROFILE, "expected_revision": 1},
        token,
    )

    assert response.status_code == 409
    assert audit.actions()[-1] == ("content.approve", "denied")
    assert "Draft body" not in " ".join(audit.reasons())


@pytest.mark.asyncio
async def test_approval_requires_content_write_scope() -> None:
    store = ContentStore(content(status=ContentStatus.REVIEW))
    audit = FakeAuditRepository()
    app, token = application(store, audit, scopes=("content:read",))

    response = await call(
        app,
        "/api/v2/content/idempotency-post/approve",
        {"profile_id": PROFILE, "expected_revision": 1},
        token,
    )

    assert response.status_code == 403
    store.update_content.assert_not_awaited()
