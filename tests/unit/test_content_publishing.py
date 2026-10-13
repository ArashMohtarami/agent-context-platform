"""Focused Phase 9 explicit-publishing safety and provider tests."""

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_content_publishing_service, get_content_service
from app.api.routes.content import router
from app.content.errors import (
    ContentApprovalBlockedError,
    ContentPublishingStateError,
    PublisherDeliveryError,
    PublisherUnavailableError,
)
from app.content.publication import PublicationSafetyPolicy
from app.content.publishers import (
    ManualPublisher,
    PublicationReceipt,
    PublicationStatus,
    PublisherConnection,
    PublisherError,
    PublisherRegistry,
)
from app.models.content import ContentItemDocument, ContentPublicationProvider, ContentStatus
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.content_publishing_service import ContentPublishingService
from tests.api_doubles import authenticated_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROFILE = "personal-brand"
PROJECT = "payment-service"
SECRET = "token-super-secret-value"


def content_item(
    *,
    body: str = "A practical explanation of idempotent payment retries.",
    status: ContentStatus = ContentStatus.APPROVED,
    revision: int = 1,
) -> ContentItemDocument:
    """Build one approved stored item with project-owned provenance."""
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
    """In-memory content-service surface used by publishing tests."""

    def __init__(self, item: ContentItemDocument) -> None:
        self.item = item
        self.get_content = AsyncMock(side_effect=self._get)
        self.mark_content_published = AsyncMock(side_effect=self._mark_published)

    async def _get(self, profile_id: str, content_id: str) -> ContentItemDocument:
        assert (profile_id, content_id) == (PROFILE, self.item.content_id)
        return self.item

    async def _mark_published(
        self,
        profile_id: str,
        content_id: str,
        *,
        expected_revision: int,
        provider: ContentPublicationProvider,
        external_id: str | None,
        external_url: str | None,
        published_at: object,
        idempotency_key: str,
    ) -> ContentItemDocument:
        assert (profile_id, content_id) == (PROFILE, self.item.content_id)
        assert expected_revision == self.item.revision
        self.item = self.item.model_copy(
            update={
                "status": ContentStatus.PUBLISHED,
                "published_at": published_at,
                "external_url": external_url,
                "publication_provider": provider,
                "external_id": external_id,
                "publication_idempotency_key": idempotency_key,
                "revision": expected_revision + 1,
            }
        )
        return self.item


class CountingManualPublisher(ManualPublisher):
    """Manual publisher with a call count proving idempotent retries do not resend."""

    def __init__(self) -> None:
        self.calls = 0

    async def publish(
        self, item: ContentItemDocument, *, idempotency_key: str
    ) -> PublicationReceipt:
        self.calls += 1
        return await super().publish(item, idempotency_key=idempotency_key)


class FailingPublisher:
    """A credential-like provider failure that must stay redacted."""

    provider = ContentPublicationProvider.MANUAL
    supports_idempotency = True

    async def publish(
        self, item: ContentItemDocument, *, idempotency_key: str
    ) -> PublicationReceipt:
        _ = (item, idempotency_key)
        raise PublisherError(f"provider rejected credential {SECRET}")

    async def validate_connection(self) -> PublisherConnection:
        return PublisherConnection(provider=self.provider, ready=True)

    async def get_publication_status(
        self, *, external_id: str | None, idempotency_key: str
    ) -> PublicationStatus:
        return PublicationStatus(
            provider=self.provider,
            idempotency_key=idempotency_key,
            published=False,
            external_id=external_id,
        )


@pytest.mark.asyncio
async def test_manual_publisher_records_an_explicit_approved_publication() -> None:
    store = ContentStore(content_item(revision=4))
    manual = CountingManualPublisher()
    service = ContentPublishingService(
        store,
        policy=PublicationSafetyPolicy(),
        publishers=PublisherRegistry((manual,)),
    )

    outcome = await service.publish_content(
        PROFILE,
        "idempotency-post",
        expected_revision=4,
        provider=ContentPublicationProvider.MANUAL,
        idempotency_key=None,
    )

    assert outcome.already_published is False
    assert outcome.item.status is ContentStatus.PUBLISHED
    assert outcome.item.publication_provider is ContentPublicationProvider.MANUAL
    assert outcome.item.publication_idempotency_key == "publish:idempotency-post:1"
    assert outcome.receipt.external_url is None
    assert manual.calls == 1
    assert (await manual.validate_connection()).ready is True


@pytest.mark.asyncio
async def test_publishing_requires_approval_and_a_fresh_safe_review() -> None:
    draft = ContentPublishingService(
        ContentStore(content_item(status=ContentStatus.DRAFTING)),
        policy=PublicationSafetyPolicy(),
        publishers=PublisherRegistry((ManualPublisher(),)),
    )
    with pytest.raises(ContentPublishingStateError, match="approved"):
        await draft.publish_content(
            PROFILE,
            "idempotency-post",
            expected_revision=1,
            provider=ContentPublicationProvider.MANUAL,
            idempotency_key=None,
        )

    unsafe_store = ContentStore(content_item(body=f"api_key={SECRET}"))
    unsafe = ContentPublishingService(
        unsafe_store,
        policy=PublicationSafetyPolicy(),
        publishers=PublisherRegistry((ManualPublisher(),)),
    )
    with pytest.raises(ContentApprovalBlockedError, match="high-severity"):
        await unsafe.publish_content(
            PROFILE,
            "idempotency-post",
            expected_revision=1,
            provider=ContentPublicationProvider.MANUAL,
            idempotency_key=None,
        )
    unsafe_store.mark_content_published.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_idempotency_key_reuses_the_persisted_manual_receipt() -> None:
    store = ContentStore(content_item(revision=2))
    manual = CountingManualPublisher()
    service = ContentPublishingService(
        store,
        policy=PublicationSafetyPolicy(),
        publishers=PublisherRegistry((manual,)),
    )

    first = await service.publish_content(
        PROFILE,
        "idempotency-post",
        expected_revision=2,
        provider=ContentPublicationProvider.MANUAL,
        idempotency_key="manual-publish-1",
    )
    retry = await service.publish_content(
        PROFILE,
        "idempotency-post",
        expected_revision=3,
        provider=ContentPublicationProvider.MANUAL,
        idempotency_key="manual-publish-1",
    )

    assert first.already_published is False
    assert retry.already_published is True
    assert manual.calls == 1
    assert store.mark_content_published.await_count == 1


@pytest.mark.asyncio
async def test_provider_failures_do_not_log_or_return_credential_like_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = ContentPublishingService(
        ContentStore(content_item()),
        policy=PublicationSafetyPolicy(),
        publishers=PublisherRegistry((FailingPublisher(),)),
    )

    with caplog.at_level(logging.WARNING, logger="agent_context.content.publishing"):
        with pytest.raises(PublisherDeliveryError) as error:
            await service.publish_content(
                PROFILE,
                "idempotency-post",
                expected_revision=1,
                provider=ContentPublicationProvider.MANUAL,
                idempotency_key=None,
            )

    assert SECRET not in str(error.value)
    assert SECRET not in caplog.text


@pytest.mark.asyncio
async def test_unconfigured_optional_provider_fails_without_affecting_manual_support() -> None:
    service = ContentPublishingService(
        ContentStore(content_item()),
        policy=PublicationSafetyPolicy(),
        publishers=PublisherRegistry((ManualPublisher(),)),
    )

    with pytest.raises(PublisherUnavailableError, match="not configured"):
        await service.publish_content(
            PROFILE,
            "idempotency-post",
            expected_revision=1,
            provider=ContentPublicationProvider.GITHUB,
            idempotency_key=None,
        )


def application(
    store: ContentStore,
    audit: FakeAuditRepository,
) -> tuple[Any, str]:
    """Build the protected content router with only the Phase 9 services overridden."""
    record, token = issued_key(
        scopes=("content:read", "content:write", "content:publish"),
        profiles=(PROFILE,),
        projects=(PROJECT,),
    )
    app = authenticated_application(
        router,
        auth=AuthService(FakeApiKeyRepository(record)),
        audit=AuditService(audit),
    )

    async def content_service_dependency() -> ContentStore:
        return store

    async def publishing_service_dependency() -> ContentPublishingService:
        return ContentPublishingService(
            store,
            policy=PublicationSafetyPolicy(),
            publishers=PublisherRegistry((ManualPublisher(),)),
        )

    app.dependency_overrides[get_content_service] = content_service_dependency
    app.dependency_overrides[get_content_publishing_service] = publishing_service_dependency
    return app, token


@pytest.mark.asyncio
async def test_publish_api_is_explicit_auditable_and_rejects_arbitrary_body() -> None:
    store = ContentStore(content_item(revision=3))
    audit = FakeAuditRepository()
    app, token = application(store, audit)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v2/content/idempotency-post/publish",
            json={"profile_id": PROFILE, "expected_revision": 3},
            headers=bearer(token),
        )
        invalid = await client.post(
            "/api/v2/content/idempotency-post/publish",
            json={
                "profile_id": PROFILE,
                "expected_revision": 4,
                "body": "publish arbitrary text",
            },
            headers=bearer(token),
        )

    assert response.status_code == 200
    assert response.json()["content"]["status"] == "published"
    assert response.json()["provider"] == "manual"
    assert response.json()["already_published"] is False
    assert audit.actions()[-1] == ("content.publish", "allowed")
    assert invalid.status_code == 422
    assert store.mark_content_published.await_count == 1


async def test_manual_publisher_status_is_local_and_credential_free() -> None:
    """The default adapter has no network configuration or credentials to leak."""
    publisher = ManualPublisher()
    result = await publisher.get_publication_status(
        external_id=None,
        idempotency_key="manual-publish-1",
    )

    assert result.published is True
    assert result.external_id is None
